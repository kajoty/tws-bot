#!/usr/bin/env python3
"""
Test-Script für TWS-Konnektivität.
Prüft Port, importiert Module und testet IB API Verbindung.
"""

import socket
import sys
import time

print("="*70)
print("TWS KONNEKTIVITÄTSTEST")
print("="*70)

# Test 1: Config laden
print("\n[1/5] Teste config.py...")
try:
    import config
    print(f"✓ Config geladen")
    print(f"    Host: {config.IB_HOST}")
    print(f"    Port: {config.IB_PORT} ({'PAPER' if config.IS_PAPER_TRADING else 'LIVE'})")
    print(f"    Client ID: {config.IB_CLIENT_ID}")
except Exception as e:
    print(f"✗ Fehler: {e}")
    sys.exit(1)

# Test 2: Port-Konnektivität
print(f"\n[2/5] Teste Verbindung zu {config.IB_HOST}:{config.IB_PORT}...")
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3)
    result = sock.connect_ex((config.IB_HOST, config.IB_PORT))
    sock.close()
    
    if result == 0:
        print(f"✓ Port {config.IB_PORT} ist offen - TWS/Gateway läuft!")
    else:
        print(f"✗ Port {config.IB_PORT} ist geschlossen (Error: {result})")
        print("\n  Mögliche Gründe:")
        print("  - TWS/Gateway läuft nicht")
        print("  - Falscher Port (Paper=7497, Live=7496)")
        print("  - API nicht aktiviert")
        sys.exit(1)
except Exception as e:
    print(f"✗ Verbindungsfehler: {e}")
    sys.exit(1)

# Test 3: IB API Import
print("\n[3/5] Teste IB API Import...")
try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    print("✓ ibapi-Modul importiert")
except ImportError as e:
    print(f"✗ IB API nicht gefunden: {e}")
    print("\n  Installiere mit: pip install ibapi")
    sys.exit(1)

# Test 4: Bot-Module importieren
print("\n[4/5] Teste Bot-Module...")
try:
    from database import DatabaseManager
    from risk_management import RiskManager
    from strategy import TradingStrategy
    from performance import PerformanceAnalyzer
    from ib_trading_bot import IBTradingBot
    print("✓ Alle Module erfolgreich importiert")
except Exception as e:
    print(f"✗ Import-Fehler: {e}")
    sys.exit(1)

# Test 5: Kurze API-Verbindung testen
print("\n[5/5] Teste IB API Verbindung (5 Sekunden)...")
try:
    bot = IBTradingBot()
    
    if bot.connect_to_tws():
        print("✓ API-Verbindung erfolgreich!")
        print(f"    Next Order ID: {bot.next_valid_order_id}")
        time.sleep(2)
        bot.disconnect_from_tws()
        print("✓ Sauber getrennt")
    else:
        print("✗ API-Verbindung fehlgeschlagen")
        print("\n  Überprüfe TWS-Einstellungen:")
        print("  - Einstellungen → API → 'Enable ActiveX and Socket Clients'")
        print("  - Port muss auf 7497 (Paper) oder 7496 (Live) stehen")
        sys.exit(1)
        
except Exception as e:
    print(f"✗ API-Test fehlgeschlagen: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "="*70)
print("ALLE TESTS BESTANDEN! ✓")
print("="*70)
print("\nDer Bot ist bereit zum Starten mit:")
print("  python main.py")
print()
