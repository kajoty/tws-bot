"""
TWS API Connector für Interactive Brokers.
"""

import time
import logging
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ..config.settings import IB_HOST, IB_PORT, IB_CLIENT_ID

logger = logging.getLogger(__name__)


class TWSConnector(EWrapper, EClient):
    """Verbindet mit TWS und handhabt Datenanfragen."""

    def __init__(self):
        EClient.__init__(self, wrapper=self)
        EWrapper.__init__(self)

        self.host = IB_HOST
        self.port = IB_PORT
        self.client_id = IB_CLIENT_ID

        self.connected = False
        self.next_valid_order_id = None
        self.request_id_counter = 1
        self.pending_requests = {}
        self.connection_attempts = 0
        self.max_connection_attempts = 5
        self.reconnect_delay = 30  # Sekunden zwischen Reconnect-Versuchen
        self.last_reconnect_attempt = 0

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        """TWS Error Callback."""
        if errorCode in [2104, 2106, 2158]:
            logger.info(f"TWS Info [{errorCode}]: {errorString}")
        elif errorCode == 502:
            logger.error(f"[FEHLER] TWS nicht verbunden [{errorCode}]: {errorString}")
            self.connected = False
            self._schedule_reconnect()
        elif errorCode in [1100, 1101, 1102]:  # Connection lost
            logger.warning(f"[WARNUNG] TWS Verbindung verloren [{errorCode}]: {errorString}")
            self.connected = False
            self._schedule_reconnect()
        else:
            logger.warning(f"TWS Error [{errorCode}] Req {reqId}: {errorString}")

    def _schedule_reconnect(self):
        """Plant automatische Wiederverbindung."""
        current_time = time.time()
        if current_time - self.last_reconnect_attempt > self.reconnect_delay:
            self.last_reconnect_attempt = current_time
            import threading
            reconnect_thread = threading.Thread(target=self._attempt_reconnect, daemon=True)
            reconnect_thread.start()

    def _attempt_reconnect(self):
        """Versucht Wiederverbindung mit exponentiellem Backoff."""
        if self.connection_attempts >= self.max_connection_attempts:
            logger.error(f"[FEHLER] Maximale Anzahl von {self.max_connection_attempts} Reconnect-Versuchen erreicht")
            return

        self.connection_attempts += 1
        delay = min(self.reconnect_delay * (2 ** (self.connection_attempts - 1)), 300)  # Max 5 Minuten

        logger.info(f"[RECONNECT] Versuche #{self.connection_attempts} in {delay}s...")

        time.sleep(delay)

        try:
            if self.connect_to_tws():
                logger.info("[RECONNECT] ✓ Erfolgreich wiederverbunden!")
                self.connection_attempts = 0  # Reset counter
            else:
                logger.warning(f"[RECONNECT] Versuch #{self.connection_attempts} fehlgeschlagen")
                self._attempt_reconnect()  # Rekursiv weiter versuchen
        except Exception as e:
            logger.error(f"[RECONNECT] Fehler bei Wiederverbindung: {e}")
            self._attempt_reconnect()

    def nextValidId(self, orderId: int):
        """Callback: Next valid order ID."""
        self.next_valid_order_id = orderId
        self.connected = True
        logger.info(f"[OK] TWS verbunden - Next Order ID: {orderId}")

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