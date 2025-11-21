"""Test-Skript zum Abrufen von Account-Informationen"""
import time
import config
from ib_trading_bot import IBTradingBot

def main():
    print("\n" + "="*70)
    print(" ACCOUNT INFORMATION TEST")
    print("="*70)
    print(f" Modus: {'PAPER TRADING' if config.IS_PAPER_TRADING else 'LIVE TRADING'}")
    print(f" Port:  {config.IB_PORT}")
    print("="*70 + "\n")
    
    # Bot initialisieren
    bot = IBTradingBot()
    
    # Mit TWS verbinden
    print("Verbinde mit TWS...")
    if not bot.connect_to_tws():
        print("❌ Verbindung fehlgeschlagen!")
        return
    
    print("✓ Verbunden\n")
    
    # Account-Updates aktivieren
    print("Aktiviere Account-Updates...")
    bot.request_account_updates(subscribe=True)
    time.sleep(2)  # Warte auf Account-Daten
    
    # Account Summary abrufen
    print("Fordere Account Summary an...")
    req_id = bot.request_account_summary()
    bot.wait_for_request(req_id, timeout=5)
    
    # Warte kurz auf alle Daten
    time.sleep(1)
    
    # Account-Informationen anzeigen
    bot.print_account_info()
    
    # Wichtige Einzelwerte anzeigen
    print("\nWichtige Account-Werte:")
    print("-" * 70)
    
    cushion_val = bot.get_account_summary_value('Cushion')
    if cushion_val:
        try:
            cushion = float(cushion_val) * 100
            print(f"  Cushion (Sicherheitspolster):  {cushion:.2f}%")
            
            if cushion < 10:
                print("  ⚠️  WARNUNG: Cushion unter 10% - Margin-Risiko!")
            elif cushion < 25:
                print("  ⚠️  VORSICHT: Niedriger Cushion")
            else:
                print("  ✓ Cushion OK")
        except:
            pass
    
    net_liq = bot.get_account_summary_value('NetLiquidation_USD')
    if net_liq:
        print(f"\n  Net Liquidation:                ${float(net_liq):,.2f}")
    
    buying_power = bot.get_account_summary_value('BuyingPower_USD')
    if buying_power:
        print(f"  Buying Power:                   ${float(buying_power):,.2f}")
    
    available = bot.get_account_summary_value('AvailableFunds_USD')
    if available:
        print(f"  Available Funds:                ${float(available):,.2f}")
    
    print("\n" + "="*70)
    
    # Account-Updates deaktivieren
    bot.request_account_updates(subscribe=False)
    
    # Trennen
    bot.disconnect_from_tws()
    print("\n✓ Test abgeschlossen\n")

if __name__ == "__main__":
    main()
