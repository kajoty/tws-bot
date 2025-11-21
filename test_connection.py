"""Einfacher TWS-Verbindungstest"""
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
import threading
import time

class TestApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.connected = False
        
    def nextValidId(self, orderId):
        self.connected = True
        print(f"✓ Verbindung erfolgreich! Nächste Order-ID: {orderId}")
        
    def error(self, reqId, errorCode, errorString):
        if errorCode < 2000:
            print(f"❌ Fehler [{errorCode}]: {errorString}")
        else:
            print(f"ℹ Info [{errorCode}]: {errorString}")

if __name__ == "__main__":
    app = TestApp()
    
    print("="*60)
    print("TWS-Verbindungstest")
    print("="*60)
    print("Verbinde mit TWS auf 127.0.0.1:7497 (Paper Trading)...")
    
    try:
        app.connect("127.0.0.1", 7497, 1)
        
        api_thread = threading.Thread(target=app.run, daemon=True)
        api_thread.start()
        
        # Warte auf Verbindung
        timeout = 5
        start = time.time()
        while not app.connected and (time.time() - start) < timeout:
            time.sleep(0.1)
        
        if app.connected:
            print("\n✅ TWS-Verbindung erfolgreich bestätigt!")
            print("\nVerbindungsdetails:")
            print(f"  Host: 127.0.0.1")
            print(f"  Port: 7497 (Paper Trading)")
            print(f"  Client ID: 1")
            time.sleep(1)
            app.disconnect()
            print("\n✓ Verbindung getrennt")
        else:
            print("\n❌ Verbindung fehlgeschlagen (Timeout)")
            print("\nBitte überprüfen Sie:")
            print("  1. TWS oder IB Gateway läuft")
            print("  2. API-Einstellungen aktiviert:")
            print("     - Einstellungen → API → Settings")
            print("     - 'Enable ActiveX and Socket Clients' aktiviert")
            print("  3. Port 7497 ist korrekt (Paper Trading)")
            
    except ConnectionRefusedError:
        print("\n❌ Verbindung abgelehnt!")
        print("TWS/Gateway läuft nicht oder Port ist falsch")
    except Exception as e:
        print(f"\n❌ Fehler: {e}")
    
    print("="*60)
