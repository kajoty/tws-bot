"""
Test-Script um Fundamentaldaten von TWS abzurufen.
"""
import sys
import time
import logging
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
import threading

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class FundamentalTest(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.fundamental_data = None
        self.connected = False
        self.next_valid_id = None
        
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        logger.error(f"Error {errorCode}: {errorString}")
        
    def nextValidId(self, orderId: int):
        logger.info(f"Connected! Next valid ID: {orderId}")
        self.next_valid_id = orderId
        self.connected = True
        
    def fundamentalData(self, reqId: int, data: str):
        logger.info(f"\n=== FUNDAMENTAL DATA RECEIVED (Length: {len(data)} bytes) ===")
        logger.info(f"First 1000 chars:\n{data[:1000]}")
        
        # Suche nach interessanten Keywords im XML
        keywords = ['PERatio', 'MarketCap', 'FreeCashFlow', 'EPS', 'Revenue', 
                   'EBITDA', 'DebtToEquity', 'ROE', 'DividendYield', 'Beta',
                   'BookValue', 'PriceToBook', 'Earnings', 'Sector', 'Industry']
        
        found_keywords = [kw for kw in keywords if kw in data]
        if found_keywords:
            logger.info(f"\n✓ Gefundene Keywords: {', '.join(found_keywords)}")
        else:
            logger.warning(f"\n⚠ KEINE der gewünschten Keywords gefunden!")
        
        # Speichere für Analyse
        self.fundamental_data = data
        
    def test_fundamental_request(self, symbol: str):
        # Warte auf Verbindung
        timeout = 10
        start = time.time()
        while not self.connected and time.time() - start < timeout:
            time.sleep(0.1)
            
        if not self.connected:
            logger.error("Timeout: Keine Verbindung zu TWS")
            return False
            
        # Erstelle Contract
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        
        req_id = 1
        
        logger.info(f"\n{'='*70}")
        logger.info(f"Requesting Fundamental Data for {symbol}...")
        logger.info(f"{'='*70}\n")
        
        # Request verschiedene Report-Typen
        report_types = [
            "ReportSnapshot",  # Company Snapshot - WICHTIGSTER!
            "ReportsFinStatements",  # Financial Statements
            "ReportsFinSummary",  # Financial Summary
            "CalendarReport",  # Calendar/Events
            "ReportsOwnership",  # Ownership
            "RESC"  # Analyst Estimates
        ]
        
        for i, report_type in enumerate(report_types):
            logger.info(f"\n{'='*70}")
            logger.info(f"--- Requesting {report_type} ---")
            logger.info(f"{'='*70}")
            self.fundamental_data = None  # Reset für jeden Report
            self.reqFundamentalData(req_id + i, contract, report_type, [])
            
            # Warte auf Antwort
            wait_time = 5
            start = time.time()
            while self.fundamental_data is None and time.time() - start < wait_time:
                time.sleep(0.1)
            
            if self.fundamental_data:
                logger.info(f"\n✓ {report_type} lieferte {len(self.fundamental_data)} bytes")
            else:
                logger.warning(f"\n⚠ {report_type} lieferte KEINE Daten")
            
            time.sleep(1)  # Kurze Pause zwischen Requests
            
        # Warte auf Antwort
        time.sleep(10)
        
        return self.fundamental_data is not None


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_fundamental_fetch.py <SYMBOL>")
        print("Example: python test_fundamental_fetch.py AAPL")
        sys.exit(1)
        
    symbol = sys.argv[1]
    
    app = FundamentalTest()
    
    # Verbinde zu TWS
    logger.info("Connecting to TWS on localhost:7496...")
    app.connect("127.0.0.1", 7496, clientId=999)
    
    # Starte EClient thread
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    
    # Teste Fundamental Request
    app.test_fundamental_request(symbol)
    
    time.sleep(2)
    app.disconnect()
    
    logger.info("\n" + "="*70)
    logger.info("ZUSAMMENFASSUNG")
    logger.info("="*70)
    logger.info("Prüfe welcher Report-Typ die besten Fundamentaldaten liefert.")
    logger.info("Hinweis: ReportSnapshot sollte P/E, Market Cap, etc. enthalten.")


if __name__ == "__main__":
    main()
