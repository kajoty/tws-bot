"""
Haupt-Start-Skript für den IB Trading Bot.
Orchestriert Bot-Initialisierung, Trading-Loop und Shutdown.
"""

import sys
import time
import signal
import logging
from datetime import datetime

import config
from ib_trading_bot import IBTradingBot

logger = logging.getLogger(__name__)

bot_instance = None


def signal_handler(sig, frame):
    """Handler für Ctrl+C und andere Signals."""
    print("\n\n🛑 Shutdown-Signal empfangen...")
    
    if bot_instance:
        print("Trenne Bot von TWS...")
        bot_instance.is_trading_active = False
        bot_instance.disconnect_from_tws()
        
        print("Erstelle Performance-Report...")
        create_performance_report(bot_instance)
    
    print("✓ Bot sauber beendet\n")
    sys.exit(0)


def create_performance_report(bot: IBTradingBot):
    """Erstellt finalen Performance-Report."""
    try:
        bot.get_portfolio_summary()
        
        performance_df = bot.db.get_performance_history(days=90)
        
        if not performance_df.empty:
            metrics = bot.performance.calculate_metrics(performance_df)
            bot.performance.print_summary(metrics)
            
            trades_df = bot.db.get_trade_history(days=90)
            
            print("Erstelle Performance-Charts...")
            perf_plot = bot.performance.plot_performance(performance_df, trades_df)
            if perf_plot:
                print(f"  ✓ Performance-Chart: {perf_plot}")
        else:
            print("⚠ Keine Performance-Daten verfügbar")
            
    except Exception as e:
        logger.error(f"Fehler beim Erstellen des Reports: {e}")


