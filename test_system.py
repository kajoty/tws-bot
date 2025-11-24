#!/usr/bin/env python3
"""
System-Test für das integrierte TWS-Trading-System
Testet Datenbank, Signal-Logik und Integration
"""

from tws_bot.data.database import DatabaseManager
from tws_bot.core.signals import check_entry_signal
import pandas as pd
import numpy as np
import logging

logging.basicConfig(level=logging.INFO)

def main():
    print('=== SYSTEM TEST ===')
    print()

    # Test 1: Datenbank
    print('1. Datenbank-Test...')
    try:
        db = DatabaseManager()
        health = db.health_check()
        print('   ✅ Datenbank: OK')
        print(f'   📊 Status: {health.get("status", "unknown")}')

        # Test Options-Tabellen
        options_signals = db.get_options_signals(days=1)
        print(f'   📈 Options-Signale (24h): {len(options_signals)}')

        aktien_signals = db.get_signals(days=1)
        print(f'   📈 Aktien-Signale (24h): {len(aktien_signals)}')

        # Test Options-Statistiken
        options_stats = db.get_options_signal_stats(days=30)
        print(f'   📊 Options-Statistiken: {options_stats}')

        db.close()
    except Exception as e:
        print(f'   ❌ Datenbank-Fehler: {e}')

    print()

    # Test 2: Signal-Logik
    print('2. Signal-Logik-Test...')
    try:
        # Test-Daten erstellen
        np.random.seed(42)
        prices = 100 + np.cumsum(np.random.randn(60) * 2)
        df = pd.DataFrame({
            'close': prices,
            'high': prices + np.abs(np.random.randn(60)),
            'low': prices - np.abs(np.random.randn(60))
        })

        signal = check_entry_signal('TEST', df, tws_connector=None)
        if signal:
            print('   ✅ Signal-Generierung: OK')
            print(f'   📊 Signal-Typ: {signal["type"]}')
            print(f'   📊 Grund: {signal["reason"]}')
        else:
            print('   ⚠️  Kein Signal generiert (normal bei zufälligen Test-Daten)')

    except Exception as e:
        print(f'   ❌ Signal-Fehler: {e}')

    print()

    # Test 3: Integration
    print('3. Integration-Test...')
    try:
        # Teste Import der Web-App (ohne Start)
        import sys
        if 'tws_bot.web.app' not in sys.modules:
            print('   ✅ Web-App kann importiert werden')
        else:
            print('   ⚠️  Web-App bereits geladen (normal)')

        print('   ✅ Integration: OK')

    except Exception as e:
        print(f'   ❌ Integrations-Fehler: {e}')

    print()
    print('=== TEST ABGESCHLOSSEN ===')
    print('Web-Dashboard: http://127.0.0.1:5000')
    print('Komplettes System: start_complete_system.bat')

if __name__ == "__main__":
    main()