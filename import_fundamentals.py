"""
Importiert Fundamentaldaten aus watchlist.csv in die Datenbank.
Führt eine einmalige Migration durch, um die Optionsstrategie zu aktivieren.
"""

import pandas as pd
import logging
import yfinance as yf
from database import DatabaseManager
import config
from datetime import datetime, timedelta




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
            'sector_pe_median': 0.0, # Wird später berechnet
            'next_earnings_date': None # Wird von yfinance geholt
        }

        # Hole fehlende Daten von yfinance
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            
            # Prüfe ob wir gültige Daten haben
            if not info or 'symbol' not in info:
                raise ValueError("Keine gültigen Daten von yfinance")

            # Avg Volume (20d)
            fundamental_data['avg_volume_20d'] = info.get('averageVolume10days', 0) or info.get('averageVolume', 0) or 0

            # Free Cash Flow
            if 'freeCashflow' in info and info['freeCashflow']:
                fundamental_data['free_cash_flow'] = float(info['freeCashflow'])
            elif 'totalCashFromOperatingActivities' in info and 'capitalExpenditures' in info:
                fundamental_data['free_cash_flow'] = float(info['totalCashFromOperatingActivities']) - float(info['capitalExpenditures'])
            else:
                fundamental_data['free_cash_flow'] = 0.0

            # Next Earnings Date (calendar can be dict or DataFrame)
            try:
                calendar = ticker.calendar
                if isinstance(calendar, pd.DataFrame) and 'Earnings Date' in calendar.index:
                    earnings_date = calendar.loc['Earnings Date'].iloc[0]
                    if pd.notna(earnings_date):
                        fundamental_data['next_earnings_date'] = earnings_date.isoformat()
                elif isinstance(calendar, dict) and 'Earnings Date' in calendar:
                    earnings_dates = calendar['Earnings Date']
                    if isinstance(earnings_dates, list) and len(earnings_dates) > 0:
                        fundamental_data['next_earnings_date'] = str(earnings_dates[0])
            except:
                pass  # Earnings date optional

        except Exception as e:
            logger.warning(f"  ✗ {symbol}: Konnte yfinance Daten nicht abrufen: {e}")

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
    """Berechnet Branchen-Mediane für P/E Ratios."""

    db = DatabaseManager()

    try:
        # Lade alle Symbole mit ihren aktuellen Fundamentaldaten aus der DB
        # (Nicht aus CSV, da yfinance Daten aktueller sein könnten)
        cursor = db.conn.cursor()
        cursor.execute("SELECT symbol, sector, pe_ratio FROM fundamental_data")
        all_fundamentals = pd.DataFrame(cursor.fetchall(), columns=['symbol', 'sector', 'pe_ratio'])

        if all_fundamentals.empty:
            logger.warning("Keine Fundamentaldaten in der DB zum Berechnen der Branchen-Mediane.")
            return False

        # Gruppiere nach Sektor und berechne Median
        sector_medians = all_fundamentals.groupby('sector')['pe_ratio'].median().to_dict()

        logger.info(f"\n{'='*60}")
        logger.info("Branchen-P/E-Mediane:")
        for sector, median in sector_medians.items():
            if pd.notna(median) and median > 0:
                logger.info(f"  {sector}: {median:.2f}")
        logger.info(f"{'='*60}\n")

        # Update alle Symbole mit ihrem Branchen-Median
        success_count = 0
        # Hole die Fundamentaldaten erneut, um alle Spalten zu bekommen
        cursor.execute("SELECT symbol, market_cap, pe_ratio, free_cash_flow, revenue, earnings, sector, industry, avg_volume_20d, timestamp, next_earnings_date FROM fundamental_data")
        all_data_from_db = pd.DataFrame(cursor.fetchall(), columns=['symbol', 'market_cap', 'pe_ratio', 'free_cash_flow', 'revenue', 'earnings', 'sector', 'industry', 'avg_volume_20d', 'timestamp', 'next_earnings_date'])

        for index, row in all_data_from_db.iterrows():
            symbol = row['symbol']
            sector = row.get('sector', '')
            sector_median = sector_medians.get(sector, 0.0)

            if pd.notna(sector_median) and sector_median > 0:
                # Bereite Update Dict vor
                update_data = {
                    'market_cap': row['market_cap'],
                    'pe_ratio': row['pe_ratio'],
                    'free_cash_flow': row['free_cash_flow'],
                    'revenue': row['revenue'],
                    'earnings': row['earnings'],
                    'sector': row['sector'],
                    'industry': row['industry'],
                    'avg_volume_20d': row['avg_volume_20d'],
                    'next_earnings_date': row['next_earnings_date'],
                    'sector_pe_median': float(sector_median)
                }
                if db.save_fundamental_data(symbol, update_data):
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

    # 1. Import Fundamentaldaten (inkl. yfinance Daten)
    logger.info("\n1. Importiere Fundamentaldaten (inkl. yfinance Daten)...")
    import_fundamentals_from_watchlist()

    # 2. Berechne Branchen-Mediane
    logger.info("\n2. Berechne Branchen-P/E-Mediane...")
    calculate_sector_medians()

    logger.info("\n✓ Import abgeschlossen! Optionsstrategie ist jetzt einsatzbereit.")
    logger.info("  Starte den Bot neu, um die Optionsstrategie zu verwenden.")