def main():
    """Hauptfunktion - Startet den Trading Bot."""
    global bot_instance
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("\n" + "="*70)
    print("  INTERACTIVE BROKERS TRADING BOT")
    print("="*70)
    print(f"  Modus:           {'PAPER TRADING' if config.IS_PAPER_TRADING else '🔴 LIVE TRADING 🔴'}")
    print(f"  Host:            {config.IB_HOST}:{config.IB_PORT}")
    print(f"  Account Size:    ${config.ACCOUNT_SIZE:,.2f}")
    print(f"  Max Risk/Trade:  {config.MAX_RISK_PER_TRADE_PCT * 100:.2f}%")
    print(f"  Watchlist:       {', '.join(config.WATCHLIST_STOCKS)}")
    print(f"  Dry Run:         {'Ja' if config.DRY_RUN else 'Nein'}")
    print("="*70 + "\n")
    
    if not config.IS_PAPER_TRADING and not config.SKIP_LIVE_TRADING_CONFIRMATION:
        print("⚠️  WARNUNG: LIVE TRADING AKTIVIERT! ⚠️")
        print("Dieser Bot wird echte Orders mit echtem Geld platzieren!")
        response = input("Sind Sie sicher, dass Sie fortfahren möchten? (yes/no): ")
        if response.lower() != 'yes':
            print("Abgebrochen.")
            return
    
    try:
        print("Initialisiere Trading Bot...")
        bot = IBTradingBot(host=config.IB_HOST, port=config.IB_PORT, client_id=config.IB_CLIENT_ID)
        bot_instance = bot
        
        print("Verbinde mit TWS/Gateway...")
        if not bot.connect_to_tws():
            print("❌ Verbindung fehlgeschlagen!")
            print("\nStellen Sie sicher, dass:")
            print("  1. TWS oder IB Gateway läuft")
            print("  2. API-Verbindungen aktiviert sind (Einstellungen → API)")
            print(f"  3. Port {config.IB_PORT} korrekt ist")
            return
        
        print("✓ Bot erfolgreich initialisiert\n")
        
        # Bereinige alte Daten (älter als CONFIG.DATA_RETENTION_DAYS)
        print(f"Bereinige alte Daten (älter als {config.DATA_RETENTION_DAYS} Tage)...")
        bot.db_manager.cleanup_old_data(days_to_keep=config.DATA_RETENTION_DAYS)
        
        print("\nLade/Aktualisiere historische Daten für Watchlist...")
        loaded_from_db = 0
        loaded_from_api = 0
        
        for symbol in bot.watchlist:
            print(f"  - {symbol}...", end=" ", flush=True)
            
            # Prüfe ob Daten aktuell sind
            if not bot.db_manager.needs_update(symbol, max_age_days=config.DATA_MAX_AGE_DAYS):
                df = bot.db_manager.load_historical_data(symbol)
                if not df.empty:
                    bot.historical_data_cache[symbol] = df
                    loaded_from_db += 1
                    print("✓ (aus DB)")
                    continue
            
            # Sonst: Von API laden
            req_id = bot.request_historical_data(symbol)
            bot.wait_for_request(req_id, timeout=30)
            loaded_from_api += 1
            print("✓ (API)")
        
        print(f"\n✓ Daten geladen: {loaded_from_db} aus DB, {loaded_from_api} von API\n")
        
        bot.is_trading_active = True
        
        print("="*70)
        print("  TRADING GESTARTET")
        print("  Drücken Sie Ctrl+C zum Beenden")
        print("="*70 + "\n")
        
        cycle_count = 0
        
        while bot.is_trading_active:
            try:
                cycle_count += 1
                current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                print(f"\n[{current_time}] Trading-Zyklus #{cycle_count}")
                print("-" * 70)
                
                bot.run_trading_cycle()
                
                summary = bot.risk_manager.get_portfolio_summary()
                bot.db.save_performance_snapshot(
                    equity=summary['total_equity'],
                    cash=summary['cash_available'],
                    positions_value=summary['positions_value'],
                    total_pnl=summary['unrealized_pnl'],
                    daily_pnl=0.0
                )
                
                print(f"\nPortfolio: ${summary['total_equity']:,.2f} | "
                      f"Cash: ${summary['cash_available']:,.2f} | "
                      f"Positionen: {summary['num_positions']}")
                
                # Stop-Loss Check
                for symbol, position in summary['positions'].items():
                    if symbol in bot.historical_data_cache:
                        df = bot.historical_data_cache[symbol]
                        if not df.empty:
                            current_price = df.iloc[-1]['close']
                            bot.risk_manager.update_position_price(symbol, current_price)
                            
                            if bot.risk_manager.check_stop_loss(symbol, current_price):
                                print(f"⚠️  Stop-Loss ausgelöst für {symbol}!")
                                quantity = position['quantity']
                                order_id = bot.place_order(symbol, "SELL", quantity)
                                if order_id:
                                    bot.risk_manager.remove_position(symbol, current_price)
                                    print(f"✓ Stop-Loss Order platziert: SELL {quantity} {symbol}")
                
                sleep_time = 300  # 5 Minuten
                print(f"\nWarte {sleep_time}s bis zum nächsten Zyklus...")
                
                for i in range(sleep_time):
                    if not bot.is_trading_active:
                        break
                    time.sleep(1)
                
            except KeyboardInterrupt:
                raise
            except Exception as e:
                logger.error(f"Fehler im Trading-Loop: {e}", exc_info=True)
                print(f"❌ Fehler: {e}")
                print("Warte 60 Sekunden vor Neustart...")
                time.sleep(60)
        
    except KeyboardInterrupt:
        print("\n\nBeende Bot...")
    except Exception as e:
        logger.error(f"Kritischer Fehler: {e}", exc_info=True)
        print(f"\n❌ Kritischer Fehler: {e}")
    finally:
        if bot_instance:
            print("\nErstelle finalen Performance-Report...")
            create_performance_report(bot_instance)
            
            print("Schließe Datenbankverbindung...")
            bot_instance.db.close()
            
            print("Trenne von TWS...")
            bot_instance.disconnect_from_tws()
        
        print("\n✓ Bot beendet\n")


if __name__ == "__main__":
    banner = """
    ╔═══════════════════════════════════════════════════════════════╗
    ║                                                               ║
    ║           Interactive Brokers Trading Bot v1.0               ║
    ║                                                               ║
    ║         Algorithmischer Handel mit TWS API                   ║
    ║                                                               ║
    ╚═══════════════════════════════════════════════════════════════╝
    """
    print(banner)
    
    main()
