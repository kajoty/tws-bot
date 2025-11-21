"""
Importiert Fundamentaldaten von Interactive Brokers.
Nutzt die IB Fundamentals API statt Yahoo Finance.
"""

import pandas as pd
import logging
import time
from typing import Dict, Optional
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from threading import Thread, Event
from database import DatabaseManager
import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class IBFundamentalsClient(EWrapper, EClient):
    """IB Client zum Abrufen von Fundamentaldaten."""
    
    def __init__(self):
        EWrapper.__init__(self)
        EClient.__init__(self, self)
        
        self.connected = False
        self.next_req_id = 1
        self.fundamental_data = {}
        self.pending_requests = {}
        self.connection_event = Event()
        
    def nextValidId(self, orderId: int):
        """Callback wenn Verbindung steht."""
        super().nextValidId(orderId)
        self.next_req_id = orderId
        self.connected = True
        self.connection_event.set()
        logger.info("✓ Verbunden mit TWS")
    
    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = ""):
        """Error Callback."""
        if errorCode in [2104, 2106, 2158]:  # Informational messages
            return
        if errorCode == 200:  # No security definition found
            logger.warning(f"  Symbol nicht gefunden (ReqId {reqId})")
            if reqId in self.pending_requests:
                self.pending_requests[reqId]['completed'] = True
                self.pending_requests[reqId]['error'] = True
        else:
            logger.warning(f"Error {errorCode}: {errorString}")
    
    def fundamentalData(self, reqId: int, data: str):
        """Callback für Fundamentaldaten."""
        if reqId in self.pending_requests:
            symbol = self.pending_requests[reqId]['symbol']
            report_type = self.pending_requests[reqId]['report_type']
            
            if symbol not in self.fundamental_data:
                self.fundamental_data[symbol] = {}
            
            self.fundamental_data[symbol][report_type] = data
            self.pending_requests[reqId]['completed'] = True
            logger.debug(f"  Daten erhalten: {symbol} ({report_type})")
    
    def connect_to_tws(self, timeout: int = 10) -> bool:
        """Verbindet mit TWS."""
        try:
            self.connect("127.0.0.1", config.IB_PORT, clientId=999)
            
            # Starte API Thread
            api_thread = Thread(target=self.run, daemon=True)
            api_thread.start()
            
            # Warte auf Verbindung
            if self.connection_event.wait(timeout):
                return True
            else:
                logger.error("Timeout beim Verbinden mit TWS")
                return False
                
        except Exception as e:
            logger.error(f"Fehler beim Verbinden: {e}")
            return False
    
    def get_fundamental_data_for_symbol(self, symbol: str) -> Optional[Dict]:
        """
        Holt Fundamentaldaten für ein Symbol von IB.
        
        Returns:
            Dict mit parsed fundamentals oder None
        """
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        
        # Request Financial Summary (enthält P/E, Market Cap, FCF etc.)
        req_id = self.next_req_id
        self.next_req_id += 1
        
        self.pending_requests[req_id] = {
            'symbol': symbol,
            'report_type': 'ReportSnapshot',
            'completed': False,
            'error': False
        }
        
        self.reqFundamentalData(req_id, contract, "ReportSnapshot", [])
        
        # Warte auf Antwort (max 5 Sekunden)
        timeout = 5
        start_time = time.time()
        while not self.pending_requests[req_id]['completed']:
            if time.time() - start_time > timeout:
                logger.warning(f"  Timeout für {symbol}")
                return None
            time.sleep(0.1)
        
        if self.pending_requests[req_id].get('error'):
            return None
        
        # Parse XML Daten
        if symbol in self.fundamental_data and 'ReportSnapshot' in self.fundamental_data[symbol]:
            return self._parse_fundamental_xml(symbol, self.fundamental_data[symbol]['ReportSnapshot'])
        
        return None
    
    def _parse_fundamental_xml(self, symbol: str, xml_data: str) -> Dict:
        """
        Parst IB XML Fundamentaldaten.
        
        IB liefert ReportSnapshot als XML mit Sections wie:
        - CoIDs: Company Info (market cap, sector, industry)
        - Ratios: P/E, etc.
        - Financial Statements: FCF, Revenue, etc.
        """
        import xml.etree.ElementTree as ET
        
        result = {
            'market_cap': 0.0,
            'pe_ratio': 0.0,
            'free_cash_flow': 0.0,
            'avg_volume_20d': 0.0,
            'sector': '',
            'industry': '',
            'revenue': 0.0,
            'earnings': 0.0
        }
        
        try:
            root = ET.fromstring(xml_data)
            
            # Market Cap (in CoIDs section)
            for coid in root.findall('.//CoID[@Type="MarketCap"]'):
                try:
                    result['market_cap'] = float(coid.text) * 1e6  # IB liefert in Millionen
                except:
                    pass
            
            # Sector & Industry
            for issue in root.findall('.//Issue'):
                sector = issue.find('Sector')
                if sector is not None and sector.text:
                    result['sector'] = sector.text
                industry = issue.find('Industry')
                if industry is not None and industry.text:
                    result['industry'] = industry.text
            
            # P/E Ratio (in Ratios section)
            for ratio in root.findall('.//Ratio[@FieldName="TTMPR"]'):  # TTM P/E
                try:
                    result['pe_ratio'] = float(ratio.text)
                except:
                    pass
            
            # Volume (Average)
            for ratio in root.findall('.//Ratio[@FieldName="AVOL"]'):
                try:
                    result['avg_volume_20d'] = float(ratio.text)
                except:
                    pass
            
            # Free Cash Flow (in Financial Statements)
            for stmt in root.findall('.//FiscalPeriod[@Type="Annual"]'):
                fcf_elem = stmt.find('.//lineItem[@coaCode="SFREECASHFLOW"]')
                if fcf_elem is not None:
                    try:
                        result['free_cash_flow'] = float(fcf_elem.text) * 1e6  # IB in Millionen
                    except:
                        pass
                
                # Revenue
                rev_elem = stmt.find('.//lineItem[@coaCode="SREV"]')
                if rev_elem is not None:
                    try:
                        result['revenue'] = float(rev_elem.text) * 1e6
                    except:
                        pass
                
                # Net Income
                ni_elem = stmt.find('.//lineItem[@coaCode="SNINC"]')
                if ni_elem is not None:
                    try:
                        result['earnings'] = float(ni_elem.text) * 1e6
                    except:
                        pass
            
            logger.debug(f"  Parsed {symbol}: MCap={result['market_cap']/1e9:.1f}B, P/E={result['pe_ratio']:.1f}")
            
        except Exception as e:
            logger.warning(f"  Fehler beim Parsen von {symbol}: {e}")
        
        return result


