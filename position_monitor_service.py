"""
Automatischer Position Monitor Service.
Läuft täglich und prüft alle offenen Positionen gegen Exit-Bedingungen.
"""

import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List

import config
import options_config as opt_config
from position_manager import PositionManager
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract

# Logging Setup
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/position_monitor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class PositionMonitor(EWrapper, EClient):
    """Monitort Options-Positionen automatisch via TWS API."""
    
    def __init__(self):
        EClient.__init__(self, self)
        self.position_manager = PositionManager()
        
        self.host = config.IB_HOST
        self.port = config.IB_PORT
        self.client_id = 3  # Unterschiedliche Client ID
        
        self.connected = False
        self.next_order_id = None
        
        # Cache für Marktdaten
        self.market_data_cache = {}
        self.pending_requests = {}
        self.request_id_counter = 1
        
        logger.info("[OK] Position Monitor initialisiert")
    
    # ========================================================================
    # TWS CALLBACKS
    # ========================================================================
    
    def nextValidId(self, orderId: int):
        """Callback wenn Verbindung steht."""
        super().nextValidId(orderId)
        self.next_order_id = orderId
        self.connected = True
        logger.info(f"[OK] TWS verbunden - Next Order ID: {orderId}")
    
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        """Error Handler."""
        if errorCode in [2104, 2106, 2158]:
            logger.info(f"TWS Info [{errorCode}]: {errorString}")
        elif errorCode in [200, 354]:
            logger.warning(f"[WARNUNG] TWS [{errorCode}]: {errorString}")
        else:
            logger.error(f"[FEHLER] TWS Error {errorCode}: {errorString}")
    
    def tickPrice(self, reqId, tickType, price, attrib):
        """Empfängt Preis-Updates."""
        if reqId in self.pending_requests:
            req_data = self.pending_requests[reqId]
            
            # TickType 4 = LAST (letzter Preis)
            if tickType == 4:
                req_data['last_price'] = price
                logger.debug(f"[DEBUG] {req_data.get('symbol')}: Last Price = ${price:.2f}")
    
    def tickOptionComputation(self, reqId, tickType, tickAttrib, 
                             impliedVol, delta, optPrice, pvDividend,
                             gamma, vega, theta, undPrice):
        """Empfängt Options-Greeks."""
        if reqId in self.pending_requests:
            req_data = self.pending_requests[reqId]
            
            if tickType == 13:  # Model Option
                req_data['option_price'] = optPrice
                req_data['underlying_price'] = undPrice
                req_data['delta'] = delta
                
                logger.debug(f"[DEBUG] {req_data.get('symbol')}: " +
                           f"Option=${optPrice:.2f} Underlying=${undPrice:.2f} Delta={delta:.3f}")
    
    # ========================================================================
    # MARKTDATEN REQUESTS
    # ========================================================================
    
    def request_market_data(self, symbol: str, strike: float, right: str, expiry: str):
        """Requested Underlying + Option Preis."""
        req_id = self.request_id_counter
        self.request_id_counter += 1
        
        # Create Options Contract
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "OPT"
        contract.exchange = "SMART"
        contract.currency = "USD"
        contract.strike = strike
        contract.right = right
        contract.lastTradeDateOrContractMonth = expiry
        
        self.pending_requests[req_id] = {
            'symbol': symbol,
            'strike': strike,
            'right': right,
            'expiry': expiry,
            'contract_type': 'OPTION'
        }
        
        # Request Market Data
        self.reqMktData(req_id, contract, "", False, False, [])
        logger.debug(f"[DEBUG] Request Market Data: {symbol} {strike}{right} {expiry}")
        
        return req_id
    
    def wait_for_data(self, timeout: int = 10):
        """Wartet auf Marktdaten."""
        start_time = time.time()
        while (time.time() - start_time) < timeout:
            time.sleep(0.5)
    
    # ========================================================================
    # POSITION MONITORING
    # ========================================================================
    
    def monitor_all_positions(self):
        """Prüft alle offenen Positionen."""
        logger.info("\n" + "="*70)
        logger.info(f"  POSITION MONITOR - {datetime.now()}")
        logger.info("="*70)
        
        positions = self.position_manager.get_all_open_positions()
        
        if not positions:
            logger.info("[INFO] Keine offenen Positionen zum Monitoren")
            return
        
        logger.info(f"[INFO] {len(positions)} offene Position(en) gefunden")
        
        for position in positions:
            try:
                logger.info(f"\nPrüfe Position [{position['id']}] {position['symbol']}...")
                
                # Request aktuelle Marktdaten
                req_id = self.request_market_data(
                    position['symbol'],
                    position['strike'],
                    position['right'],
                    position['expiry']
                )
                
                # Warte auf Daten
                self.wait_for_data(timeout=5)
                
                # Hole Daten aus Cache
                if req_id in self.pending_requests:
                    data = self.pending_requests[req_id]
                    
                    current_option_price = data.get('option_price', position.get('current_premium', 0))
                    current_underlying_price = data.get('underlying_price', position.get('current_underlying_price', 0))
                    
                    if current_option_price and current_underlying_price:
                        # Update Position
                        result = self.position_manager.update_position(
                            position['id'],
                            current_option_price,
                            current_underlying_price
                        )
                        
                        logger.info(f"  Status: {result['status']}")
                        logger.info(f"  Option: ${current_option_price:.2f}")
                        logger.info(f"  Underlying: ${current_underlying_price:.2f}")
                        logger.info(f"  P&L: ${result['pnl']:.2f} ({result['pnl_pct']:+.1f}%)")
                        logger.info(f"  DTE: {result['current_dte']}")
                        
                        # Auto-Close bei Exit-Bedingung
                        if result['exit_reason']:
                            logger.warning(f"  [ALERT] Exit-Bedingung: {result['exit_reason']}")
                            # Optional: Automatisches Schließen aktivieren
                            # self.position_manager.close_position(position['id'], result['exit_reason'])
                    else:
                        logger.warning(f"  [WARNUNG] Keine Marktdaten verfügbar")
                
                # Cancel Market Data
                self.cancelMktData(req_id)
                
                time.sleep(2)  # Rate Limiting
                
            except Exception as e:
                logger.error(f"[FEHLER] Fehler bei Position {position['id']}: {e}", exc_info=True)
        
        # Portfolio Summary
        logger.info("\n" + "="*70)
        self.position_manager.print_portfolio_summary()
        logger.info("="*70 + "\n")
    
    # ========================================================================
    # SERVICE CONTROL
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
                logger.error("[FEHLER] TWS Verbindung Timeout")
                return False
                
        except Exception as e:
            logger.error(f"[FEHLER] TWS Verbindungsfehler: {e}")
            return False
    
    def disconnect_from_tws(self):
        """Trennt Verbindung."""
        if self.connected:
            self.disconnect()
            logger.info("[OK] TWS Verbindung getrennt")
    
    def run_service(self, interval_hours: int = 24):
        """Läuft kontinuierlich mit festem Intervall."""
        logger.info(f"\n{'='*70}")
        logger.info(f"  POSITION MONITOR SERVICE GESTARTET")
        logger.info(f"{'='*70}")
        logger.info(f"Monitoring-Intervall: {interval_hours} Stunden")
        logger.info(f"{'='*70}\n")
        
        while True:
            try:
                # Monitor Positionen
                self.monitor_all_positions()
                
                # Warte bis nächster Check
                next_run = datetime.now() + timedelta(hours=interval_hours)
                logger.info(f"Naechster Check: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
                
                time.sleep(interval_hours * 3600)
                
            except KeyboardInterrupt:
                logger.info("\n[OK] Service wird beendet...")
                break
            except Exception as e:
                logger.error(f"[FEHLER] Service Error: {e}", exc_info=True)
                time.sleep(300)  # 5 Minuten Pause bei Fehler


def main():
    """Startet Position Monitor Service."""
    import os
    os.makedirs('logs', exist_ok=True)
    
    print("\n" + "="*70)
    print("  TWS POSITION MONITOR SERVICE")
    print("="*70)
    print(f"  TWS: {config.IB_HOST}:{config.IB_PORT}")
    print(f"  Client ID: 3")
    print("="*70 + "\n")
    
    monitor = PositionMonitor()
    
    if not monitor.connect_to_tws():
        logger.error("[FEHLER] TWS Verbindung fehlgeschlagen!")
        return
    
    # Starte Service
    try:
        monitor.run_service(interval_hours=1)  # Stündliches Monitoring
    finally:
        monitor.disconnect_from_tws()


if __name__ == "__main__":
    main()
