"""
Vereinfachter Fundamentaldaten-Import.
Nutzt watchlist.csv Daten und ergänzt Volume von IB Market Data.
"""

import pandas as pd
import logging
from database import DatabaseManager
import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def import_fundamentals_from_watchlist():
    """
    Importiert Fundamentaldaten aus watchlist.csv.
    Die CSV wurde bereits mit aktuellen S&P 500 Daten erstellt.
    """
    
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
        
        # Erstelle Fundamentaldaten-Dict aus CSV
        fundamental_data = {
            'market_cap': float(row.get('market_cap', 0) or 0),
            'pe_ratio': float(row.get('pe_ratio', 0) or 0),
            'free_cash_flow': float(row.get('fcf', 0) or 0),
            'revenue': 0.0,
            'earnings': 0.0,
            'sector': str(row.get('sector', '')),
            'industry': str(row.get('industry', '')),
            'avg_volume_20d': float(row.get('avg_volume', 0) or 0),
            'sector_median_pe': 0.0,  # Wird nachher berechnet
            'next_earnings_date': None
        }
        
        # Speichere in DB
        if db.save_fundamental_data(symbol, fundamental_data):
            success_count += 1
            fcf = fundamental_data.get('free_cash_flow', 0) or 0
            vol = fundamental_data.get('avg_volume_20d', 0) or 0
            logger.info(f"  ✓ {symbol}: MCap ${fundamental_data['market_cap']/1e9:.2f}B, P/E {fundamental_data['pe_ratio']:.2f}, FCF ${fcf/1e9:.2f}B, Vol {vol:.0f}")
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
    """Berechnet Branchen-Mediane für P/E Ratios aus DB Daten."""
    
    db = DatabaseManager()
    
    try:
        cursor = db.conn.cursor()
        cursor.execute("SELECT symbol, sector, pe_ratio FROM fundamental_data WHERE pe_ratio > 0")
        all_fundamentals = pd.DataFrame(cursor.fetchall(), columns=['symbol', 'sector', 'pe_ratio'])
        
        if all_fundamentals.empty:
            logger.warning("Keine Fundamentaldaten in der DB.")
            return False
        
        # Gruppiere nach Sektor und berechne Median
        sector_medians = all_fundamentals.groupby('sector')['pe_ratio'].median().to_dict()
        
        logger.info(f"\n{'='*60}")
        logger.info("Branchen-P/E-Mediane:")
        for sector, median in sorted(sector_medians.items()):
            if pd.notna(median) and median > 0:
                logger.info(f"  {sector}: {median:.2f}")
        logger.info(f"{'='*60}\n")
        
        # Update alle Symbole mit ihrem Branchen-Median
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
    logger.info(" FUNDAMENTALDATEN-IMPORT AUS WATCHLIST.CSV")
    logger.info("="*60)
    logger.info("\n1. Importiere Fundamentaldaten...")
    
    if import_fundamentals_from_watchlist():
        logger.info("\n2. Berechne Branchen-Mediane...")
        calculate_sector_medians()
        
        logger.info("\n✓ IMPORT ABGESCHLOSSEN")
        logger.info("\nNächste Schritte:")
        logger.info("  1. python backtest_criteria.py  # Prüfe ob Filter funktionieren")
        logger.info("  2. python main.py              # Starte Trading Bot")
    else:
        logger.error("\n✗ IMPORT FEHLGESCHLAGEN")