def import_fundamentals_from_ib():
    """Importiert Fundamentaldaten von Interactive Brokers."""
    
    # Lade Watchlist
    try:
        watchlist_df = pd.read_csv('watchlist.csv')
        symbols = watchlist_df['symbol'].tolist()
        logger.info(f"✓ Watchlist geladen: {len(symbols)} Symbole")
    except Exception as e:
        logger.error(f"✗ Fehler beim Laden der Watchlist: {e}")
        return False
    
    # Verbinde mit TWS
    client = IBFundamentalsClient()
    if not client.connect_to_tws():
        logger.error("✗ Konnte nicht mit TWS verbinden. Stelle sicher dass TWS/Gateway läuft.")
        return False
    
    # Warte kurz bis Verbindung stabil
    time.sleep(2)
    
    db = DatabaseManager()
    success_count = 0
    error_count = 0
    
    logger.info("\nStarte Fundamentaldaten-Import von IB...")
    logger.info("HINWEIS: Dies dauert ~10-15 Minuten für 503 Symbole (Rate Limit: 1 Request/Sekunde)\n")
    
    for idx, symbol in enumerate(symbols):
        logger.info(f"[{idx+1}/{len(symbols)}] {symbol}...")
        
        # Hole Fundamentals von IB
        fundamentals = client.get_fundamental_data_for_symbol(symbol)
        
        if fundamentals:
            # Ergänze mit Daten aus CSV (falls vorhanden)
            row = watchlist_df[watchlist_df['symbol'] == symbol].iloc[0]
            if fundamentals['market_cap'] == 0:
                fundamentals['market_cap'] = float(row.get('market_cap', 0) or 0)
            if fundamentals['sector'] == '':
                fundamentals['sector'] = str(row.get('sector', ''))
            if fundamentals['industry'] == '':
                fundamentals['industry'] = str(row.get('industry', ''))
            
            # Speichere
            if db.save_fundamental_data(symbol, fundamentals):
                success_count += 1
                logger.info(f"  ✓ {symbol}: MCap ${fundamentals['market_cap']/1e9:.2f}B, P/E {fundamentals['pe_ratio']:.2f}, FCF ${fundamentals['free_cash_flow']/1e9:.2f}B")
            else:
                error_count += 1
                logger.error(f"  ✗ {symbol}: Fehler beim Speichern")
        else:
            error_count += 1
            logger.warning(f"  ✗ {symbol}: Keine Daten von IB erhalten")
        
        # Rate Limit: 1 Request pro Sekunde
        time.sleep(1.1)
    
    client.disconnect()
    
    logger.info(f"\n{'='*60}")
    logger.info(f"Import abgeschlossen:")
    logger.info(f"  Erfolgreich: {success_count}")
    logger.info(f"  Fehler: {error_count}")
    logger.info(f"{'='*60}")
    
    return True


