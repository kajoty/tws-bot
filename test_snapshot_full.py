#!/usr/bin/env python3
"""
Test-Script um die vollständige ReportSnapshot XML-Struktur zu sehen.
"""
import logging
import time
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TestApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.nextValidOrderId = None
        self.fundamental_data_received = False
        self.full_xml = ""
        
    def nextValidId(self, orderId: int):
        """Callback wenn Verbindung steht."""
        logger.info(f"Connected! Next valid ID: {orderId}")
        self.nextValidOrderId = orderId
        
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        """Error handler."""
        logger.error(f"Error {errorCode}: {errorString}")
        
    def fundamentalData(self, reqId, data):
        """Callback für Fundamentaldaten."""
        logger.info(f"\n=== FULL XML FOR ReportSnapshot ===")
        logger.info(f"Length: {len(data)} bytes\n")
        logger.info(data)  # Vollständige XML ausgeben
        logger.info("\n=== END XML ===\n")
        self.full_xml = data
        self.fundamental_data_received = True

def main():
    symbol = "AAPL"
    port = 7496  # Live trading
    
    app = TestApp()
    
    # Verbindung zu TWS
    logger.info(f"Connecting to TWS on localhost:{port}...")
    app.connect("127.0.0.1", port, clientId=999)
    
    # Thread starten
    import threading
    api_thread = threading.Thread(target=app.run, daemon=True)
    api_thread.start()
    
    # Warten auf Verbindung
    timeout = 10
    start = time.time()
    while app.nextValidOrderId is None:
        time.sleep(0.1)
        if time.time() - start > timeout:
            logger.error("Connection timeout!")
            return
    
    # Contract erstellen
    contract = Contract()
    contract.symbol = symbol
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    
    logger.info(f"\n{'='*70}")
    logger.info(f"Requesting ReportSnapshot for {symbol}...")
    logger.info(f"{'='*70}\n")
    
    # ReportSnapshot anfordern
    app.reqFundamentalData(
        reqId=1,
        contract=contract,
        reportType="ReportSnapshot",  # Bester Report-Typ
        fundamentalDataOptions=[]
    )
    
    # Warten auf Daten
    timeout = 10
    start = time.time()
    while not app.fundamental_data_received:
        time.sleep(0.1)
        if time.time() - start > timeout:
            logger.warning("⚠ Timeout beim Warten auf Fundamentaldaten")
            break
    
    time.sleep(1)
    app.disconnect()
    
    if app.full_xml:
        # XML in Datei speichern für bessere Analyse
        output_file = f"snapshot_{symbol}.xml"
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(app.full_xml)
        logger.info(f"\n✓ XML gespeichert in: {output_file}")

if __name__ == "__main__":
    main()
