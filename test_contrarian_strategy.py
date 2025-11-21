"""
Test-Skript für die konträre Optionsstrategie.
Demonstriert Screening und Signal-Generierung ohne Live-Trading.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

import config
from contrarian_options_strategy import ContrarianOptionsStrategy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_mock_price_data(symbol: str, scenario: str = "near_52w_high") -> pd.DataFrame:
    """
    Erstellt Mock-Preisdaten für Tests.
    
    Args:
        symbol: Ticker Symbol
        scenario: "near_52w_high", "near_52w_low", oder "neutral"
    """
    # Generiere 252 Handelstage (1 Jahr)
    dates = pd.date_range(end=datetime.now(), periods=252, freq='D')
    
    if scenario == "near_52w_high":
        # Aktie nahe 52W-Hoch
        base_price = 150.0
        prices = np.random.normal(140, 5, 250)  # Meiste Zeit zwischen 135-145
        prices = np.append(prices, [148.0, 149.5])  # Steigt am Ende auf 149.5 (52W-Hoch=150)
        
    elif scenario == "near_52w_low":
        # Aktie nahe 52W-Tief
        base_price = 100.0
        prices = np.random.normal(110, 5, 250)  # Meiste Zeit zwischen 105-115
        prices = np.append(prices, [102.0, 101.0])  # Fällt am Ende auf 101 (52W-Tief=100)
        
    else:  # neutral
        prices = np.random.normal(125, 5, 252)
    
    df = pd.DataFrame({
        'date': dates,
        'close': prices,
        'open': prices * 0.99,
        'high': prices * 1.01,
        'low': prices * 0.98,
        'volume': np.random.randint(500000, 2000000, 252)
    })
    
    # Berechne 52-Wochen Hoch/Tief
    df['52w_high'] = df['close'].rolling(window=252, min_periods=1).max()
    df['52w_low'] = df['close'].rolling(window=252, min_periods=1).min()
    
    return df


def create_mock_fundamental_data(scenario: str = "overvalued") -> dict:
    """
    Erstellt Mock-Fundamentaldaten für Tests.
    
    Args:
        scenario: "overvalued" (für Long Put) oder "undervalued" (für Long Call)
    """
    base_data = {
        'market_cap': 10_000_000_000,  # $10B
        'avg_volume': 1_000_000,
        'sector': 'Technology'
    }
    
    if scenario == "overvalued":
        # Für Long Put Test
        base_data.update({
            'pe_ratio': 90.0,  # Hoch
            'sector_pe_median': 30.0,  # 90/30 = 3x = 300% (über 150% Threshold)
            'free_cash_flow': 500_000_000,
            'current_iv': 0.45,  # 45% IV
            'iv_history': pd.Series([0.20, 0.25, 0.30, 0.35, 0.40, 0.42, 0.45]),  # Steigend
        })
        
    else:  # undervalued
        # Für Long Call Test
        base_data.update({
            'pe_ratio': 12.0,  # Niedrig
            'sector_pe_median': 30.0,
            'free_cash_flow': 800_000_000,  # Positiv und stark
            'current_iv': 0.18,  # 18% IV (niedrig)
            'iv_history': pd.Series([0.25, 0.30, 0.35, 0.28, 0.22, 0.20, 0.18]),  # Fallend
        })
    
    return base_data


def test_long_put_strategy():
    """Test Long Put Strategie (Short am 52W-Hoch)."""
    print("\n" + "="*70)
    print("TEST: LONG PUT STRATEGIE (Short am 52-Wochen-Hoch)")
    print("="*70)
    
    strategy = ContrarianOptionsStrategy()
    symbol = "AAPL"
    
    # Erstelle Testdaten
    df = create_mock_price_data(symbol, scenario="near_52w_high")
    fundamental_data = create_mock_fundamental_data(scenario="overvalued")
    
    print(f"\n📊 Mock-Daten erstellt:")
    print(f"  Symbol: {symbol}")
    print(f"  Aktueller Preis: ${df.iloc[-1]['close']:.2f}")
    print(f"  52W-Hoch: ${df.iloc[-1]['52w_high']:.2f}")
    print(f"  Distanz zu 52W-Hoch: {((df.iloc[-1]['close'] / df.iloc[-1]['52w_high']) - 1) * 100:.2f}%")
    print(f"  P/E Ratio: {fundamental_data['pe_ratio']:.1f}")
    print(f"  Sektor P/E Median: {fundamental_data['sector_pe_median']:.1f}")
    print(f"  IV Rank: {strategy.calculate_iv_rank(fundamental_data['current_iv'], fundamental_data['iv_history']):.1f}")
    
    # Teste Strategie
    signal, confidence, details = strategy.check_long_put_criteria(
        symbol, df, fundamental_data
    )
    
    print(f"\n🎯 ERGEBNIS:")
    print(f"  Signal: {'✓ LONG PUT GETRIGGERT' if signal else '✗ Kein Signal'}")
    if signal:
        print(f"  Confidence: {confidence:.1%}")
        print(f"  Gründe:")
        for s in details['signals']:
            print(f"    - {s}")
        
        # Berechne Stop-Loss
        stop_loss = strategy.calculate_stop_loss("LONG_PUT", details['52w_high'])
        print(f"  Stop-Loss: ${stop_loss:.2f}")
    else:
        print(f"  Grund: {details.get('reason', 'Unbekannt')}")
    
    return signal, confidence, details


def test_long_call_strategy():
    """Test Long Call Strategie (Long am 52W-Tief)."""
    print("\n" + "="*70)
    print("TEST: LONG CALL STRATEGIE (Long am 52-Wochen-Tief)")
    print("="*70)
    
    strategy = ContrarianOptionsStrategy()
    symbol = "MSFT"
    
    # Erstelle Testdaten
    df = create_mock_price_data(symbol, scenario="near_52w_low")
    fundamental_data = create_mock_fundamental_data(scenario="undervalued")
    
    print(f"\n📊 Mock-Daten erstellt:")
    print(f"  Symbol: {symbol}")
    print(f"  Aktueller Preis: ${df.iloc[-1]['close']:.2f}")
    print(f"  52W-Tief: ${df.iloc[-1]['52w_low']:.2f}")
    print(f"  Distanz zu 52W-Tief: {((df.iloc[-1]['close'] / df.iloc[-1]['52w_low']) - 1) * 100:.2f}%")
    print(f"  FCF: ${fundamental_data['free_cash_flow']:,.0f}")
    print(f"  FCF Yield: {(fundamental_data['free_cash_flow'] / fundamental_data['market_cap']) * 100:.2f}%")
    print(f"  IV Rank: {strategy.calculate_iv_rank(fundamental_data['current_iv'], fundamental_data['iv_history']):.1f}")
    
    # Teste Strategie
    signal, confidence, details = strategy.check_long_call_criteria(
        symbol, df, fundamental_data
    )
    
    print(f"\n🎯 ERGEBNIS:")
    print(f"  Signal: {'✓ LONG CALL GETRIGGERT' if signal else '✗ Kein Signal'}")
    if signal:
        print(f"  Confidence: {confidence:.1%}")
        print(f"  Gründe:")
        for s in details['signals']:
            print(f"    - {s}")
        
        # Berechne Stop-Loss
        stop_loss = strategy.calculate_stop_loss("LONG_CALL", details['52w_low'])
        print(f"  Stop-Loss: ${stop_loss:.2f}")
    else:
        print(f"  Grund: {details.get('reason', 'Unbekannt')}")
    
    return signal, confidence, details


def test_position_management():
    """Test Position Management (Stop-Loss, Take-Profit, DTE)."""
    print("\n" + "="*70)
    print("TEST: POSITION MANAGEMENT")
    print("="*70)
    
    strategy = ContrarianOptionsStrategy()
    
    # Test Long Put Position
    print("\n📈 Long Put Position Management:")
    put_position = {
        'strategy': 'LONG_PUT',
        'symbol': 'AAPL',
        'entry_premium': 5.00,  # $5 pro Contract
        'stop_loss_price': 152.0,
        'days_to_expiration': 70
    }
    
    # Szenario 1: Stop-Loss getroffen
    should_close, reason = strategy.should_close_position(
        put_position,
        current_stock_price=153.0,  # Über Stop-Loss
        current_option_value=3.50
    )
    print(f"  Szenario 1 (Aktienkurs über Stop-Loss):")
    print(f"    Schließen: {'Ja' if should_close else 'Nein'}")
    print(f"    Grund: {reason}")
    
    # Szenario 2: Take-Profit erreicht
    should_close, reason = strategy.should_close_position(
        put_position,
        current_stock_price=148.0,
        current_option_value=7.50  # +50% Gewinn
    )
    print(f"  Szenario 2 (Take-Profit +50%):")
    print(f"    Schließen: {'Ja' if should_close else 'Nein'}")
    print(f"    Grund: {reason}")
    
    # Szenario 3: DTE Auto-Close
    put_position['days_to_expiration'] = 8  # Unter Threshold (10)
    should_close, reason = strategy.should_close_position(
        put_position,
        current_stock_price=149.0,
        current_option_value=4.00  # Im Verlust
    )
    print(f"  Szenario 3 (DTE=8, im Verlust):")
    print(f"    Schließen: {'Ja' if should_close else 'Nein'}")
    print(f"    Grund: {reason}")


def main():
    """Führe alle Tests aus."""
    print("\n" + "="*70)
    print(" CONTRARIAN OPTIONS STRATEGY - TEST SUITE")
    print("="*70)
    print(f" Datum: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    
    # Tests ausführen
    test_long_put_strategy()
    test_long_call_strategy()
    test_position_management()
    
    print("\n" + "="*70)
    print(" ✓ ALLE TESTS ABGESCHLOSSEN")
    print("="*70)
    print("\n💡 Nächste Schritte:")
    print("  1. Fundamentaldaten-Quelle anbinden (z.B. IB Fundamentals, Yahoo Finance)")
    print("  2. IV-Daten sammeln und in Datenbank speichern")
    print("  3. Options-Chain-Abfrage mit echten Marktdaten testen")
    print("  4. Backtesting mit historischen Daten durchführen")
    print("  5. Paper Trading mit DRY_RUN=True testen")
    print()


if __name__ == "__main__":
    main()