def calculate_sector_medians():
    """Berechnet Branchen-Mediane für P/E Ratios."""
    
    db = DatabaseManager()
    
    try:
        cursor = db.conn.cursor()
        cursor.execute("SELECT symbol, sector, pe_ratio FROM fundamental_data WHERE pe_ratio > 0")
        all_fundamentals = pd.DataFrame(cursor.fetchall(), columns=['symbol', 'sector', 'pe_ratio'])
        
        if all_fundamentals.empty:
            logger.warning("Keine Fundamentaldaten in der DB.")
            return False
        
        # Gruppiere nach Sektor
        sector_medians = all_fundamentals.groupby('sector')['pe_ratio'].median().to_dict()
        
        logger.info(f"\n{'='*60}")
        logger.info("Branchen-P/E-Mediane:")
        for sector, median in sector_medians.items():
            if pd.notna(median) and median > 0:
                logger.info(f"  {sector}: {median:.2f}")
        logger.info(f"{'='*60}\n")
        
        # Update alle Symbole
        success_count = 0
        cursor.execute("SELECT * FROM fundamental_data")
        columns = [desc[0] for desc in cursor.description]
        all_data = pd.DataFrame(cursor.fetchall(), columns=columns)
        
        for _, row in all_data.iterrows():
            symbol = row['symbol']
            sector = row.get('sector', '')
            sector_median = sector_medians.get(sector, 0.0)
            
            if pd.notna(sector_median) and sector_median > 0:
                update_data = row.to_dict()
                update_data['sector_median_pe'] = float(sector_median)
                if db.save_fundamental_data(symbol, update_data):
                    success_count += 1
        
        logger.info(f"✓ Branchen-Mediane für {success_count} Symbole aktualisiert")
        return True
        
    except Exception as e:
        logger.error(f"✗ Fehler beim Berechnen der Branchen-Mediane: {e}")
        return False


if __name__ == '__main__':
    logger.info("="*60)
    logger.info(" FUNDAMENTALDATEN-IMPORT VON INTERACTIVE BROKERS")
    logger.info("="*60)
    logger.info("\n1. Importiere Fundamentaldaten von IB...")
    
    if import_fundamentals_from_ib():
        logger.info("\n2. Berechne Branchen-Mediane...")
        calculate_sector_medians()
        
        logger.info("\n✓ IMPORT ABGESCHLOSSEN")
        logger.info("\nNächste Schritte:")
        logger.info("  1. python backtest_criteria.py  # Prüfe ob Daten jetzt vollständig sind")
        logger.info("  2. python main.py              # Starte Trading Bot")
    else:
        logger.error("\n✗ IMPORT FEHLGESCHLAGEN")
        logger.info("\nTroubleshooting:")
        logger.info("  - Ist TWS/Gateway gestartet?")
        logger.info("  - Ist API aktiviert? (TWS: Global Config > API > Enable ActiveX)")
        logger.info(f"  - Richtiger Port? (Paper: 7497, Live: 7496, aktuell: {config.IB_PORT})")
