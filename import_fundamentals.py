"""
Importiert Fundamentaldaten aus watchlist.csv in die Datenbank.
Führt eine einmalige Migration durch, um die Optionsstrategie zu aktivieren.
"""

import pandas as pd
import logging
from database import DatabaseManager
import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def import_fundamentals_from_watchlist():
    """Importiert alle Fundamentaldaten aus watchlist.csv in die DB."""
    
    db = DatabaseManager()
    
    # Lade Watchlist CSV
    try:
        watchlist_df = pd.read_csv('watchlist.csv')
        logger.info(f"✓ Watchlist geladen: {len(watchlist_df)} Symbole")
    except Exception as e:
        logger.error(f"✗ Fehler beim Laden der Watchlist: {e}")
        return False
    
    success_count = 0
    error_count = 0
    
    for _, row in watchlist_df.iterrows():
        symbol = row['symbol']
        
        # Erstelle Fundamentaldaten-Dict
        fundamental_data = {
            'market_cap': float(row.get('market_cap', 0) or 0),
            'avg_volume': float(row.get('avg_volume', 0) or 0),
            'sector': str(row.get('sector', '')),
            'industry': str(row.get('industry', '')),
            'pe_ratio': float(row.get('pe_ratio', 0) or 0),
            'fcf': float(row.get('fcf', 0) or 0),
            # Für Optionsstrategie wichtige Felder (vorerst Defaults)
            'sector_pe_median': 0.0,  # TODO: Branchen-Median berechnen
            'current_iv': 0.0,         # TODO: Von IB API oder anderen Quellen laden
            'iv_rank': 50.0            # Default Mittelwert
        }
        
        # Speichere in DB
        if db.save_fundamental_data(symbol, fundamental_data):
            success_count += 1
            logger.info(f"  ✓ {symbol}: Market Cap ${fundamental_data['market_cap']/1e9:.2f}B, P/E {fundamental_data['pe_ratio']:.2f}")
        else:
            error_count += 1
            logger.error(f"  ✗ {symbol}: Fehler beim Speichern")
    
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
        watchlist_df = pd.read_csv('watchlist.csv')
        
        # Gruppiere nach Sektor und berechne Median
        sector_medians = watchlist_df.groupby('sector')['pe_ratio'].median().to_dict()
        
        logger.info(f"\n{'='*60}")
        logger.info("Branchen-P/E-Mediane:")
        for sector, median in sector_medians.items():
            if pd.notna(median) and median > 0:
                logger.info(f"  {sector}: {median:.2f}")
        logger.info(f"{'='*60}\n")
        
        # Update alle Symbole mit ihrem Branchen-Median
        success_count = 0
        for _, row in watchlist_df.iterrows():
            symbol = row['symbol']
            sector = row.get('sector', '')
            sector_median = sector_medians.get(sector, 0.0)
            
            if pd.notna(sector_median) and sector_median > 0:
                # Lade bestehende Daten
                fundamental_data = db.get_fundamental_data(symbol)
                if fundamental_data:
                    fundamental_data['sector_pe_median'] = float(sector_median)
                    if db.save_fundamental_data(symbol, fundamental_data):
                        success_count += 1
        
        logger.info(f"✓ Branchen-Mediane für {success_count} Symbole aktualisiert")
        return True
        
    except Exception as e:
        logger.error(f"✗ Fehler beim Berechnen der Branchen-Mediane: {e}")
        return False


if __name__ == '__main__':
    logger.info("="*60)
    logger.info(" FUNDAMENTALDATEN-IMPORT")
    logger.info("="*60)
    
    # 1. Import Fundamentaldaten
    logger.info("\n1. Importiere Fundamentaldaten aus watchlist.csv...")
    import_fundamentals_from_watchlist()
    
    # 2. Berechne Branchen-Mediane
    logger.info("\n2. Berechne Branchen-P/E-Mediane...")
    calculate_sector_medians()
    
    logger.info("\n✓ Import abgeschlossen! Optionsstrategie ist jetzt einsatzbereit.")
    logger.info("  Starte den Bot neu, um die Optionsstrategie zu verwenden.")
