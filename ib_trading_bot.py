"""
Hauptklasse für den Interactive Brokers Trading Bot.
Erbt von EClient und EWrapper für IB TWS API-Integration.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Dict, Optional
import pandas as pd

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order

import config
from database import DatabaseManager
from risk_management import RiskManager
from strategy import TradingStrategy
from performance import PerformanceAnalyzer

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
        self.risk_manager = RiskManager()
        self.strategy = TradingStrategy()
        self.performance = PerformanceAnalyzer()

        self.next_valid_order_id = None
        self.connected = False
        self.historical_data_cache: Dict[str, pd.DataFrame] = {}
        self.pending_requests: Dict[int, Dict] = {}
        self.request_id_counter = 0

        self.is_trading_active = False
        self.watchlist = config.WATCHLIST_STOCKS

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

        for symbol in self.watchlist:
            try:
                if symbol not in self.historical_data_cache:
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
                        logger.info(f"✓ Kauforder platziert: {quantity} {symbol} @ ${current_price:.2f}")

            except Exception as e:
                logger.error(f"Fehler im Trading-Zyklus für {symbol}: {e}")

        logger.info("=== Trading-Zyklus beendet ===")

    def _get_next_request_id(self) -> int:
        """Generiert nächste Request-ID."""
        self.request_id_counter += 1
        return self.request_id_counter

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
