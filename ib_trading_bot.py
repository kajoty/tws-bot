"""
Hauptklasse für den Interactive Brokers Trading Bot.
Erbt von EClient und EWrapper für IB TWS API-Integration.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Dict, Optional, Tuple
import pandas as pd
import numpy as np

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order

import config
from database import DatabaseManager
from risk_management import RiskManager
from strategy import TradingStrategy
from contrarian_options_strategy import ContrarianOptionsStrategy
from performance import PerformanceAnalyzer
from watchlist_manager import WatchlistManager

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class IBTradingBot(EWrapper, EClient):
    """
    Trading Bot für Interactive Brokers TWS.
    Kombiniert IB API mit Strategie, Risikomanagement und Performance-Tracking.
    """

    def __init__(self, host: str = config.IB_HOST, port: int = config.IB_PORT, client_id: int = config.IB_CLIENT_ID):
        EClient.__init__(self, self)
        EWrapper.__init__(self)

        self.host = host
        self.port = port
        self.client_id = client_id

        self.db = DatabaseManager()
        self.db_manager = self.db  # Alias für Kompatibilität
        self.risk_manager = RiskManager()
        self.strategy = TradingStrategy()  # Klassische Aktienstrategie
        self.options_strategy = ContrarianOptionsStrategy()  # Konträre Optionsstrategie
        self.performance = PerformanceAnalyzer()
        self.watchlist_manager = WatchlistManager()

        self.next_valid_order_id = None
        self.connected = False
        self.historical_data_cache: Dict[str, pd.DataFrame] = {}
        self.pending_requests: Dict[int, Dict] = {}
        self.request_id_counter = 0
        self.option_chains: Dict[str, Dict] = {}  # Options Chain Daten
        self.option_prices: Dict[str, Dict] = {}  # Option Preise und IV

        # Account-Daten
        self.account_values: Dict[str, Dict] = {}
        self.account_summary: Dict[str, str] = {}
        self.portfolio_items: Dict[str, Dict] = {}

        self.is_trading_active = False
        self.watchlist = self.watchlist_manager.get_active_symbols()

        logger.info(f"IBTradingBot initialisiert: {host}:{port} (Client ID: {client_id}) "
                   f"[{'PAPER' if config.IS_PAPER_TRADING else 'LIVE'} TRADING]")

    def connect_to_tws(self) -> bool:
        """Verbindet mit TWS/Gateway."""
        try:
            logger.info(f"Verbinde mit TWS: {self.host}:{self.port}")
            self.connect(self.host, self.port, self.client_id)

            api_thread = threading.Thread(target=self.run, daemon=True)
            api_thread.start()

            timeout = 10
            start_time = time.time()
            while not self.connected and (time.time() - start_time) < timeout:
                time.sleep(0.1)

            if self.connected:
                logger.info("✓ Erfolgreich mit TWS verbunden")
                return True
            else:
                logger.error("✗ Verbindung zu TWS fehlgeschlagen (Timeout)")
                return False

        except Exception as e:
            logger.error(f"Verbindungsfehler: {e}")
            return False

    def disconnect_from_tws(self):
        """Trennt Verbindung zu TWS."""
        try:
            self.disconnect()
            self.connected = False
            logger.info("Von TWS getrennt")
        except Exception as e:
            logger.error(f"Fehler beim Trennen: {e}")

    def nextValidId(self, orderId: int):
        """Callback: Nächste gültige Order-ID."""
        super().nextValidId(orderId)
        self.next_valid_order_id = orderId
        self.connected = True
        logger.info(f"Nächste gültige Order-ID: {orderId}")

    def error(self, reqId: int, errorCode: int, errorString: str):
        """Callback: Fehlerbehandlung."""
        if errorCode >= 2000:
            if config.VERBOSE_API_LOGGING:
                logger.debug(f"Info [{errorCode}]: {errorString}")
        else:
            logger.error(f"Fehler [{errorCode}] für Request {reqId}: {errorString}")

    def historicalData(self, reqId: int, bar):
        """Callback: Historische Daten empfangen."""
        if reqId not in self.pending_requests:
            return

        request_info = self.pending_requests[reqId]
        if 'data' not in request_info:
            request_info['data'] = []

        request_info['data'].append({
            'date': bar.date,
            'open': bar.open,
            'high': bar.high,
            'low': bar.low,
            'close': bar.close,
            'volume': bar.volume
        })

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        """Callback: Historische Daten komplett."""
        if reqId not in self.pending_requests:
            return

        request_info = self.pending_requests[reqId]
        symbol = request_info.get('symbol', 'UNKNOWN')

        df = pd.DataFrame(request_info.get('data', []))

        if not df.empty:
            df['date'] = pd.to_datetime(df['date'])
            self.historical_data_cache[symbol] = df
            self.db.save_historical_data(symbol, 'STK', df)
            logger.info(f"Historische Daten für {symbol} empfangen: {len(df)} Bars")
        else:
            logger.warning(f"Keine historischen Daten für {symbol}")

        request_info['completed'] = True

    def orderStatus(self, orderId: int, status: str, filled: float, remaining: float, avgFillPrice: float,
                   permId: int, parentId: int, lastFillPrice: float, clientId: int, whyHeld: str, mktCapPrice: float):
        """Callback: Order-Status-Update."""
        logger.info(f"Order {orderId} Status: {status}, Gefüllt: {filled}, Verbleibend: {remaining}, Avg: {avgFillPrice}")

    def execDetails(self, reqId: int, contract: Contract, execution):
        """Callback: Order-Ausführung."""
        logger.info(f"Order ausgeführt: {execution.side} {execution.shares} {contract.symbol} @ {execution.price}")

        self.db.save_trade(
            symbol=contract.symbol,
            sec_type=contract.secType,
            action=execution.side,
            quantity=int(execution.shares),
            price=execution.price,
            commission=0.0,
            order_id=execution.orderId
        )

    def securityDefinitionOptionParameter(self, reqId: int, exchange: str,
                                         underlyingConId: int, tradingClass: str,
                                         multiplier: str, expirations, strikes):
        """Callback: Options Chain Parameter empfangen."""
        if reqId not in self.pending_requests:
            return
        
        request_info = self.pending_requests[reqId]
        symbol = request_info.get('symbol', 'UNKNOWN')
        
        if symbol not in self.option_chains:
            self.option_chains[symbol] = {
                'expirations': set(),
                'strikes': set(),
                'multiplier': multiplier,
                'trading_class': tradingClass,
                'underlying_con_id': underlyingConId
            }
        
        # Sammle Expirations und Strikes
        self.option_chains[symbol]['expirations'].update(expirations)
        self.option_chains[symbol]['strikes'].update(strikes)
        
        logger.debug(f"Options Chain für {symbol}: {len(expirations)} Expiries, {len(strikes)} Strikes")

    def securityDefinitionOptionParameterEnd(self, reqId: int):
        """Callback: Options Chain Daten komplett."""
        if reqId in self.pending_requests:
            request_info = self.pending_requests[reqId]
            symbol = request_info.get('symbol', 'UNKNOWN')
            
            if symbol in self.option_chains:
                chain = self.option_chains[symbol]
                chain['expirations'] = sorted(list(chain['expirations']))
                chain['strikes'] = sorted(list(chain['strikes']))
                logger.info(
                    f"Options Chain für {symbol} komplett: "
                    f"{len(chain['expirations'])} Expiries, {len(chain['strikes'])} Strikes"
                )
            
            request_info['completed'] = True

    def tickOptionComputation(self, reqId: int, tickType, impliedVol: float,
                            delta: float, optPrice: float, pvDividend: float,
                            gamma: float, vega: float, theta: float, undPrice: float):
        """Callback: Options Greeks und IV."""
        if reqId not in self.pending_requests:
            return
        
        request_info = self.pending_requests[reqId]
        
        # Sammle IV Daten
        if 'iv_data' not in request_info:
            request_info['iv_data'] = []
        
        if impliedVol > 0 and impliedVol < 5:  # Sinnvolle IV Werte (0-500%)
            request_info['iv_data'].append(impliedVol)

    def tickPrice(self, reqId: int, tickType, price: float, attrib):
        """Callback: Market Data Preis."""
        if reqId not in self.pending_requests:
            return
        
        request_info = self.pending_requests[reqId]
        
        if 'prices' not in request_info:
            request_info['prices'] = {}
        
        # tickType: 1=Bid, 2=Ask, 4=Last
        if tickType == 1:
            request_info['prices']['bid'] = price
        elif tickType == 2:
            request_info['prices']['ask'] = price
        elif tickType == 4:
            request_info['prices']['last'] = price

    def updateAccountValue(self, key: str, val: str, currency: str, accountName: str):
        """Callback: Account-Wert Update."""
        if accountName not in self.account_values:
            self.account_values[accountName] = {}
        
        self.account_values[accountName][key] = {
            'value': val,
            'currency': currency
        }
        
        if config.VERBOSE_API_LOGGING:
            logger.debug(f"Account {accountName}: {key} = {val} {currency}")

    def updatePortfolio(self, contract: Contract, position: float, marketPrice: float, 
                       marketValue: float, averageCost: float, unrealizedPNL: float, 
                       realizedPNL: float, accountName: str):
        """Callback: Portfolio-Update."""
        symbol = contract.symbol
        self.portfolio_items[symbol] = {
            'position': position,
            'market_price': marketPrice,
            'market_value': marketValue,
            'average_cost': averageCost,
            'unrealized_pnl': unrealizedPNL,
            'realized_pnl': realizedPNL,
            'account': accountName
        }
        
        if config.VERBOSE_API_LOGGING:
            logger.debug(f"Portfolio Update: {symbol} - Position: {position}, Value: ${marketValue:.2f}")

    def updateAccountTime(self, timeStamp: str):
        """Callback: Account-Zeit Update."""
        if config.VERBOSE_API_LOGGING:
            logger.debug(f"Account Time: {timeStamp}")

    def accountDownloadEnd(self, accountName: str):
        """Callback: Account-Download abgeschlossen."""
        logger.info(f"Account-Daten für {accountName} vollständig geladen")

    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str):
        """Callback: Account Summary."""
        key = f"{tag}_{currency}" if currency else tag
        self.account_summary[key] = value
        
        if config.VERBOSE_API_LOGGING:
            logger.debug(f"Account Summary: {tag} = {value} {currency}")

    def accountSummaryEnd(self, reqId: int):
        """Callback: Account Summary Ende."""
        if reqId in self.pending_requests:
            self.pending_requests[reqId]['completed'] = True
        logger.info("Account Summary vollständig geladen")

    def create_stock_contract(self, symbol: str, exchange: str = "SMART") -> Contract:
        """Erstellt Aktien-Contract."""
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = exchange
        contract.currency = "USD"
        return contract

    def create_option_contract(self, symbol: str, expiry: str, strike: float, right: str, exchange: str = "SMART") -> Contract:
        """Erstellt Options-Contract."""
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "OPT"
        contract.exchange = exchange
        contract.currency = "USD"
        contract.lastTradeDateOrContractMonth = expiry
        contract.strike = strike
        contract.right = right
        contract.multiplier = "100"
        return contract
    
    def request_contract_details(self, contract: Contract) -> int:
        """
        Fordert Contract-Details an (für Options-Chain).
        
        Args:
            contract: Contract-Objekt (kann unvollständig sein für Suche)
            
        Returns:
            Request ID
        """
        req_id = self._get_next_request_id()
        
        self.pending_requests[req_id] = {
            'type': 'contract_details',
            'symbol': contract.symbol,
            'data': [],
            'completed': False
        }
        
        self.reqContractDetails(req_id, contract)
        logger.info(f"Contract Details angefordert für {contract.symbol} (Request ID: {req_id})")
        return req_id
    
    def contractDetails(self, reqId: int, contractDetails):
        """Callback: Contract Details empfangen."""
        if reqId not in self.pending_requests:
            return
        
        request_info = self.pending_requests[reqId]
        if 'data' not in request_info:
            request_info['data'] = []
        
        # Extrahiere relevante Contract-Infos
        contract = contractDetails.contract
        contract_info = {
            'symbol': contract.symbol,
            'strike': contract.strike,
            'expiry': contract.lastTradeDateOrContractMonth,
            'right': contract.right,
            'multiplier': contract.multiplier,
            'exchange': contract.exchange,
            'conId': contract.conId
        }
        
        request_info['data'].append(contract_info)
        
        if config.VERBOSE_API_LOGGING:
            logger.debug(f"Contract Detail: {contract.symbol} {contract.strike} {contract.right} {contract.lastTradeDateOrContractMonth}")
    
    def contractDetailsEnd(self, reqId: int):
        """Callback: Contract Details komplett."""
        if reqId in self.pending_requests:
            self.pending_requests[reqId]['completed'] = True
            symbol = self.pending_requests[reqId].get('symbol', 'UNKNOWN')
            count = len(self.pending_requests[reqId].get('data', []))
            logger.info(f"Contract Details für {symbol} vollständig: {count} Contracts")
    
    def find_option_by_dte_and_delta(self, symbol: str, strategy_type: str, 
                                     target_delta: Optional[float] = None) -> Optional[Dict]:
        """
        Findet passende Option basierend auf DTE-Bereich und Delta.
        
        Args:
            symbol: Underlying Symbol
            strategy_type: "LONG_PUT" oder "LONG_CALL"
            target_delta: Ziel-Delta (None für ATM)
            
        Returns:
            Dict mit Contract-Details oder None
        """
        # Bestimme Parameter basierend auf Strategie
        if strategy_type == "LONG_PUT":
            dte_min = config.LONG_PUT_DTE_MIN
            dte_max = config.LONG_PUT_DTE_MAX
            right = "P"
            delta_target = -0.50  # ATM Put hat Delta ~-0.50
        elif strategy_type == "LONG_CALL":
            dte_min = config.LONG_CALL_DTE_MIN
            dte_max = config.LONG_CALL_DTE_MAX
            right = "C"
            delta_target = target_delta or config.LONG_CALL_DELTA_TARGET
        else:
            logger.error(f"Unbekannter Strategie-Typ: {strategy_type}")
            return None
        
        # Erstelle Contract für Options-Chain Abfrage
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "OPT"
        contract.exchange = "SMART"
        contract.currency = "USD"
        contract.right = right
        
        # Anfrage Contract Details
        req_id = self.request_contract_details(contract)
        
        # Warte auf Antwort
        if not self.wait_for_request(req_id, timeout=10):
            logger.warning(f"Timeout beim Abrufen der Options-Chain für {symbol}")
            return None
        
        # Filtere Options nach DTE
        contracts = self.pending_requests[req_id].get('data', [])
        
        from datetime import datetime
        today = datetime.now()
        
        valid_contracts = []
        for c in contracts:
            try:
                # Parse Expiry (Format: YYYYMMDD)
                expiry_str = c['expiry']
                expiry_date = datetime.strptime(expiry_str, '%Y%m%d')
                dte = (expiry_date - today).days
                
                if dte_min <= dte <= dte_max:
                    c['dte'] = dte
                    valid_contracts.append(c)
            except Exception as e:
                logger.debug(f"Fehler beim Parsen von Expiry {c.get('expiry')}: {e}")
                continue
        
        if not valid_contracts:
            logger.warning(f"Keine Options im DTE-Bereich {dte_min}-{dte_max} für {symbol}")
            return None
        
        # Sortiere nach DTE (bevorzuge mittleren Bereich)
        target_dte = (dte_min + dte_max) / 2
        valid_contracts.sort(key=lambda x: abs(x['dte'] - target_dte))
        
        # Für jetzt: Nimm erste passende Option
        # TODO: Delta-basierte Auswahl implementieren (benötigt Marktdaten-Abfrage)
        selected = valid_contracts[0]
        
        logger.info(
            f"Option ausgewählt: {symbol} {selected['strike']} {selected['right']} "
            f"{selected['expiry']} (DTE: {selected['dte']})"
        )
        
        return selected
    
    def place_option_order(self, symbol: str, strategy_type: str, 
                          quantity: int, option_details: Dict) -> Optional[int]:
        """
        Platziert Options-Order.
        
        Args:
            symbol: Underlying Symbol
            strategy_type: "LONG_PUT" oder "LONG_CALL"
            quantity: Anzahl Contracts
            option_details: Dict mit Strike, Expiry, Right
            
        Returns:
            Order ID oder None
        """
        if config.DRY_RUN:
            logger.info(
                f"[DRY RUN] Options-Order: {strategy_type} {quantity} contracts "
                f"{symbol} {option_details['strike']} {option_details['right']} {option_details['expiry']}"
            )
            return None
        
        # Erstelle Options-Contract
        contract = self.create_option_contract(
            symbol=symbol,
            expiry=option_details['expiry'],
            strike=option_details['strike'],
            right=option_details['right']
        )
        
        # Erstelle Market Order (Buy to Open)
        order = Order()
        order.action = "BUY"
        order.orderType = "MKT"
        order.totalQuantity = quantity
        
        # Platziere Order
        order_id = self.next_valid_order_id
        self.placeOrder(order_id, contract, order)
        self.next_valid_order_id += 1
        
        logger.info(
            f"✓ Options-Order platziert: BUY {quantity} {symbol} "
            f"{option_details['strike']}{option_details['right']} {option_details['expiry']} "
            f"(Order ID: {order_id})"
        )
        
        # Speichere in Datenbank
        self.db.save_trade(
            symbol=symbol,
            sec_type="OPT",
            action="BUY",
            quantity=quantity,
            price=0.0,  # Wird durch execDetails aktualisiert
            commission=0.0,
            order_id=order_id
        )
        
        return order_id

    def request_historical_data(self, symbol: str, duration: str = config.HISTORICAL_DATA_DURATION,
                               bar_size: str = config.HISTORICAL_DATA_BAR_SIZE) -> int:
        """Fordert historische Daten an."""
        contract = self.create_stock_contract(symbol)

        req_id = self._get_next_request_id()
        self.pending_requests[req_id] = {
            'symbol': symbol,
            'type': 'historical_data',
            'completed': False
        }

        self.reqHistoricalData(
            reqId=req_id,
            contract=contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=1,
            formatDate=1,
            keepUpToDate=False,
            chartOptions=[]
        )

        logger.info(f"Historische Daten angefordert: {symbol} ({duration}, {bar_size})")
        return req_id

    def wait_for_request(self, req_id: int, timeout: int = 30) -> bool:
        """Wartet auf Abschluss eines Requests."""
        start_time = time.time()
        while (time.time() - start_time) < timeout:
            if req_id in self.pending_requests:
                if self.pending_requests[req_id].get('completed', False):
                    return True
            time.sleep(0.1)

        logger.warning(f"Request {req_id} Timeout nach {timeout}s")
        return False

    def place_order(self, symbol: str, action: str, quantity: int, order_type: str = "MKT",
                   limit_price: Optional[float] = None) -> Optional[int]:
        """Platziert eine Order."""
        if config.DRY_RUN:
            logger.info(f"[DRY RUN] Order: {action} {quantity} {symbol} ({order_type})")
            return None

        try:
            contract = self.create_stock_contract(symbol)

            order = Order()
            order.action = action
            order.totalQuantity = quantity
            order.orderType = order_type

            if order_type == "LMT" and limit_price:
                order.lmtPrice = limit_price

            order_id = self.next_valid_order_id
            self.next_valid_order_id += 1

            self.placeOrder(order_id, contract, order)

            logger.info(f"Order platziert: {action} {quantity} {symbol} ({order_type}) - ID: {order_id}")

            return order_id

        except Exception as e:
            logger.error(f"Fehler beim Platzieren der Order: {e}")
            return None

    def run_trading_cycle(self):
        """Führt einen Trading-Zyklus aus: Daten aktualisieren, Strategie prüfen, Orders platzieren."""
        logger.info("=== Trading-Zyklus gestartet ===")
        
        # Wähle Strategie basierend auf Config
        if config.TRADING_STRATEGY == 'OPTIONS':
            self._run_options_trading_cycle()
        else:
            self._run_stock_trading_cycle()
        
        logger.info("=== Trading-Zyklus beendet ===")

    def _run_stock_trading_cycle(self):
        """Führt klassischen Aktienhandel aus."""
        for symbol in self.watchlist:
            try:
                # Prüfe ob Daten aus DB geladen werden können
                if symbol not in self.historical_data_cache:
                    # Versuche erst aus DB zu laden
                    df = self.db_manager.load_historical_data(symbol)
                    
                    # Wenn Daten vorhanden und aktuell sind (< CONFIG.DATA_MAX_AGE_DAYS), nutze sie
                    if not df.empty and not self.db_manager.needs_update(symbol, max_age_days=config.DATA_MAX_AGE_DAYS):
                        self.historical_data_cache[symbol] = df
                        logger.info(f"Daten aus DB geladen fuer {symbol} (keine Aktualisierung noetig)")
                    else:
                        # Sonst: Von IB API laden
                        req_id = self.request_historical_data(symbol)
                        self.wait_for_request(req_id)

                df = self.historical_data_cache.get(symbol)
                if df is None or df.empty:
                    logger.warning(f"Keine Daten für {symbol}")
                    continue

                signal, confidence, details = self.strategy.check_strategy(symbol, df)

                if signal == 'HOLD' or confidence < 0.6:
                    continue

                can_trade, reason = self.risk_manager.can_open_position(symbol)
                if not can_trade:
                    logger.info(f"Trade nicht möglich für {symbol}: {reason}")
                    continue

                current_price = df.iloc[-1]['close']
                stop_loss = details.get('stop_loss_price')

                if stop_loss is None:
                    logger.warning(f"Kein Stop-Loss für {symbol}")
                    continue

                quantity, risk, calc_details = self.risk_manager.calculate_position_size(
                    symbol, current_price, stop_loss
                )

                if quantity == 0:
                    logger.info(f"Positionsgröße zu klein für {symbol}")
                    continue

                if signal == 'BUY':
                    order_id = self.place_order(symbol, "BUY", quantity)
                    if order_id:
                        self.risk_manager.add_position(symbol, quantity, current_price, stop_loss)
                        
                        # Speichere Trade im Tagebuch
                        self.db_manager.save_trade(
                            symbol=symbol,
                            sec_type="STK",
                            action="BUY",
                            quantity=quantity,
                            price=current_price,
                            commission=config.STOCK_COMMISSION_PER_ORDER,
                            order_id=order_id,
                            strategy="STOCK_MA_RSI"
                        )
                        
                        logger.info(f"✓ Kauforder platziert: {quantity} {symbol} @ ${current_price:.2f}")

            except Exception as e:
                logger.error(f"Fehler im Trading-Zyklus für {symbol}: {e}")

    def _run_options_trading_cycle(self):
        """Führt konträre Optionsstrategie aus."""
        logger.info(f"[OPTIONS] Analysiere {len(self.watchlist)} Symbole für Optionssignale...")
        
        # 1. ERST: Prüfe bestehende Positionen auf Stop-Loss und Exit-Signale
        self._check_existing_positions_options()
        
        # 2. DANN: Suche nach neuen Entry-Signalen
        for symbol in self.watchlist:
            try:
                # Lade historische Preisdaten
                if symbol not in self.historical_data_cache:
                    df = self.db_manager.load_historical_data(symbol)
                    
                    if not df.empty and not self.db_manager.needs_update(symbol, max_age_days=config.DATA_MAX_AGE_DAYS):
                        self.historical_data_cache[symbol] = df
                    else:
                        req_id = self.request_historical_data(symbol)
                        self.wait_for_request(req_id)

                df = self.historical_data_cache.get(symbol)
                if df is None or df.empty:
                    continue

                # Lade Fundamentaldaten aus DB
                fundamental_data = self.db_manager.get_fundamental_data(symbol)
                if not fundamental_data:
                    logger.debug(f"[OPTIONS] Keine Fundamentaldaten für {symbol}")
                    continue

                # Universe Filter prüfen
                market_cap = fundamental_data.get('market_cap', 0)
                avg_volume = fundamental_data.get('avg_volume', 0)
                
                passes_filter, reason = self.options_strategy.check_universe_filter(
                    symbol, market_cap, avg_volume
                )
                
                if not passes_filter:
                    logger.debug(f"[OPTIONS] {symbol}: {reason}")
                    continue

                # Prüfe Long Put Kriterien (Short am 52W-Hoch)
                long_put_signal, put_confidence, put_details = self.options_strategy.check_long_put_criteria(
                    symbol, df, fundamental_data
                )

                # Prüfe Long Call Kriterien (Long am 52W-Tief)  
                long_call_signal, call_confidence, call_details = self.options_strategy.check_long_call_criteria(
                    symbol, df, fundamental_data
                )

                # Verarbeite Long Put Signal
                if long_put_signal and put_confidence >= config.MIN_CONFIDENCE_OPTIONS:
                    logger.info(
                        f"[OPTIONS] 📉 LONG PUT Signal für {symbol} "
                        f"(Confidence: {put_confidence:.2f})"
                    )
                    
                    # Prüfe Position Limits
                    if not self.risk_manager.can_open_position(symbol, "OPT"):
                        logger.info(f"[OPTIONS] Position Limit erreicht für {symbol}")
                        continue
                    
                    current_price = put_details['current_price']
                    
                    # 1. Request Options Chain
                    logger.info(f"[OPTIONS] Lade Options Chain für {symbol}...")
                    chain_req_id = self.request_option_chain(symbol)
                    self.wait_for_request(chain_req_id, timeout=10)
                    
                    # 2. Wähle Strike und Expiry
                    strike_expiry = self.select_option_strike_and_expiry(
                        symbol, "LONG_PUT", current_price
                    )
                    
                    if not strike_expiry:
                        logger.warning(f"[OPTIONS] Konnte Strike/Expiry nicht wählen für {symbol}")
                        continue
                    
                    expiry, strike = strike_expiry
                    
                    # 3. Erstelle Option Contract
                    option_contract = self.create_option_contract(symbol, expiry, strike, "P")
                    
                    # 4. Request Market Data für Preis
                    md_req_id = self.request_option_market_data(option_contract)
                    self.wait_for_request(md_req_id, timeout=5)
                    
                    request_info = self.pending_requests.get(md_req_id, {})
                    prices = request_info.get('prices', {})
                    iv_data = request_info.get('iv_data', [])
                    
                    bid = prices.get('bid', 0)
                    ask = prices.get('ask', 0)
                    
                    if bid <= 0 or ask <= 0:
                        logger.warning(f"[OPTIONS] Keine gültigen Preise für {symbol} Option")
                        continue
                    
                    mid_price = (bid + ask) / 2
                    premium = mid_price * 100  # Pro Kontrakt
                    
                    # 5. Berechne Anzahl Kontrakte
                    max_risk = config.ACCOUNT_SIZE * config.MAX_RISK_PER_TRADE_PCT
                    contracts = max(1, int(max_risk / premium))
                    
                    # 6. Platziere Order
                    order_id = self.place_option_order(
                        option_contract, "BUY", contracts, mid_price
                    )
                    
                    if order_id:
                        # 7. Speichere Trade
                        self.db_manager.save_trade(
                            symbol=symbol,
                            sec_type="OPT",
                            action="BUY",
                            quantity=contracts,
                            price=mid_price,
                            commission=config.OPTION_COMMISSION_PER_CONTRACT * contracts,
                            order_id=order_id,
                            strategy="OPTIONS_LONG_PUT",
                            notes=f"Strike ${strike}, Expiry {expiry}, IV: {np.mean(iv_data) if iv_data else 'N/A'}"
                        )
                        
                        logger.info(
                            f"✓ [OPTIONS] Long Put Order platziert: {contracts}x {symbol} "
                            f"${strike}P @ ${mid_price:.2f} (Premium: ${premium:.2f})"
                        )

                # Verarbeite Long Call Signal
                if long_call_signal and call_confidence >= config.MIN_CONFIDENCE_OPTIONS:
                    logger.info(
                        f"[OPTIONS] 📈 LONG CALL Signal für {symbol} "
                        f"(Confidence: {call_confidence:.2f})"
                    )
                    
                    # Prüfe Position Limits
                    if not self.risk_manager.can_open_position(symbol, "OPT"):
                        logger.info(f"[OPTIONS] Position Limit erreicht für {symbol}")
                        continue
                    
                    current_price = call_details['current_price']
                    
                    # 1. Request Options Chain
                    logger.info(f"[OPTIONS] Lade Options Chain für {symbol}...")
                    chain_req_id = self.request_option_chain(symbol)
                    self.wait_for_request(chain_req_id, timeout=10)
                    
                    # 2. Wähle Strike und Expiry
                    strike_expiry = self.select_option_strike_and_expiry(
                        symbol, "LONG_CALL", current_price
                    )
                    
                    if not strike_expiry:
                        logger.warning(f"[OPTIONS] Konnte Strike/Expiry nicht wählen für {symbol}")
                        continue
                    
                    expiry, strike = strike_expiry
                    
                    # 3. Erstelle Option Contract
                    option_contract = self.create_option_contract(symbol, expiry, strike, "C")
                    
                    # 4. Request Market Data für Preis
                    md_req_id = self.request_option_market_data(option_contract)
                    self.wait_for_request(md_req_id, timeout=5)
                    
                    request_info = self.pending_requests.get(md_req_id, {})
                    prices = request_info.get('prices', {})
                    iv_data = request_info.get('iv_data', [])
                    
                    bid = prices.get('bid', 0)
                    ask = prices.get('ask', 0)
                    
                    if bid <= 0 or ask <= 0:
                        logger.warning(f"[OPTIONS] Keine gültigen Preise für {symbol} Option")
                        continue
                    
                    mid_price = (bid + ask) / 2
                    premium = mid_price * 100  # Pro Kontrakt
                    
                    # 5. Berechne Anzahl Kontrakte
                    max_risk = config.ACCOUNT_SIZE * config.MAX_RISK_PER_TRADE_PCT
                    contracts = max(1, int(max_risk / premium))
                    
                    # 6. Platziere Order
                    order_id = self.place_option_order(
                        option_contract, "BUY", contracts, mid_price
                    )
                    
                    if order_id:
                        # 7. Speichere Trade
                        self.db_manager.save_trade(
                            symbol=symbol,
                            sec_type="OPT",
                            action="BUY",
                            quantity=contracts,
                            price=mid_price,
                            commission=config.OPTION_COMMISSION_PER_CONTRACT * contracts,
                            order_id=order_id,
                            strategy="OPTIONS_LONG_CALL",
                            notes=f"Strike ${strike}, Expiry {expiry}, IV: {np.mean(iv_data) if iv_data else 'N/A'}"
                        )
                        
                        logger.info(
                            f"✓ [OPTIONS] Long Call Order platziert: {contracts}x {symbol} "
                            f"${strike}C @ ${mid_price:.2f} (Premium: ${premium:.2f})"
                        )

            except Exception as e:
                logger.error(f"[OPTIONS] Fehler für {symbol}: {e}", exc_info=True)

    def _check_existing_positions_options(self):
        """Prüft bestehende Positionen auf Stop-Loss und Exit-Signale (für Options-Strategie)."""
        if not self.risk_manager.current_positions:
            return
        
        logger.info(f"[OPTIONS] Prüfe {len(self.risk_manager.current_positions)} bestehende Positionen...")
        
        for symbol in list(self.risk_manager.current_positions.keys()):
            try:
                position = self.risk_manager.current_positions[symbol]
                
                # Hole aktuelle Preisdaten
                df = self.historical_data_cache.get(symbol)
                if df is None or df.empty:
                    # Lade Daten nach
                    req_id = self.request_historical_data(symbol)
                    self.wait_for_request(req_id)
                    df = self.historical_data_cache.get(symbol)
                
                if df is None or df.empty:
                    logger.warning(f"[OPTIONS] Keine Daten für Position {symbol}")
                    continue
                
                current_price = df.iloc[-1]['close']
                entry_price = position['entry_price']
                stop_loss = position['stop_loss']
                quantity = position['quantity']
                
                # Berechne PnL
                pnl = (current_price - entry_price) * quantity
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                
                # Prüfe Stop-Loss
                if self.risk_manager.check_stop_loss(symbol, current_price):
                    logger.warning(
                        f"[OPTIONS] ⚠ STOP-LOSS getriggert für {symbol}! "
                        f"Entry: ${entry_price:.2f}, Current: ${current_price:.2f}, "
                        f"PnL: ${pnl:.2f} ({pnl_pct:.2f}%)"
                    )
                    
                    # Platziere Close-Order
                    order_id = self.place_order(symbol, "SELL", quantity)
                    if order_id:
                        # Speichere Trade im Tagebuch
                        self.db_manager.save_trade(
                            symbol=symbol,
                            sec_type="STK",  # TODO: Für Optionen auf "OPT" ändern
                            action="SELL",
                            quantity=quantity,
                            price=current_price,
                            commission=config.STOCK_COMMISSION_PER_ORDER,
                            order_id=order_id,
                            strategy="OPTIONS_STOP_LOSS",
                            notes=f"PnL: ${pnl:.2f} ({pnl_pct:.2f}%)"
                        )
                        logger.info(f"[OPTIONS] ✓ Stop-Loss Order platziert: SELL {quantity} {symbol} @ ${current_price:.2f}")
                
                # Prüfe Exit-Signale (Mean-Reversion erreicht)
                # TODO: Implementiere Take-Profit Logik für Optionen
                
            except Exception as e:
                logger.error(f"[OPTIONS] Fehler beim Prüfen von Position {symbol}: {e}", exc_info=True)

    def _get_next_request_id(self) -> int:
        """Generiert nächste Request-ID."""
        self.request_id_counter += 1
        return self.request_id_counter

    def request_account_updates(self, subscribe: bool = True, account: str = ""):
        """Fordert Account-Updates an."""
        try:
            self.reqAccountUpdates(subscribe, account)
            logger.info(f"Account-Updates {'aktiviert' if subscribe else 'deaktiviert'}")
            return True
        except Exception as e:
            logger.error(f"Fehler bei Account-Updates: {e}")
            return False

    def request_account_summary(self) -> int:
        """Fordert Account Summary an."""
        req_id = self._get_next_request_id()
        
        self.pending_requests[req_id] = {
            'type': 'account_summary',
            'completed': False
        }
        
        # Tags für Account Summary
        tags = "AccountType,NetLiquidation,TotalCashValue,SettledCash,AccruedCash," \
               "BuyingPower,EquityWithLoanValue,GrossPositionValue," \
               "AvailableFunds,ExcessLiquidity,Cushion,FullInitMarginReq," \
               "FullMaintMarginReq,UnrealizedPnL,RealizedPnL,DayTradesRemaining"
        
        self.reqAccountSummary(req_id, "All", tags)
        logger.info(f"Account Summary angefordert (Request ID: {req_id})")
        return req_id

    def request_option_chain(self, symbol: str, underlying_con_id: int = 0) -> int:
        """
        Fordert Options Chain für ein Symbol an.
        
        Args:
            symbol: Underlying Symbol (z.B. "AAPL")
            underlying_con_id: Contract ID des Underlyings (optional)
            
        Returns:
            Request ID
        """
        req_id = self._get_next_request_id()
        
        self.pending_requests[req_id] = {
            'type': 'option_chain',
            'symbol': symbol,
            'completed': False
        }
        
        # Erstelle Underlying Contract
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        
        if underlying_con_id:
            contract.conId = underlying_con_id
        
        self.reqSecDefOptParams(req_id, symbol, "", "STK", underlying_con_id)
        logger.debug(f"Options Chain für {symbol} angefordert (Request ID: {req_id})")
        return req_id

    def request_option_market_data(self, contract: Contract) -> int:
        """
        Fordert Market Data für einen Options-Contract an.
        
        Args:
            contract: Options Contract
            
        Returns:
            Request ID
        """
        req_id = self._get_next_request_id()
        
        self.pending_requests[req_id] = {
            'type': 'option_market_data',
            'contract': contract,
            'completed': False,
            'prices': {},
            'iv_data': []
        }
        
        # Fordere Bid/Ask und Greeks an
        self.reqMktData(req_id, contract, "", False, False, [])
        logger.debug(f"Market Data für Option angefordert (Request ID: {req_id})")
        return req_id

    def select_option_strike_and_expiry(self, symbol: str, strategy_type: str,
                                       current_stock_price: float) -> Optional[Tuple[str, float]]:
        """
        Wählt optimalen Strike und Expiry für Options-Trade.
        
        Args:
            symbol: Underlying Symbol
            strategy_type: "LONG_PUT" oder "LONG_CALL"
            current_stock_price: Aktueller Aktienkurs
            
        Returns:
            (expiry_date, strike_price) oder None
        """
        if symbol not in self.option_chains:
            logger.warning(f"Keine Options Chain für {symbol} verfügbar")
            return None
        
        chain = self.option_chains[symbol]
        expirations = chain['expirations']
        strikes = chain['strikes']
        
        if not expirations or not strikes:
            logger.warning(f"Options Chain für {symbol} ist leer")
            return None
        
        # 1. Wähle Expiry (30-45 DTE bevorzugt)
        from datetime import datetime, timedelta
        today = datetime.now()
        target_dte_min = 30
        target_dte_max = 45
        
        suitable_expiries = []
        for expiry_str in expirations:
            try:
                # Format: YYYYMMDD
                expiry_date = datetime.strptime(expiry_str, "%Y%m%d")
                dte = (expiry_date - today).days
                
                if target_dte_min <= dte <= target_dte_max:
                    suitable_expiries.append((expiry_str, dte))
            except:
                continue
        
        if not suitable_expiries:
            # Fallback: Nächster verfügbarer Expiry > 30 Tage
            for expiry_str in expirations:
                try:
                    expiry_date = datetime.strptime(expiry_str, "%Y%m%d")
                    dte = (expiry_date - today).days
                    if dte >= 30:
                        suitable_expiries.append((expiry_str, dte))
                        break
                except:
                    continue
        
        if not suitable_expiries:
            logger.warning(f"Keine geeigneten Expiries für {symbol} gefunden")
            return None
        
        # Wähle Expiry mit DTE am nächsten zu 35 Tagen
        selected_expiry = min(suitable_expiries, key=lambda x: abs(x[1] - 35))
        expiry_date = selected_expiry[0]
        
        # 2. Wähle Strike basierend auf Strategie
        if strategy_type == "LONG_PUT":
            # Für Long Put: ATM oder leicht OTM (Strike etwas unter aktuellem Preis)
            # Ziel: Strike bei 95-100% des aktuellen Preises
            target_strike = current_stock_price * 0.975  # 2.5% OTM
            
        elif strategy_type == "LONG_CALL":
            # Für Long Call: ATM oder leicht OTM (Strike etwas über aktuellem Preis)
            # Ziel: Strike bei 100-105% des aktuellen Preises
            target_strike = current_stock_price * 1.025  # 2.5% OTM
        else:
            logger.error(f"Unbekannter Strategy Type: {strategy_type}")
            return None
        
        # Finde nächsten verfügbaren Strike
        available_strikes = sorted([float(s) for s in strikes])
        selected_strike = min(available_strikes, key=lambda x: abs(x - target_strike))
        
        logger.info(
            f"Strike & Expiry gewählt für {symbol} {strategy_type}: "
            f"Strike ${selected_strike:.2f} (Target: ${target_strike:.2f}), "
            f"Expiry {expiry_date} ({selected_expiry[1]} DTE)"
        )
        
        return (expiry_date, selected_strike)

    def create_option_contract(self, symbol: str, expiry: str, strike: float,
                              right: str) -> Contract:
        """
        Erstellt IB Options Contract.
        
        Args:
            symbol: Underlying Symbol
            expiry: Expiry Datum (YYYYMMDD)
            strike: Strike Price
            right: "C" für Call, "P" für Put
            
        Returns:
            IB Contract Objekt
        """
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "OPT"
        contract.exchange = "SMART"
        contract.currency = "USD"
        contract.lastTradeDateOrContractMonth = expiry
        contract.strike = strike
        contract.right = right
        contract.multiplier = "100"  # US Equity Options
        
        return contract

    def place_option_order(self, contract: Contract, action: str, quantity: int,
                          limit_price: Optional[float] = None) -> Optional[int]:
        """
        Platziert Options Order.
        
        Args:
            contract: Options Contract
            action: "BUY" oder "SELL"
            quantity: Anzahl Kontrakte
            limit_price: Limit Preis (optional, berechnet Mid-Price wenn None)
            
        Returns:
            Order ID oder None bei Fehler
        """
        if not self.connected:
            logger.error("Nicht mit TWS verbunden!")
            return None
        
        if config.DRY_RUN:
            logger.info(
                f"[DRY RUN] Option Order: {action} {quantity} {contract.symbol} "
                f"{contract.strike}{contract.right} @ ${limit_price:.2f}"
            )
            return -1  # Fake Order ID
        
        try:
            from ibapi.order import Order
            
            order = Order()
            order.action = action
            order.totalQuantity = quantity
            order.orderType = "LMT"
            
            # Wenn kein Limit Preis gegeben, verwende Mid-Price
            if limit_price is None:
                # Request Market Data um Bid/Ask zu bekommen
                req_id = self.request_option_market_data(contract)
                self.wait_for_request(req_id, timeout=5)
                
                request_info = self.pending_requests.get(req_id, {})
                prices = request_info.get('prices', {})
                
                bid = prices.get('bid', 0)
                ask = prices.get('ask', 0)
                
                if bid > 0 and ask > 0:
                    # Liquiditätsfilter: Spread < 10%
                    mid_price = (bid + ask) / 2
                    spread_pct = (ask - bid) / mid_price if mid_price > 0 else 0
                    
                    if spread_pct > 0.10:  # 10% Spread zu breit
                        logger.warning(
                            f"Spread zu breit: {spread_pct*100:.1f}% "
                            f"(Bid: ${bid:.2f}, Ask: ${ask:.2f}). Order abgebrochen."
                        )
                        return None
                    
                    limit_price = mid_price
                    logger.info(f"Mid-Price berechnet: ${limit_price:.2f} (Bid: ${bid:.2f}, Ask: ${ask:.2f}, Spread: {spread_pct*100:.1f}%)")
                else:
                    logger.error("Konnte Bid/Ask nicht abrufen!")
                    return None
            
            order.lmtPrice = round(limit_price, 2)
            
            order_id = self.next_valid_order_id
            self.placeOrder(order_id, contract, order)
            self.next_valid_order_id += 1
            
            logger.info(
                f"Option Order platziert: {action} {quantity} {contract.symbol} "
                f"{contract.strike}{contract.right} @ ${order.lmtPrice:.2f} (Order ID: {order_id})"
            )
            
            return order_id
            
        except Exception as e:
            logger.error(f"Fehler beim Platzieren der Option Order: {e}")
            return None

    def get_account_value(self, key: str, account: str = None) -> Optional[Dict]:
        """Gibt einen spezifischen Account-Wert zurück."""
        if account:
            return self.account_values.get(account, {}).get(key)
        
        # Wenn kein Account angegeben, nimm ersten verfügbaren
        for acc_values in self.account_values.values():
            if key in acc_values:
                return acc_values[key]
        return None

    def get_account_summary_value(self, key: str) -> Optional[str]:
        """Gibt einen Wert aus dem Account Summary zurück."""
        return self.account_summary.get(key)

    def print_account_info(self):
        """Gibt Account-Informationen aus."""
        print("\n" + "="*70)
        print(" ACCOUNT INFORMATION")
        print("="*70)
        
        # Account Summary Werte
        if self.account_summary:
            print("\n Account Summary:")
            important_keys = [
                'NetLiquidation', 'TotalCashValue', 'BuyingPower',
                'AvailableFunds', 'Cushion', 'UnrealizedPnL', 'RealizedPnL'
            ]
            
            for key in important_keys:
                # Suche nach Key mit oder ohne Currency-Suffix
                value = None
                for full_key in self.account_summary:
                    if full_key.startswith(key):
                        value = self.account_summary[full_key]
                        break
                
                if value:
                    try:
                        num_val = float(value)
                        if key == 'Cushion':
                            print(f"  {key:20s}: {num_val*100:.2f}%")
                        else:
                            print(f"  {key:20s}: ${num_val:,.2f}")
                    except ValueError:
                        print(f"  {key:20s}: {value}")
        
        # Portfolio Positionen
        if self.portfolio_items:
            print("\n Portfolio Positionen:")
            for symbol, data in self.portfolio_items.items():
                if data['position'] != 0:
                    print(f"  {symbol:8s}: {data['position']:>6.0f} shares @ ${data['average_cost']:>8.2f} "
                          f"| Market: ${data['market_price']:>8.2f} | PnL: ${data['unrealized_pnl']:>10.2f}")
        
        print("="*70 + "\n")

    def get_portfolio_summary(self):
        """Gibt Portfolio-Zusammenfassung aus."""
        summary = self.risk_manager.get_portfolio_summary()

        print("\n" + "="*60)
        print(" PORTFOLIO SUMMARY")
        print("="*60)
        print(f" Total Equity:       ${summary['total_equity']:,.2f}")
        print(f" Cash Available:     ${summary['cash_available']:,.2f}")
        print(f" Positions Value:    ${summary['positions_value']:,.2f}")
        print(f" Unrealized PnL:     ${summary['unrealized_pnl']:,.2f}")
        print(f" Number of Positions: {summary['num_positions']}")
        print("="*60)

        if summary['positions']:
            print("\nAktive Positionen:")
            for symbol, pos in summary['positions'].items():
                print(f"  {symbol}: {pos['quantity']} @ ${pos['entry_price']:.2f} "
                      f"(Current: ${pos['current_price']:.2f}, PnL: ${pos['unrealized_pnl']:.2f})")
        print()
