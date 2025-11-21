"""
Backtest: Analysiert warum keine Ticker ausgewählt werden.
Zeigt für jeden Filter wie viele Symbole durchkommen.
"""

import pandas as pd
import numpy as np
from database import DatabaseManager
from contrarian_options_strategy import ContrarianOptionsStrategy
import config

def analyze_filters():
    db = DatabaseManager(config.DB_PATH)
    strategy = ContrarianOptionsStrategy()
    
    print("\n" + "="*70)
    print("  FILTER ANALYSE - Warum werden keine Ticker ausgewählt?")
    print("="*70 + "\n")
    
    # Lade alle Symbole
    cursor = db.conn.cursor()
    cursor.execute("SELECT DISTINCT symbol FROM fundamental_data ORDER BY symbol")
    all_symbols = [row[0] for row in cursor.fetchall()]
    
    print(f"Starte mit: {len(all_symbols)} Symbolen\n")
    
    results = []
    
    for symbol in all_symbols[:50]:  # Teste erste 50 für Performance
        # Lade Daten
        df = db.load_historical_data(symbol)
        fundamentals = db.get_fundamental_data(symbol)
        
        if df.empty or not fundamentals:
            continue
            
        current_price = df['close'].iloc[-1]
        # 52W High/Low: use all available data instead of rolling(252) to avoid NaN
        high_52w = df['high'].max()
        low_52w = df['low'].min()
        
        # Berechne RSI
        rsi = strategy.calculate_rsi(df)
        
        # Fundamentals (default to 0 if None)
        market_cap = fundamentals.get('market_cap') or 0
        avg_volume = fundamentals.get('avg_volume_20d') or 0  # Spalte heißt avg_volume_20d
        pe_ratio = fundamentals.get('pe_ratio')
        sector_pe = fundamentals.get('sector_median_pe')
        fcf = fundamentals.get('free_cash_flow')
        
        # Universe Filter
        passes_universe = market_cap >= config.MIN_MARKET_CAP and avg_volume >= config.MIN_AVG_VOLUME
        
        # Long Put Checks
        distance_to_high = (current_price - high_52w) / high_52w
        near_52w_high = current_price >= high_52w * (1 - config.TRIGGER_DISTANCE_52W_PERCENT)
        rsi_overbought = rsi > 70
        pe_overvalued = False
        if pe_ratio and sector_pe and sector_pe > 0:
            pe_overvalued = pe_ratio > (sector_pe * config.LONG_PUT_PE_OVERVALUATION)
        
        # Long Call Checks
        distance_to_low = (current_price - low_52w) / low_52w
        near_52w_low = current_price <= low_52w * (1 + config.TRIGGER_DISTANCE_52W_PERCENT)
        rsi_oversold = rsi < 30
        fcf_positive = fcf and fcf > 0 if fcf else False
        
        result = {
            'symbol': symbol,
            'price': current_price,
            '52w_high': high_52w,
            '52w_low': low_52w,
            'dist_high_%': distance_to_high * 100,
            'dist_low_%': distance_to_low * 100,
            'rsi': rsi,
            'pe_ratio': pe_ratio if pe_ratio else 0,
            'sector_pe': sector_pe if sector_pe else 0,
            'fcf': fcf if fcf else 0,
            'mkt_cap_b': market_cap / 1e9,
            'volume': avg_volume,
            # Filters
            'universe_ok': passes_universe,
            'near_high': near_52w_high,
            'near_low': near_52w_low,
            'rsi_>70': rsi_overbought,
            'rsi_<30': rsi_oversold,
            'pe_over': pe_overvalued,
            'fcf_pos': fcf_positive
        }
        results.append(result)
    
    df_results = pd.DataFrame(results)
    
    # Summary Statistics
    print("\n" + "-"*70)
    print("FILTER DURCHLAUF (erste 50 Symbole)")
    print("-"*70)
    print(f"Universe Filter (MktCap + Volume):  {df_results['universe_ok'].sum():3d} / {len(df_results)} ✓")
    print(f"\nLONG PUT Kriterien:")
    print(f"  - Nahe 52W-Hoch (±2%):            {df_results['near_high'].sum():3d} / {len(df_results)}")
    print(f"  - RSI > 70 (Überkauft):           {df_results['rsi_>70'].sum():3d} / {len(df_results)}")
    print(f"  - P/E > 150% Sektor-Median:       {df_results['pe_over'].sum():3d} / {len(df_results)}")
    
    print(f"\nLONG CALL Kriterien:")
    print(f"  - Nahe 52W-Tief (±2%):            {df_results['near_low'].sum():3d} / {len(df_results)}")
    print(f"  - RSI < 30 (Überverkauft):        {df_results['rsi_<30'].sum():3d} / {len(df_results)}")
    print(f"  - FCF positiv:                    {df_results['fcf_pos'].sum():3d} / {len(df_results)}")
    
    # Kombinations-Check
    put_candidates = df_results[df_results['universe_ok'] & df_results['near_high'] & df_results['rsi_>70'] & df_results['pe_over']]
    call_candidates = df_results[df_results['universe_ok'] & df_results['near_low'] & df_results['rsi_<30'] & df_results['fcf_pos']]
    
    print("\n" + "-"*70)
    print(f"ALLE LONG PUT Kriterien erfüllt:    {len(put_candidates)} Symbole")
    print(f"ALLE LONG CALL Kriterien erfüllt:   {len(call_candidates)} Symbole")
    print("-"*70)
    
    if len(put_candidates) > 0:
        print("\n🎯 LONG PUT Kandidaten:")
        for _, row in put_candidates.iterrows():
            print(f"  {row['symbol']:6s} - Preis: ${row['price']:7.2f}, RSI: {row['rsi']:5.1f}, "
                  f"P/E: {row['pe_ratio']:6.1f} vs Sektor: {row['sector_pe']:6.1f}")
    
    if len(call_candidates) > 0:
        print("\n🎯 LONG CALL Kandidaten:")
        for _, row in call_candidates.iterrows():
            print(f"  {row['symbol']:6s} - Preis: ${row['price']:7.2f}, RSI: {row['rsi']:5.1f}, "
                  f"FCF: ${row['fcf']/1e9:.2f}B")
    
    # Zeige Top 10 nach RSI sortiert
    print("\n" + "-"*70)
    print("TOP 10 HÖCHSTER RSI (potenzielle Long Put Kandidaten)")
    print("-"*70)
    top_rsi = df_results.nlargest(10, 'rsi')[['symbol', 'price', 'rsi', 'dist_high_%', 'near_high', 'pe_over']]
    print(top_rsi.to_string(index=False))
    
    print("\n" + "-"*70)
    print("TOP 10 NIEDRIGSTER RSI (potenzielle Long Call Kandidaten)")
    print("-"*70)
    bottom_rsi = df_results.nsmallest(10, 'rsi')[['symbol', 'price', 'rsi', 'dist_low_%', 'near_low', 'fcf_pos']]
    print(bottom_rsi.to_string(index=False))
    
    print("\n" + "-"*70)
    print("NÄCHSTE KANDIDATEN AN 52W-HOCH")
    print("-"*70)
    near_high = df_results.nsmallest(10, 'dist_high_%')[['symbol', 'price', 'dist_high_%', 'rsi', 'rsi_>70', 'pe_over']]
    print(near_high.to_string(index=False))
    
    print("\n" + "-"*70)
    print("NÄCHSTE KANDIDATEN AN 52W-TIEF")
    print("-"*70)
    near_low = df_results.nsmallest(10, 'dist_low_%')[['symbol', 'price', 'dist_low_%', 'rsi', 'rsi_<30', 'fcf_pos']]
    print(near_low.to_string(index=False))
    
    db.close()
    
    print("\n" + "="*70)
    print("  FAZIT")
    print("="*70)
    print("Die Strategie ist extrem selektiv - das ist gewollt!")
    print("Nur wenn ALLE Kriterien gleichzeitig erfüllt sind, wird gehandelt.")
    print("\nWenn keine Signale kommen:")
    print("  1. Markt ist aktuell in keiner Extremsituation")
    print("  2. RSI bestätigt keine Übertreibung")
    print("  3. Fundamentals passen nicht (P/E, FCF)")
    print("\nErwartung: 1-3 Signale pro Monat bei 503 Symbolen")
    print("="*70 + "\n")

if __name__ == '__main__':
    analyze_filters()
