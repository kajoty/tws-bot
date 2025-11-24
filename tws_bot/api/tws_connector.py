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
        
        # Portfolio/Account Daten
        self.account_data = {}
        self.portfolio_positions = []
        self.account_data_complete = False

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

    def managedAccounts(self, accountsList: str):
        """Callback: Liste der verwalteten Accounts."""
        self.managed_accounts = accountsList.split(',')
        logger.info(f"[OK] Managed Accounts: {self.managed_accounts}")

    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str):
        """Callback: Account Summary Daten."""
        if reqId not in self.pending_requests:
            return
        
        if 'account_data' not in self.pending_requests[reqId]:
            self.pending_requests[reqId]['account_data'] = {}
        
        self.pending_requests[reqId]['account_data'][tag] = {
            'value': value,
            'currency': currency
        }

    def accountSummaryEnd(self, reqId: int):
        """Callback: Ende der Account Summary Daten."""
        if reqId in self.pending_requests:
            self.pending_requests[reqId]['completed'] = True

    def position(self, account: str, contract, position: float, avgCost: float):
        """Callback: Portfolio Position."""
        if not hasattr(self, 'current_positions_req'):
            return
        
        req_id = self.current_positions_req
        
        if req_id not in self.pending_requests:
            return
        
        if 'positions' not in self.pending_requests[req_id]:
            self.pending_requests[req_id]['positions'] = []
        
        position_data = {
            'account': account,
            'symbol': contract.symbol,
            'secType': contract.secType,
            'position': position,
            'avgCost': avgCost,
            'marketValue': position * avgCost if avgCost > 0 else 0
        }
        
        self.pending_requests[req_id]['positions'].append(position_data)

    def positionEnd(self):
        """Callback: Ende der Position-Daten."""
        if hasattr(self, 'current_positions_req'):
            req_id = self.current_positions_req
            if req_id in self.pending_requests:
                self.pending_requests[req_id]['completed'] = True

    def updateAccountValue(self, key: str, val: str, currency: str, accountName: str):
        """Callback: Account Value Daten von reqAccountUpdates."""
        # Speichere in globalem Account Data Cache
        if accountName not in self.account_data:
            self.account_data[accountName] = {}
        
        self.account_data[accountName][key] = {
            'value': val,
            'currency': currency
        }

    def updatePortfolio(self, contract, position: float, marketPrice: float, marketValue: float, averageCost: float, unrealizedPNL: float, realizedPNL: float, accountName: str):
        """Callback: Portfolio Position Daten von reqAccountUpdates."""
        # Speichere Position in Portfolio-Liste
        position_data = {
            'account': accountName,
            'symbol': contract.symbol,
            'secType': contract.secType,
            'position': position,
            'marketPrice': marketPrice,
            'marketValue': marketValue,
            'avgCost': averageCost,
            'unrealizedPNL': unrealizedPNL,
            'realizedPNL': realizedPNL
        }
        
        # Aktualisiere oder füge Position hinzu
        existing_index = None
        for i, pos in enumerate(self.portfolio_positions):
            if pos['symbol'] == contract.symbol and pos['account'] == accountName:
                existing_index = i
                break
        
        if existing_index is not None:
            self.portfolio_positions[existing_index] = position_data
        else:
            self.portfolio_positions.append(position_data)

    def accountDownloadEnd(self, accountName: str):
        """Callback: Account Download abgeschlossen."""
        logger.info(f"[OK] Account Daten Download abgeschlossen für {accountName}")
        # Setze Flag, dass Daten vollständig sind
        self.account_data_complete = True

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

    def request_account_summary(self) -> int:
        """
        Fordert Account Summary Daten an.

        Returns:
            Request ID
        """
        if not self.connected:
            logger.error("[FEHLER] Nicht mit TWS verbunden")
            return None

        # Verwende reqAccountUpdates für kontinuierliche Account-Daten
        # Dies löst accountValue Callbacks aus
        self.reqAccountUpdates(True, "")

        # Warte kurz, damit Callbacks eintreffen
        import time
        time.sleep(2)

        return 0  # Dummy ID, da reqAccountUpdates keine Request ID verwendet

    def request_portfolio_positions(self) -> int:
        """
        Fordert Portfolio-Positionen an.
        
        Returns:
            Request ID
        """
        if not self.connected:
            logger.error("[FEHLER] Nicht mit TWS verbunden")
            return None
        
        req_id = self.request_id_counter
        self.request_id_counter += 1
        
        self.pending_requests[req_id] = {
            'type': 'portfolio_positions',
            'completed': False,
            'positions': []
        }
        
        self.current_positions_req = req_id
        self.reqPositions()
        
        return req_id

    def get_portfolio_data(self) -> dict:
        """
        Ruft komplette Portfolio-Daten ab.

        Returns:
            Dictionary mit Portfolio-Informationen
        """
        if not self.connected:
            logger.error("[FEHLER] Nicht mit TWS verbunden")
            return {}

        # Account Updates anfordern (löst accountValue Callbacks aus)
        self.account_data_complete = False  # Reset Flag
        account_req_id = self.request_account_summary()

        # Warte auf Account Daten (max 5 Sekunden)
        timeout = 5
        start_time = time.time()
        while not self.account_data_complete and (time.time() - start_time) < timeout:
            time.sleep(0.1)

        if not self.account_data_complete:
            logger.warning("[WARNUNG] Account Daten nicht vollständig empfangen")

        # Portfolio Positionen anfordern
        positions_req_id = self.request_portfolio_positions()
        if positions_req_id:
            self.wait_for_request(positions_req_id, timeout=10)

        # Daten zusammenstellen
        portfolio_data = {}

        # Account Daten aus accountValue Callback (zuverlässiger)
        account_name = self.managed_accounts[0] if self.managed_accounts else None
        if account_name and account_name in self.account_data:
            account_info = self.account_data[account_name]
            portfolio_data.update({
                'net_liquidation': float(account_info.get('NetLiquidation', {}).get('value', '0')),
                'total_cash': float(account_info.get('TotalCashValue', {}).get('value', '0')),
                'buying_power': float(account_info.get('BuyingPower', {}).get('value', '0')),
                'available_funds': float(account_info.get('AvailableFunds', {}).get('value', '0')),
                'cushion': float(account_info.get('Cushion', {}).get('value', '0'))
            })
            logger.info(f"[DEBUG] Account Daten gefunden für {account_name}: {len(account_info)} Felder")
        else:
            logger.warning(f"[DEBUG] Keine Account Daten gefunden. Accounts: {self.managed_accounts}, account_data keys: {list(self.account_data.keys())}")

        # Portfolio Positionen aus updatePortfolio Callbacks (zuverlässiger)
        positions = []
        for pos in self.portfolio_positions:
            if pos.get('position', 0) != 0:
                # Normalisiere Feldnamen für Konsistenz
                normalized_pos = {
                    'symbol': pos.get('symbol', ''),
                    'position': pos.get('position', 0),
                    'marketValue': pos.get('marketValue', 0),
                    'avgCost': pos.get('averageCost', 0),  # Normalisiere von averageCost
                    'unrealizedPNL': pos.get('unrealizedPNL', 0),
                    'marketPrice': pos.get('marketPrice', 0)
                }
                positions.append(normalized_pos)
        
        portfolio_data['positions'] = positions

        # Berechne Portfolio-Wert
        total_value = sum(pos.get('marketValue', 0) for pos in positions)
        portfolio_data['portfolio_value'] = total_value

        # Berechne Anzahl Positionen
        portfolio_data['num_positions'] = len(positions)

        return portfolio_data