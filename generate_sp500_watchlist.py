"""
Skript zum Abrufen aller S&P 500 Ticker und Erstellen der Watchlist.
"""

import pandas as pd
import yfinance as yf
from datetime import datetime

def get_sp500_tickers():
    """Holt S&P 500 Ticker von Wikipedia."""
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    
    # Verwende requests Session für bessere Kompatibilität
    try:
        import requests
        from io import StringIO
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        # Lese alle Tabellen
        tables = pd.read_html(StringIO(response.text))
        
        # Finde die richtige Tabelle (die mit den meisten Zeilen und Spalten)
        sp500_table = None
        for table in tables:
            if len(table.columns) >= 7 and len(table) > 400:  # S&P 500 hat ~500 Zeilen
                sp500_table = table
                break
        
        if sp500_table is None:
            raise ValueError("Konnte S&P 500 Tabelle nicht finden")
        
        print(f"   Tabelle gefunden mit {len(sp500_table)} Zeilen und {len(sp500_table.columns)} Spalten")
        print(f"   Spalten: {list(sp500_table.columns)}")
        
        # Extrahiere relevante Spalten mit flexiblen Namen
        df = pd.DataFrame({
            'symbol': sp500_table.iloc[:, 0].astype(str).str.replace('.', '-', regex=False),
            'name': sp500_table.iloc[:, 1].astype(str),
            'sector': sp500_table.iloc[:, 2].astype(str) if len(sp500_table.columns) > 2 else 'Unknown',
            'industry': sp500_table.iloc[:, 3].astype(str) if len(sp500_table.columns) > 3 else 'Unknown'
        })
        
        # Entferne leere Zeilen
        df = df.dropna(subset=['symbol'])
        df = df[df['symbol'].str.strip() != '']
        df = df[df['symbol'] != 'nan']
        
        print(f"   {len(df)} Ticker erfolgreich geladen")
        
        return df
    except Exception as e:
        print(f"Fehler beim Laden von Wikipedia: {e}")
        import traceback
        traceback.print_exc()
        raise

def enrich_with_yfinance(df, max_symbols=None):
    """
    Reichert Ticker mit Daten von Yahoo Finance an.
    
    Args:
        df: DataFrame mit Symbolen
        max_symbols: Optional - limitiere auf X Symbole für schnelleres Testen
    """
    if max_symbols:
        df = df.head(max_symbols)
    
    print(f"Rufe Daten für {len(df)} Symbole ab...")
    
    enriched_data = []
    
    for idx, row in df.iterrows():
        symbol = row['symbol']
        
        try:
            print(f"  {idx+1}/{len(df)}: {symbol}...", end=' ')
            
            ticker = yf.Ticker(symbol)
            info = ticker.info
            
            # Extrahiere relevante Daten
            market_cap = info.get('marketCap', 0)
            avg_volume = info.get('averageVolume', 0)
            pe_ratio = info.get('trailingPE', 0)
            fcf = info.get('freeCashflow', 0)
            
            enriched_data.append({
                'symbol': symbol,
                'name': row['name'],
                'market_cap': market_cap if market_cap else 0,
                'avg_volume': avg_volume if avg_volume else 0,
                'sector': row['sector'],
                'industry': row['industry'],
                'pe_ratio': pe_ratio if pe_ratio else 0.0,
                'fcf': fcf if fcf else 0.0,
                'enabled': True,  # Alle aktiv
                'notes': ''
            })
            
            print("✓")
            
        except Exception as e:
            print(f"✗ ({e})")
            # Füge mit Defaults hinzu
            enriched_data.append({
                'symbol': symbol,
                'name': row['name'],
                'market_cap': 0,
                'avg_volume': 0,
                'sector': row['sector'],
                'industry': row['industry'],
                'pe_ratio': 0.0,
                'fcf': 0.0,
                'enabled': True,
                'notes': 'Daten nicht verfügbar'
            })
    
    return pd.DataFrame(enriched_data)

def main():
    print("="*70)
    print(" S&P 500 WATCHLIST GENERATOR")
    print("="*70)
    print()
    
    # Hole S&P 500 Liste
    print("1. Lade S&P 500 Ticker von Wikipedia...")
    sp500_df = get_sp500_tickers()
    print(f"   ✓ {len(sp500_df)} Ticker gefunden\n")
    
    # Frage ob alle oder Subset
    print("Möchten Sie:")
    print("  1. Alle S&P 500 Ticker laden (dauert ~15-20 Minuten)")
    print("  2. Nur Top 50 nach Alphabet (schneller Test)")
    print("  3. Nur aktuelle 10 aus config.py ersetzen")
    
    choice = input("\nAuswahl (1/2/3): ").strip()
    
    if choice == "2":
        sp500_df = sp500_df.head(50)
        print(f"Limitiere auf {len(sp500_df)} Symbole")
    elif choice == "3":
        # Nur aktuelle Liste aus config übernehmen
        from config import WATCHLIST_STOCKS
        sp500_df = sp500_df[sp500_df['symbol'].isin(WATCHLIST_STOCKS)]
        print(f"Filtere auf vorhandene {len(sp500_df)} Symbole")
    
    print()
    
    # Reichere mit Yahoo Finance an
    print("2. Reichere mit Marktdaten an (Yahoo Finance)...")
    enriched_df = enrich_with_yfinance(sp500_df)
    
    print()
    print("3. Speichere in watchlist.csv...")
    
    # Speichere
    output_file = 'watchlist.csv'
    enriched_df.to_csv(output_file, index=False)
    
    print(f"   ✓ Gespeichert: {output_file}")
    
    # Statistik
    print()
    print("="*70)
    print(" STATISTIK")
    print("="*70)
    print(f" Gesamt Symbole:        {len(enriched_df)}")
    print(f" Mit Marktdaten:        {len(enriched_df[enriched_df['market_cap'] > 0])}")
    print(f" Ohne Marktdaten:       {len(enriched_df[enriched_df['market_cap'] == 0])}")
    print()
    
    # Top 10 nach Marktkapitalisierung
    top10 = enriched_df.nlargest(10, 'market_cap')
    print(" Top 10 nach Marktkapitalisierung:")
    for _, row in top10.iterrows():
        print(f"   {row['symbol']:6s} | ${row['market_cap']/1e9:8.1f}B | {row['sector']}")
    
    print()
    print("="*70)
    print(" ✓ FERTIG")
    print("="*70)
    print()
    print("Nächste Schritte:")
    print("  - Prüfe watchlist.csv")
    print("  - Teste mit: python watchlist_cli.py list")
    print("  - Starte Bot: python main.py")
    print()

if __name__ == "__main__":
    main()
