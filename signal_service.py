"""
TWS Trading Signal Service - Generiert Trading Signale und sendet Pushover Benachrichtigungen.
Kein automatisches Trading, nur Signal-Erkennung.
"""

import logging
import time
import signal
import sys
from datetime import datetime
from typing import Dict, List, Optional
import pandas as pd
import numpy as np

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract

import config
from pushover_notifier import PushoverNotifier
from database import DatabaseManager

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class SignalService(EWrapper, EClient):
    """
    Trading Signal Service - Nur Signal-Generierung, kein automatisches Trading.
    Sendet Benachrichtigungen via Pushover bei Entry/Exit Signalen.
    """
    
    def __init__(self, host: str = config.IB_HOST, port: int = config.IB_PORT, 
                 client_id: int = config.IB_CLIENT_ID):
        EClient.__init__(self, self)
        EWrapper.__init__(self)
        
        self.host = host
        self.port = port
        self.client_id = client_id
        
        self.db = DatabaseManager()
        self.notifier = PushoverNotifier()
        
        self.connected = False
        self.next_valid_order_id = None
        self.historical_data_cache: Dict[str, pd.DataFrame] = {}
        self.pending_requests: Dict[int, Dict] = {}
        self.request_id_counter = 0
        
        # Aktive Positionen (Tracking für Exit-Signale)
        self.active_positions: Dict[str, Dict] = {}
        
        # Watchlist direkt aus Config
        self.watchlist = config.WATCHLIST_STOCKS
        
        self.running = False
        
        logger.info(f"Signal Service initialisiert: {host}:{port} (Client ID: {client_id})")
        logger.info(f"Watchlist: {', '.join(self.watchlist)}")
    
    # ========================================================================
    # TWS CALLBACKS
    # ========================================================================
    
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        """Error Callback von TWS."""
        if errorCode in [2104, 2106, 2158]:  # Verbindungs-Infos
            logger.info(f"TWS Info [{errorCode}]: {errorString}")
        elif errorCode == 502:
            logger.error(f"❌ TWS nicht verbunden [{errorCode}]: {errorString}")
            self.connected = False
        else:
            logger.warning(f"TWS Error [{errorCode}] Req {reqId}: {errorString}")
    
    def nextValidId(self, orderId: int):
        """Callback: Next valid order ID."""
        self.next_valid_order_id = orderId
        self.connected = True
        logger.info(f"✓ TWS verbunden - Next Order ID: {orderId}")
    
    def historicalData(self, reqId: int, bar):
        """Callback: Historische Bar-Daten."""
        if reqId not in self.pending_requests:
            return
        
        symbol = self.pending_requests[reqId].get('symbol')
        
        if symbol not in self.historical_data_cache:
            self.historical_data_cache[symbol] = []
        
        self.historical_data_cache[symbol].append({
            'date': bar.date,
            'open': bar.open,
            'high': bar.high,
            'low': bar.low,
            'close': bar.close,
            'volume': bar.volume
        })
    
    def historicalDataEnd(self, reqId: int, start: str, end: str):
        """Callback: Ende der historischen Daten."""
        if reqId not in self.pending_requests:
            return
        
        symbol = self.pending_requests[reqId].get('symbol')
        
        if symbol in self.historical_data_cache:
            df = pd.DataFrame(self.historical_data_cache[symbol])
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
            
            self.historical_data_cache[symbol] = df
            self.db.save_historical_data(symbol, df)
            
            logger.info(f"✓ {symbol}: {len(df)} Bars geladen")
        
        self.pending_requests[reqId]['completed'] = True
    
    # ========================================================================
    # TWS VERBINDUNG
    # ========================================================================
    
    def connect_to_tws(self) -> bool:
        """Verbindet mit TWS."""
        try:
            logger.info(f"Verbinde mit TWS: {self.host}:{self.port}")
            self.connect(self.host, self.port, self.client_id)
            
            import threading
            api_thread = threading.Thread(target=self.run, daemon=True)
            api_thread.start()
            
            timeout = 10
            start_time = time.time()
            while not self.connected and (time.time() - start_time) < timeout:
                time.sleep(0.1)
            
            if self.connected:
                logger.info("✓ TWS Verbindung aktiv")
                return True
            else:
                logger.error("❌ TWS Verbindung fehlgeschlagen (Timeout)")
                return False
                
        except Exception as e:
            logger.error(f"❌ TWS Verbindungsfehler: {e}")
            return False
    
    def disconnect_from_tws(self):
        """Trennt TWS Verbindung."""
        if self.connected:
            self.disconnect()
            self.connected = False
            logger.info("✓ TWS Verbindung getrennt")
    
    # ========================================================================
    # DATEN ABRUFEN
    # ========================================================================
    
    def request_historical_data(self, symbol: str, days: int = 90) -> int:
        """
        Fordert historische Daten an.
        
        Args:
            symbol: Ticker Symbol
            days: Anzahl Tage
            
        Returns:
            Request ID
        """
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        
        req_id = self.request_id_counter
        self.request_id_counter += 1
        
        self.pending_requests[req_id] = {
            'symbol': symbol,
            'completed': False
        }
        
        self.historical_data_cache[symbol] = []
        
        end_date = ""
        duration = f"{days} D"
        bar_size = "1 day"
        what_to_show = "TRADES"
        use_rth = 1
        
        self.reqHistoricalData(req_id, contract, end_date, duration, 
                              bar_size, what_to_show, use_rth, 1, False, [])
        
        return req_id
    
    def wait_for_request(self, req_id: int, timeout: int = 30):
        """Wartet auf Abschluss einer Anfrage."""
        start_time = time.time()
        while req_id in self.pending_requests:
            if self.pending_requests[req_id].get('completed'):
                del self.pending_requests[req_id]
                break
            
            if (time.time() - start_time) > timeout:
                logger.warning(f"⚠️ Request {req_id} Timeout")
                break
            
            time.sleep(0.1)
    
    # ========================================================================
    # SIGNAL GENERIERUNG
    # ========================================================================
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Berechnet technische Indikatoren.
        
        Args:
            df: DataFrame mit OHLCV Daten
            
        Returns:
            DataFrame mit Indikatoren
        """
        df = df.copy()
        
        # Moving Averages
        df['ma_short'] = df['close'].rolling(window=config.MA_SHORT_PERIOD).mean()
        df['ma_long'] = df['close'].rolling(window=config.MA_LONG_PERIOD).mean()
        
        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=config.RSI_PERIOD).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=config.RSI_PERIOD).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # MACD
        if config.USE_MACD:
            exp1 = df['close'].ewm(span=config.MACD_FAST, adjust=False).mean()
            exp2 = df['close'].ewm(span=config.MACD_SLOW, adjust=False).mean()
            df['macd'] = exp1 - exp2
            df['macd_signal'] = df['macd'].ewm(span=config.MACD_SIGNAL, adjust=False).mean()
        
        return df
    
    def check_entry_signal(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        """
        Prüft Entry-Signal Bedingungen.
        
        Args:
            symbol: Ticker Symbol
            df: DataFrame mit Indikatoren
            
        Returns:
            Signal-Dict oder None
        """
        if len(df) < config.MA_LONG_PERIOD + 1:
            return None
        
        current = df.iloc[-1]
        previous = df.iloc[-2]
        
        signals = []
        reasons = []
        
        # MA Crossover
        if config.USE_MA_CROSSOVER:
            if (previous['ma_short'] <= previous['ma_long'] and 
                current['ma_short'] > current['ma_long']):
                signals.append(True)
                reasons.append("MA Crossover")
            else:
                signals.append(False)
        
        # RSI Oversold
        if config.USE_RSI:
            if current['rsi'] < config.RSI_OVERSOLD:
                signals.append(True)
                reasons.append(f"RSI {current['rsi']:.1f} < {config.RSI_OVERSOLD}")
            else:
                signals.append(False)
        
        # MACD Crossover
        if config.USE_MACD:
            if (previous['macd'] <= previous['macd_signal'] and
                current['macd'] > current['macd_signal']):
                signals.append(True)
                reasons.append("MACD Crossover")
            else:
                signals.append(False)
        
        # Mindestanzahl Signale
        signal_count = sum(signals)
        
        if signal_count >= config.MIN_SIGNALS_FOR_ENTRY:
            price = current['close']
            stop_loss = price * (1 - config.STOP_LOSS_PCT)
            take_profit = price * (1 + config.TAKE_PROFIT_PCT)
            
            # Position Size berechnen
            risk_amount = config.ACCOUNT_SIZE * config.MAX_RISK_PER_TRADE_PCT
            stop_distance = price - stop_loss
            quantity = int(risk_amount / stop_distance)
            
            if quantity * price < config.MIN_POSITION_SIZE:
                return None
            
            return {
                'type': 'ENTRY',
                'symbol': symbol,
                'price': price,
                'quantity': quantity,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'reason': " + ".join(reasons),
                'timestamp': datetime.now()
            }
        
        return None
    
    def check_exit_signal(self, symbol: str, position: Dict, df: pd.DataFrame) -> Optional[Dict]:
        """
        Prüft Exit-Signal Bedingungen für bestehende Position.
        
        Args:
            symbol: Ticker Symbol
            position: Position Dict
            df: DataFrame mit Indikatoren
            
        Returns:
            Signal-Dict oder None
        """
        if len(df) == 0:
            return None
        
        current = df.iloc[-1]
        current_price = current['close']
        entry_price = position['entry_price']
        
        # Stop Loss
        if current_price <= position['stop_loss']:
            pnl = (current_price - entry_price) * position['quantity']
            pnl_pct = ((current_price / entry_price) - 1) * 100
            
            return {
                'type': 'EXIT',
                'symbol': symbol,
                'price': current_price,
                'quantity': position['quantity'],
                'entry_price': entry_price,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'reason': '🛑 Stop Loss erreicht',
                'timestamp': datetime.now()
            }
        
        # Take Profit
        if current_price >= position['take_profit']:
            pnl = (current_price - entry_price) * position['quantity']
            pnl_pct = ((current_price / entry_price) - 1) * 100
            
            return {
                'type': 'EXIT',
                'symbol': symbol,
                'price': current_price,
                'quantity': position['quantity'],
                'entry_price': entry_price,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'reason': '🎯 Take Profit erreicht',
                'timestamp': datetime.now()
            }
        
        # RSI Overbought (für Long-Positionen)
        if config.USE_RSI and current['rsi'] > config.RSI_OVERBOUGHT:
            pnl = (current_price - entry_price) * position['quantity']
            pnl_pct = ((current_price / entry_price) - 1) * 100
            
            return {
                'type': 'EXIT',
                'symbol': symbol,
                'price': current_price,
                'quantity': position['quantity'],
                'entry_price': entry_price,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'reason': f'RSI Overbought ({current["rsi"]:.1f})',
                'timestamp': datetime.now()
            }
        
        return None
    
    def process_signal(self, signal: Dict):
        """
        Verarbeitet und sendet Trading Signal.
        
        Args:
            signal: Signal Dictionary
        """
        if signal['type'] == 'ENTRY':
            # Speichere Signal in DB
            self.db.save_signal(
                signal_type='ENTRY',
                symbol=signal['symbol'],
                price=signal['price'],
                quantity=signal['quantity'],
                reason=signal['reason']
            )
            
            # Sende Pushover Benachrichtigung
            self.notifier.send_entry_signal(
                symbol=signal['symbol'],
                price=signal['price'],
                quantity=signal['quantity'],
                reason=signal['reason'],
                stop_loss=signal['stop_loss'],
                take_profit=signal['take_profit']
            )
            
            # Merke Position für Exit-Tracking
            self.active_positions[signal['symbol']] = {
                'entry_price': signal['price'],
                'quantity': signal['quantity'],
                'stop_loss': signal['stop_loss'],
                'take_profit': signal['take_profit'],
                'entry_time': signal['timestamp']
            }
            
            logger.info(f"🟢 ENTRY Signal: {signal['symbol']} @ ${signal['price']:.2f}")
        
        elif signal['type'] == 'EXIT':
            # Speichere Signal in DB
            self.db.save_signal(
                signal_type='EXIT',
                symbol=signal['symbol'],
                price=signal['price'],
                quantity=signal['quantity'],
                reason=signal['reason'],
                pnl=signal.get('pnl', 0)
            )
            
            # Sende Pushover Benachrichtigung
            self.notifier.send_exit_signal(
                symbol=signal['symbol'],
                price=signal['price'],
                quantity=signal['quantity'],
                entry_price=signal['entry_price'],
                pnl=signal['pnl'],
                pnl_pct=signal['pnl_pct'],
                reason=signal['reason']
            )
            
            # Entferne Position aus Tracking
            if signal['symbol'] in self.active_positions:
                del self.active_positions[signal['symbol']]
            
            icon = "🟢" if signal['pnl'] > 0 else "🔴"
            logger.info(f"{icon} EXIT Signal: {signal['symbol']} @ ${signal['price']:.2f} | "
                       f"P&L: ${signal['pnl']:+.2f} ({signal['pnl_pct']:+.2f}%)")
    
    # ========================================================================
    # HAUPT-SCANNER
    # ========================================================================
    
    def scan_for_signals(self):
        """Scannt Watchlist nach Trading Signalen."""
        logger.info("\n" + "="*70)
        logger.info(f"  SIGNAL SCAN - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("="*70)
        
        for symbol in self.watchlist:
            try:
                # Lade historische Daten
                if symbol not in self.historical_data_cache or len(self.historical_data_cache[symbol]) == 0:
                    logger.info(f"Lade Daten für {symbol}...")
                    req_id = self.request_historical_data(symbol, config.HISTORY_DAYS)
                    self.wait_for_request(req_id, timeout=30)
                
                if symbol not in self.historical_data_cache:
                    logger.warning(f"⚠️ {symbol}: Keine Daten verfügbar")
                    continue
                
                df = self.historical_data_cache[symbol]
                
                if len(df) == 0:
                    continue
                
                # Berechne Indikatoren
                df = self.calculate_indicators(df)
                self.historical_data_cache[symbol] = df
                
                current_price = df.iloc[-1]['close']
                
                # Prüfe Exit-Signale für aktive Positionen
                if symbol in self.active_positions:
                    exit_signal = self.check_exit_signal(symbol, self.active_positions[symbol], df)
                    if exit_signal:
                        self.process_signal(exit_signal)
                        continue
                    
                    # Position Status
                    position = self.active_positions[symbol]
                    entry_price = position['entry_price']
                    pnl_pct = ((current_price / entry_price) - 1) * 100
                    logger.info(f"📊 {symbol}: ${current_price:.2f} | "
                              f"Position: {pnl_pct:+.2f}% | "
                              f"SL: ${position['stop_loss']:.2f} | "
                              f"TP: ${position['take_profit']:.2f}")
                else:
                    # Prüfe Entry-Signale (nur wenn keine Position)
                    entry_signal = self.check_entry_signal(symbol, df)
                    if entry_signal:
                        self.process_signal(entry_signal)
                    else:
                        # Status ohne Signal
                        rsi = df.iloc[-1]['rsi']
                        ma_short = df.iloc[-1]['ma_short']
                        ma_long = df.iloc[-1]['ma_long']
                        logger.info(f"📊 {symbol}: ${current_price:.2f} | "
                                  f"RSI: {rsi:.1f} | "
                                  f"MA: {ma_short:.2f}/{ma_long:.2f}")
                
            except Exception as e:
                logger.error(f"❌ Fehler bei {symbol}: {e}", exc_info=True)
        
        logger.info(f"\nAktive Positionen: {len(self.active_positions)}")
        logger.info(f"Nächster Scan in {config.SCAN_INTERVAL}s")
    
    # ========================================================================
    # HAUPTSCHLEIFE
    # ========================================================================
    
    def run_service(self):
        """Startet den Signal Service."""
        self.running = True
        
        logger.info("\n" + "="*70)
        logger.info("  SIGNAL SERVICE GESTARTET")
        logger.info("="*70)
        logger.info(f"Watchlist: {', '.join(self.watchlist)}")
        logger.info(f"Scan-Intervall: {config.SCAN_INTERVAL}s")
        logger.info(f"Signal-Only Mode: {'Ja' if config.SIGNAL_ONLY_MODE else 'Nein'}")
        logger.info("="*70 + "\n")
        
        # Initial Scan
        self.scan_for_signals()
        
        # Hauptschleife
        while self.running:
            try:
                time.sleep(config.SCAN_INTERVAL)
                self.scan_for_signals()
                
            except KeyboardInterrupt:
                logger.info("\n⚠️ Shutdown Signal empfangen...")
                break
            except Exception as e:
                logger.error(f"❌ Fehler im Scanner: {e}", exc_info=True)
                time.sleep(60)
    
    def stop_service(self):
        """Stoppt den Service."""
        self.running = False
        self.disconnect_from_tws()
        self.db.close()
        logger.info("✓ Service gestoppt")


def signal_handler(sig, frame):
    """Signal Handler für sauberes Beenden."""
    print("\n\n🛑 Beende Service...")
    if service_instance:
        service_instance.stop_service()
    sys.exit(0)


service_instance = None


def main():
    """Hauptfunktion."""
    global service_instance
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("\n" + "="*70)
    print("  TWS TRADING SIGNAL SERVICE")
    print("="*70)
    print(f"  TWS: {config.IB_HOST}:{config.IB_PORT}")
    print(f"  Modus: {'PAPER' if config.IS_PAPER_TRADING else 'LIVE'}")
    print(f"  Pushover: {'Aktiv' if config.PUSHOVER_USER_KEY else 'Inaktiv'}")
    print("="*70 + "\n")
    
    try:
        service = SignalService()
        service_instance = service
        
        # Test Pushover
        if config.PUSHOVER_USER_KEY:
            logger.info("Teste Pushover Verbindung...")
            service.notifier.test_notification()
        
        # Verbinde TWS
        if not service.connect_to_tws():
            logger.error("❌ TWS Verbindung fehlgeschlagen!")
            logger.error("Stelle sicher, dass TWS läuft und API aktiviert ist.")
            return
        
        # Starte Service
        service.run_service()
        
    except Exception as e:
        logger.error(f"❌ Kritischer Fehler: {e}", exc_info=True)
    finally:
        if service_instance:
            service_instance.stop_service()


if __name__ == "__main__":
    main()
