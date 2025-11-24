#!/usr/bin/env python3
"""
Test der Fundamentaldaten-Integration im Options-Scanner.
"""
import logging
import time
import sys
import os
from datetime import datetime

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Importiere Scanner
from options_scanner import OptionsScanner
import config

def main():
    """Teste Fundamentaldaten-Abruf für ein Symbol."""
    
    symbol = "AAPL"
    
    scanner = OptionsScanner(
        host=config.IB_HOST,
        port=config.IB_PORT,
        client_id=998
    )
    
    # Verbindung starten
    logger.info("Starte TWS-Verbindung...")
    import threading
    api_thread = threading.Thread(target=scanner.run, daemon=True)
    api_thread.start()
    
    # Warten auf Verbindung
    timeout = 10
    start = time.time()
    while not scanner.connected:
        time.sleep(0.1)
        if time.time() - start > timeout:
            logger.error("Connection timeout!")
            return
    
    logger.info(f"\n{'='*70}")
    logger.info(f"Teste Fundamentaldaten-Abruf für {symbol}")
    logger.info(f"{'='*70}\n")
    
    # Request fundamental data
    scanner.request_fundamental_data(symbol)
    
    # Warten auf Antwort
    timeout = 10
    start = time.time()
    while symbol not in scanner.fundamental_data_cache:
        time.sleep(0.1)
        if time.time() - start > timeout:
            logger.warning("⚠ Timeout beim Warten auf Fundamentaldaten")
            break
    
    time.sleep(1)
    
    # Ergebnis prüfen
    if symbol in scanner.fundamental_data_cache:
        data = scanner.fundamental_data_cache[symbol]
        
        logger.info(f"\n{'='*70}")
        logger.info("FUNDAMENTALDATEN ERHALTEN:")
        logger.info(f"{'='*70}")
        
        for key, value in data.items():
            if value is not None:
                if key == 'market_cap':
                    logger.info(f"  {key:15s}: ${value:,.0f} (${value/1e9:.2f}B)")
                elif key == 'fcf':
                    logger.info(f"  {key:15s}: ${value:,.0f}")
                elif key == 'avg_volume':
                    logger.info(f"  {key:15s}: {value:,.0f} Aktien")
                elif key == 'pe_ratio':
                    logger.info(f"  {key:15s}: {value:.2f}")
                else:
                    logger.info(f"  {key:15s}: {value}")
            else:
                logger.info(f"  {key:15s}: None")
        
        logger.info("\n✓ Fundamentaldaten erfolgreich abgerufen!")
        
        # Prüfe Datenbank
        db_data = scanner.db.get_fundamental_data(symbol)
        if db_data:
            logger.info(f"\n✓ Daten auch in Datenbank gespeichert!")
            logger.info(f"  Timestamp: {db_data.get('timestamp', 'N/A')}")
        else:
            logger.warning("\n⚠ Daten NICHT in Datenbank gefunden!")
    else:
        logger.error("\n✗ Keine Fundamentaldaten erhalten!")
    
    # Disconnect
    scanner.disconnect()
    logger.info("\nTest abgeschlossen.")


if __name__ == "__main__":
    main()
