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

from tws_bot.config.settings import (
    WATCHLIST_STOCKS, SCAN_INTERVAL, HISTORY_DAYS, DATA_MAX_AGE_DAYS,
    LOG_LEVEL, LOG_FILE, SIGNAL_ONLY_MODE, DRY_RUN,
    MIN_MARKET_CAP, MIN_AVG_VOLUME, PUT_PE_RATIO_MULTIPLIER, PUT_MIN_IV_RANK,
    CALL_MIN_FCF_YIELD, CALL_MAX_IV_RANK, SPREAD_PE_RATIO_MULTIPLIER, SPREAD_MIN_IV_RANK,
    IB_HOST, IB_PORT, IS_PAPER_TRADING, PUSHOVER_USER_KEY
)
from tws_bot.notifications.pushover import PushoverNotifier
from tws_bot.data.database import DatabaseManager
from tws_bot.core.signals import check_entry_signal, check_exit_signal
from tws_bot.core.indicators import calculate_indicators
from tws_bot.core.indicators import calculate_indicators
from tws_bot.api.tws_connector import TWSConnector

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class SignalService(TWSConnector):
    def check_long_put_filters(self, symbol: str) -> dict:
        """Prüft alle LONG PUT Filter für ein Symbol."""
        result = {}
        # Fundamentaldaten
        fund = self.db.get_fundamental_data(symbol)
        result['pe_ratio'] = bool(fund and fund.get('pe_ratio') is not None)
        try:
            market_cap_val = float(fund.get('market_cap', 0)) if fund else 0.0
        except Exception:
            market_cap_val = 0.0
        try:
            avg_vol_val = float(fund.get('avg_volume', 0)) if fund else 0.0
        except Exception:
            avg_vol_val = 0.0
        result['market_cap'] = market_cap_val >= MIN_MARKET_CAP
        result['avg_volume'] = avg_vol_val >= MIN_AVG_VOLUME
        # PE Ratio Branchenvergleich (Dummy: vergleiche numerisch)
        try:
            pe_val = float(fund.get('pe_ratio', 0)) if fund else 0.0
        except Exception:
            pe_val = 0.0
        result['pe_ratio_mult'] = pe_val > PUT_PE_RATIO_MULTIPLIER * 10  # TODO: Branchen-Median
        # IV Rank (echt)
        iv_df = self.db.get_iv_history(symbol, days=252)
        iv_rank = None
        if not iv_df.empty and 'implied_vol' in iv_df.columns:
            current_iv = iv_df['implied_vol'].iloc[-1]
            iv_min = iv_df['implied_vol'].min()
            iv_max = iv_df['implied_vol'].max()
            if iv_max > iv_min:
                iv_rank = 100 * (current_iv - iv_min) / (iv_max - iv_min)
        result['iv_rank'] = iv_rank is not None and iv_rank >= PUT_MIN_IV_RANK
        # DTE (Dummy: immer True, da keine Optionsdaten)
        result['dte'] = True  # TODO: DTE aus options_positions
        # Nähe zum Hoch (Dummy: immer True, da keine Kursdaten)
        result['proximity_high'] = True  # TODO: aus historical_data
        return result

    def check_long_call_filters(self, symbol: str) -> dict:
        """Prüft alle LONG CALL Filter für ein Symbol."""
        result = {}
        fund = self.db.get_fundamental_data(symbol)
        try:
            fcf_val = float(fund.get('fcf', 0)) if fund and fund.get('fcf') is not None else 0.0
        except Exception:
            fcf_val = 0.0
        result['fcf_yield'] = fcf_val > CALL_MIN_FCF_YIELD
        try:
            market_cap_val = float(fund.get('market_cap', 0)) if fund else 0.0
        except Exception:
            market_cap_val = 0.0
        try:
            avg_vol_val = float(fund.get('avg_volume', 0)) if fund else 0.0
        except Exception:
            avg_vol_val = 0.0
        result['market_cap'] = market_cap_val >= MIN_MARKET_CAP
        result['avg_volume'] = avg_vol_val >= MIN_AVG_VOLUME
        # IV Rank (echt)
        iv_df = self.db.get_iv_history(symbol, days=252)
        iv_rank = None
        if not iv_df.empty and 'implied_vol' in iv_df.columns:
            current_iv = iv_df['implied_vol'].iloc[-1]
            iv_min = iv_df['implied_vol'].min()
            iv_max = iv_df['implied_vol'].max()
            if iv_max > iv_min:
                iv_rank = 100 * (current_iv - iv_min) / (iv_max - iv_min)
        result['iv_rank'] = iv_rank is not None and iv_rank <= CALL_MAX_IV_RANK
        # DTE (Dummy)
        result['dte'] = True  # TODO
        # Nähe zum Tief (Dummy)
        result['proximity_low'] = True  # TODO
        # Delta (Dummy)
        result['delta'] = True  # TODO
        return result

    def check_bear_call_spread_filters(self, symbol: str) -> dict:
        """Prüft alle BEAR CALL SPREAD Filter für ein Symbol."""
        result = {}
        fund = self.db.get_fundamental_data(symbol)
        result['pe_ratio'] = bool(fund and fund.get('pe_ratio') is not None)
        try:
            market_cap_val = float(fund.get('market_cap', 0)) if fund else 0.0
        except Exception:
            market_cap_val = 0.0
        try:
            avg_vol_val = float(fund.get('avg_volume', 0)) if fund else 0.0
        except Exception:
            avg_vol_val = 0.0
        result['market_cap'] = market_cap_val >= MIN_MARKET_CAP
        result['avg_volume'] = avg_vol_val >= MIN_AVG_VOLUME
        # PE Ratio Branchenvergleich (Dummy)
        try:
            pe_val = float(fund.get('pe_ratio', 0)) if fund else 0.0
        except Exception:
            pe_val = 0.0
        result['pe_ratio_mult'] = pe_val > SPREAD_PE_RATIO_MULTIPLIER * 10  # TODO
        # IV Rank (echt)
        iv_df = self.db.get_iv_history(symbol, days=252)
        iv_rank = None
        if not iv_df.empty and 'implied_vol' in iv_df.columns:
            current_iv = iv_df['implied_vol'].iloc[-1]
            iv_min = iv_df['implied_vol'].min()
            iv_max = iv_df['implied_vol'].max()
            if iv_max > iv_min:
                iv_rank = 100 * (current_iv - iv_min) / (iv_max - iv_min)
        result['iv_rank'] = iv_rank is not None and iv_rank >= SPREAD_MIN_IV_RANK
        # DTE (Dummy)
        result['dte'] = True  # TODO
        # Delta (Dummy)
        result['delta'] = True  # TODO
        # Strike Width (Dummy)
        result['strike_width'] = True  # TODO
        return result

    def scan_strategy_filters(self):
        """Scannt alle Symbole nach Strategie-Filtererfüllung und loggt Statistik."""
        stats = {'long_put': [], 'long_call': [], 'bear_call_spread': []}
        for symbol in self.watchlist:
            put = self.check_long_put_filters(symbol)
            call = self.check_long_call_filters(symbol)
            spread = self.check_bear_call_spread_filters(symbol)
            stats['long_put'].append(sum(put.values()) / len(put))
            stats['long_call'].append(sum(call.values()) / len(call))
            stats['bear_call_spread'].append(sum(spread.values()) / len(spread))
            logger.info(f"[FILTER] {symbol}: LONG PUT {sum(put.values())}/{len(put)} | LONG CALL {sum(call.values())}/{len(call)} | BEAR CALL SPREAD {sum(spread.values())}/{len(spread)}")
        # Statistik
        for strat, values in stats.items():
            total = len(values)
            pct_100 = sum(1 for v in values if v == 1.0) / total * 100
            pct_80 = sum(1 for v in values if v >= 0.8) / total * 100
            pct_70 = sum(1 for v in values if v >= 0.7) / total * 100
            logger.info(f"[STAT] {strat}: 100%={pct_100:.1f}% | >=80%={pct_80:.1f}% | >=70%={pct_70:.1f}%")

    def fundamentalData(self, reqId: int, data: str):
        """Callback: Fundamentale Daten (XML)."""
        if reqId not in self.pending_requests:
            return
        request_data = self.pending_requests[reqId]
        symbol = request_data.get('symbol')
        # Parse XML für P/E, FCF, Market Cap
        import xml.etree.ElementTree as ET
        fundamental = {
            'pe_ratio': None,
            'fcf': None,
            'market_cap': None,
            'sector': None,
            'avg_volume': None
        }
        try:
            root = ET.fromstring(data)
            pe_elem = root.find(".//Ratio[@FieldName='PEEXCLXOR']")
            if pe_elem is not None and pe_elem.text:
                fundamental['pe_ratio'] = float(pe_elem.text)
            mktcap_elem = root.find(".//Ratio[@FieldName='MKTCAP']")
            if mktcap_elem is not None and mktcap_elem.text:
                fundamental['market_cap'] = float(mktcap_elem.text) * 1_000_000
            cfshr_elem = root.find(".//Ratio[@FieldName='TTMCFSHR']")
            shares_elem = root.find(".//SharesOut")
            if cfshr_elem is not None and shares_elem is not None:
                try:
                    cf_per_share = float(cfshr_elem.text)
                    shares_out = float(shares_elem.text)
                    fundamental['fcf'] = cf_per_share * shares_out
                except (ValueError, AttributeError):
                    pass
            sector_elem = root.find(".//Industry[@type='TRBC']")
            if sector_elem is not None and sector_elem.text:
                fundamental['sector'] = sector_elem.text.strip()
            avgvol_elem = root.find(".//Ratio[@FieldName='VOL10DAVG']")
            if avgvol_elem is not None and avgvol_elem.text:
                fundamental['avg_volume'] = float(avgvol_elem.text) * 1_000_000
        except Exception as e:
            logger.error(f"[FEHLER] Fundamental-Parsing: {e}", exc_info=True)
        self.db.save_fundamental_data(symbol, fundamental)
        logger.info(f"[OK] {symbol}: Fundamentaldaten geladen")
        self.pending_requests[reqId]['completed'] = True
    
    def __init__(self):
        TWSConnector.__init__(self)
        
        self.db = DatabaseManager()
        self.notifier = PushoverNotifier()
        
        self.historical_data_cache: Dict[str, pd.DataFrame] = {}
        
        # Aktive Positionen (Tracking für Exit-Signale)
        self.active_positions: Dict[str, Dict] = {}
        
        # Watchlist direkt aus Config
        self.watchlist = WATCHLIST_STOCKS
        
        self.running = False
        
        logger.info(f"Signal Service initialisiert: {self.host}:{self.port} (Client ID: {self.client_id})")
        logger.info(f"Watchlist: {', '.join(self.watchlist)}")

        # Monitoring-Metriken
        self.metrics = {
            'start_time': datetime.now(),
            'scans_completed': 0,
            'signals_generated': 0,
            'errors_encountered': 0,
            'last_health_check': None,
            'connection_drops': 0
        }
    
    # ========================================================================
    # TWS CALLBACKS
    # ========================================================================
    
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        """Error Callback von TWS."""
        if errorCode in [2104, 2106, 2158]:  # Verbindungs-Infos
            logger.info(f"TWS Info [{errorCode}]: {errorString}")
        elif errorCode == 502:
            logger.error(f"[FEHLER] TWS nicht verbunden [{errorCode}]: {errorString}")
            self.connected = False
        else:
            logger.warning(f"TWS Error [{errorCode}] Req {reqId}: {errorString}")
    
    def nextValidId(self, orderId: int):
        """Callback: Next valid order ID."""
        self.next_valid_order_id = orderId
        self.connected = True
        logger.info(f"[OK] TWS verbunden - Next Order ID: {orderId}")
    
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
            
            logger.info(f"[OK] {symbol}: {len(df)} Bars geladen")
        
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
                logger.info("[OK] TWS Verbindung aktiv")
                return True
            else:
                logger.error("[FEHLER] TWS Verbindung fehlgeschlagen (Timeout)")
                return False
                
        except Exception as e:
            logger.error(f"[FEHLER] TWS Verbindungsfehler: {e}")
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
                logger.warning(f"[WARNUNG] Request {req_id} Timeout")
                break
            
            time.sleep(0.1)
    
    # ========================================================================
    # SIGNAL GENERIERUNG
    # ========================================================================
    
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
            
            logger.info(f"[ENTRY] Entry Signal: {signal['symbol']} @ ${signal['price']:.2f}")
        
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
            
            icon = "[GEWINN]" if signal['pnl'] > 0 else "[VERLUST]"
            logger.info(f"{icon} EXIT Signal: {signal['symbol']} @ ${signal['price']:.2f} | "
                       f"P&L: ${signal['pnl']:+.2f} ({signal['pnl_pct']:+.2f}%)")
    
    # ========================================================================
    # HAUPT-SCANNER
    # ========================================================================
    
    def request_fundamental_data(self, symbol: str) -> int:
        """Fordert Fundamentaldaten von TWS an."""
        req_id = self.request_id_counter
        self.request_id_counter += 1
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        self.pending_requests[req_id] = {
            'type': 'fundamental',
            'symbol': symbol,
            'completed': False
        }
        self.reqFundamentalData(req_id, contract, "ReportSnapshot", [])
        logger.info(f"Lade Fundamentaldaten für {symbol}...")
        return req_id

    def scan_for_signals(self):
        """Scannt Watchlist nach Trading Signalen."""
        logger.info("\n" + "="*70)
        logger.info(f"  SIGNAL SCAN - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("="*70)
        
        # Portfolio-Daten für Risiko-Management abrufen
        try:
            portfolio_data = self.get_portfolio_data()
            logger.info(f"[PORTFOLIO] Cushion: {portfolio_data.get('cushion', 0):.1%} | "
                       f"Buying Power: ${portfolio_data.get('buying_power', 0):,.0f} | "
                       f"Positionen: {portfolio_data.get('num_positions', 0)}")
        except Exception as e:
            logger.warning(f"[PORTFOLIO] Fehler beim Abrufen der Portfolio-Daten: {e}")
            portfolio_data = {}
        
        for symbol in self.watchlist:
            try:
                # --- Fundamentaldaten prüfen/laden ---
                fund_data = self.db.get_fundamental_data(symbol, max_age_days=30)
                if not fund_data:
                    req_id_fund = self.request_fundamental_data(symbol)
                    self.wait_for_request(req_id_fund, timeout=30)
                else:
                    logger.info(f"[CACHE] Fundamentaldaten für {symbol} aus DB geladen.")

                # --- Historische Daten prüfen/laden ---
                if symbol not in self.historical_data_cache:
                    # Versuche aus DB zu laden
                    df_hist = self.db.load_historical_data(symbol, days=HISTORY_DAYS)
                    if not df_hist.empty:
                        self.historical_data_cache[symbol] = df_hist
                        logger.info(f"[CACHE] Historische Daten für {symbol} aus DB geladen.")

                # Prüfe Aktualität
                needs_update = self.db.needs_update(symbol, max_age_days=1)
                if symbol not in self.historical_data_cache or needs_update:
                    logger.info(f"Lade neue historische Daten für {symbol}...")
                    req_id = self.request_historical_data(symbol, HISTORY_DAYS)
                    self.wait_for_request(req_id, timeout=30)

                if symbol not in self.historical_data_cache:
                    logger.warning(f"[WARNUNG] {symbol}: Keine Daten verfügbar")
                    continue

                df = self.historical_data_cache[symbol]
                if len(df) == 0:
                    continue

                # Berechne Indikatoren
                df = calculate_indicators(df)
                self.historical_data_cache[symbol] = df

                current_price = df.iloc[-1]['close']

                # Prüfe Exit-Signale für aktive Positionen
                if symbol in self.active_positions:
                    exit_signal = check_exit_signal(symbol, df, self.active_positions[symbol])
                    if exit_signal:
                        self.process_signal(exit_signal)
                        self.metrics['signals_generated'] += 1
                        continue

                    # Position Status
                    position = self.active_positions[symbol]
                    entry_price = position['entry_price']
                    pnl_pct = ((current_price / entry_price) - 1) * 100
                    logger.info(f"[POS] {symbol}: ${current_price:.2f} | "
                                f"Position: {pnl_pct:+.2f}% | "
                                f"SL: ${position['stop_loss']:.2f} | "
                                f"TP: ${position['take_profit']:.2f}")
                else:
                    # Prüfe Entry-Signale (nur wenn keine Position)
                    entry_signal = check_entry_signal(symbol, df, self, portfolio_data)
                    if entry_signal:
                        self.process_signal(entry_signal)
                        self.metrics['signals_generated'] += 1
                    else:
                        # Status ohne Signal
                        rsi = df.iloc[-1]['rsi']
                        ma_short = df.iloc[-1]['ma_short']
                        ma_long = df.iloc[-1]['ma_long']
                        logger.info(f"[SCAN] {symbol}: ${current_price:.2f} | "
                                    f"RSI: {rsi:.1f} | "
                                    f"MA: {ma_short:.2f}/{ma_long:.2f}")

            except Exception as e:
                logger.error(f"[FEHLER] Fehler bei {symbol}: {e}", exc_info=True)

        logger.info(f"\nAktive Positionen: {len(self.active_positions)}")
        logger.info(f"\nNaechster Scan in {SCAN_INTERVAL}s")
    
    # ========================================================================
    # HAUPTSCHLEIFE
    # ========================================================================

    def perform_health_check(self) -> dict:
        """
        Führt umfassenden Health-Check durch.

        Returns:
            Dictionary mit Health-Status
        """
        health = {
            'timestamp': datetime.now(),
            'overall_status': 'unknown',
            'checks': {}
        }

        try:
            # TWS-Verbindung prüfen
            health['checks']['tws_connection'] = {
                'status': 'healthy' if self.connected else 'unhealthy',
                'details': f"Connected: {self.connected}"
            }

            # Datenbank prüfen
            db_health = self.db.health_check()
            health['checks']['database'] = {
                'status': db_health['status'],
                'details': f"Connection: {db_health['connection']}, Tables: {db_health['tables_exist']}"
            }

            # Cache prüfen
            cache_size = len(self.historical_data_cache)
            health['checks']['cache'] = {
                'status': 'healthy' if cache_size > 0 else 'warning',
                'details': f"Symbols cached: {cache_size}"
            }

            # Positionen prüfen
            positions_count = len(self.active_positions)
            health['checks']['positions'] = {
                'status': 'healthy',
                'details': f"Active positions: {positions_count}"
            }

            # Metriken prüfen
            uptime = (datetime.now() - self.metrics['start_time']).total_seconds()
            health['checks']['metrics'] = {
                'status': 'healthy',
                'details': f"Uptime: {uptime:.0f}s, Scans: {self.metrics['scans_completed']}, Signals: {self.metrics['signals_generated']}"
            }

            # Gesamtstatus bestimmen
            statuses = [check['status'] for check in health['checks'].values()]
            if all(status == 'healthy' for status in statuses):
                health['overall_status'] = 'healthy'
            elif 'unhealthy' in statuses:
                health['overall_status'] = 'unhealthy'
            else:
                health['overall_status'] = 'degraded'

            self.metrics['last_health_check'] = health

        except Exception as e:
            logger.error(f"[HEALTH] Health-Check Fehler: {e}")
            health['overall_status'] = 'error'
            health['error'] = str(e)

        return health

    def log_health_status(self):
        """Loggt aktuellen Health-Status."""
        health = self.perform_health_check()

        status_icon = {
            'healthy': '🟢',
            'degraded': '🟡',
            'unhealthy': '🔴',
            'error': '❌',
            'unknown': '❓'
        }.get(health['overall_status'], '❓')

        logger.info(f"[HEALTH] {status_icon} System Status: {health['overall_status'].upper()}")

        for check_name, check_data in health['checks'].items():
            icon = {'healthy': '✅', 'warning': '⚠️', 'unhealthy': '❌'}.get(check_data['status'], '❓')
            logger.info(f"[HEALTH] {icon} {check_name}: {check_data['details']}")

    def run_service(self):
        """Startet den Signal Service mit robuster Fehlerbehandlung."""
        self.running = True
        consecutive_errors = 0
        max_consecutive_errors = 5
        error_backoff_time = 60  # Start mit 60 Sekunden

        logger.info("\n" + "="*70)
        logger.info("  SIGNAL SERVICE GESTARTET")
        logger.info("="*70)
        logger.info(f"Watchlist: {', '.join(self.watchlist)}")
        logger.info(f"Scan-Intervall: {SCAN_INTERVAL}s")
        logger.info(f"Signal-Only Mode: {'Ja' if SIGNAL_ONLY_MODE else 'Nein'}")
        logger.info("="*70 + "\n")

        # Initial Scan
        try:
            self.scan_for_signals()
            self.metrics['scans_completed'] += 1
            consecutive_errors = 0  # Reset bei erfolgreichem Scan
        except Exception as e:
            logger.error(f"[FEHLER] Initialer Scan fehlgeschlagen: {e}", exc_info=True)
            self.metrics['errors_encountered'] += 1
            consecutive_errors += 1

        # Health-Check Timer
        last_health_check = time.time()
        health_check_interval = 300  # Alle 5 Minuten

        # Hauptschleife mit robuster Fehlerbehandlung
        while self.running:
            try:
                # Regelmäßiger Health-Check
                current_time = time.time()
                if current_time - last_health_check > health_check_interval:
                    self.log_health_status()
                    last_health_check = current_time

                # Normaler Scan
                time.sleep(SCAN_INTERVAL)
                self.scan_for_signals()
                self.metrics['scans_completed'] += 1

                # Erfolg - Reset Fehler-Counter
                consecutive_errors = 0
                error_backoff_time = 60  # Reset Backoff

            except KeyboardInterrupt:
                logger.info("\n[WARNUNG] Shutdown Signal empfangen...")
                break

            except ConnectionError as e:
                consecutive_errors += 1
                self.metrics['errors_encountered'] += 1
                error_backoff_time = min(error_backoff_time * 1.5, 300)  # Max 5 Minuten
                logger.error(f"[FEHLER] Verbindungsfehler #{consecutive_errors}: {e}")
                logger.info(f"[BACKOFF] Warte {error_backoff_time:.0f}s vor nächstem Versuch...")

                if consecutive_errors >= max_consecutive_errors:
                    logger.critical(f"[CRITICAL] {max_consecutive_errors} aufeinanderfolgende Fehler - Service stoppt")
                    self.running = False
                    break

                time.sleep(error_backoff_time)

            except Exception as e:
                consecutive_errors += 1
                self.metrics['errors_encountered'] += 1
                error_backoff_time = min(error_backoff_time * 1.5, 300)
                logger.error(f"[FEHLER] Unerwarteter Fehler #{consecutive_errors}: {e}", exc_info=True)

                if consecutive_errors >= max_consecutive_errors:
                    logger.critical(f"[CRITICAL] {max_consecutive_errors} aufeinanderfolgende Fehler - Service stoppt")
                    self.running = False
                    break

                logger.info(f"[BACKOFF] Warte {error_backoff_time:.0f}s vor nächstem Versuch...")
                time.sleep(error_backoff_time)
    
    def stop_service(self):
        """Stoppt den Service."""
        self.running = False
        self.disconnect_from_tws()
        self.db.close()
        logger.info("[OK] Service gestoppt")


def signal_handler(sig, frame):
    """Signal Handler für sauberes Beenden."""
    print("\n\n[STOP] Beende Service...")
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
    print(f"  TWS: {IB_HOST}:{IB_PORT}")
    print(f"  Modus: {'PAPER' if IS_PAPER_TRADING else 'LIVE'}")
    print(f"  Pushover: {'Aktiv' if PUSHOVER_USER_KEY else 'Inaktiv'}")
    print("="*70 + "\n")
    
    try:
        service = SignalService()
        service_instance = service
        
        # Test Pushover
        if PUSHOVER_USER_KEY:
            logger.info("Teste Pushover Verbindung...")
            service.notifier.test_notification()
        
        # Verbinde TWS
        if not service.connect_to_tws():
            logger.error("[FEHLER] TWS Verbindung fehlgeschlagen!")
            logger.error("Stelle sicher, dass TWS läuft und API aktiviert ist.")
            return
        
        # Starte Service
        service.run_service()
        
    except Exception as e:
        logger.error(f"[FEHLER] Kritischer Fehler: {e}", exc_info=True)
    finally:
        if service_instance:
            service_instance.stop_service()


if __name__ == "__main__":
    main()
