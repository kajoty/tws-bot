#!/usr/bin/env python3
"""
Linux Service Wrapper für IB Trading Bot.
Läuft als systemd Service.
"""

import sys
import os
import time
import signal
import logging
from pathlib import Path

# Setze Working Directory
os.chdir(Path(__file__).parent)

# Logging Setup
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / "service.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ServiceWrapper")


class IBTradingBotService:
    """Linux systemd Service für IB Trading Bot."""
    
    def __init__(self):
        self.running = False
        self.bot = None
        
        # Signal Handler für sauberes Herunterfahren
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handler für SIGTERM/SIGINT."""
        logger.info(f"Signal {signum} empfangen - fahre herunter...")
        self.stop()
    
    def stop(self):
        """Service stoppen."""
        logger.info("Service Stop angefordert")
        self.running = False
        
        # Bot sauber beenden
        if self.bot:
            try:
                logger.info("Beende Trading Bot...")
                self.bot.disconnect()
            except Exception as e:
                logger.error(f"Fehler beim Beenden: {e}")
    
    def run(self):
        """Service ausführen."""
        logger.info("="*60)
        logger.info("IB Trading Bot Service gestartet")
        logger.info("="*60)
        
        self.running = True
        self.main()
    
    def main(self):
        """Hauptlogik - Trading Bot ausführen."""
        try:
            # Importiere Bot
            from ib_trading_bot import IBTradingBot
            import config
            
            logger.info(f"Trading Mode: {'PAPER' if config.IS_PAPER_TRADING else 'LIVE'}")
            logger.info(f"Strategy: {config.TRADING_STRATEGY}")
            logger.info(f"Port: {config.IB_PORT}")
            
            # Erstelle Bot-Instanz
            self.bot = IBTradingBot()
            
            # Verbinde zu TWS
            logger.info("Verbinde zu TWS...")
            if not self.bot.connect_to_tws():
                logger.error("Verbindung zu TWS fehlgeschlagen!")
                return
            
            logger.info("Verbindung erfolgreich! Trading Loop startet...")
            
            # Haupt-Trading-Loop
            while self.running:
                try:
                    # Führe Trading Cycle aus
                    self.bot.run_trading_cycle()
                    
                    # Warte zwischen Zyklen
                    sleep_time = config.TRADING_CYCLE_SLEEP
                    logger.info(f"Warte {sleep_time}s bis zum nächsten Zyklus...")
                    
                    for _ in range(sleep_time):
                        if not self.running:
                            break
                        time.sleep(1)
                
                except KeyboardInterrupt:
                    logger.info("Interrupt empfangen")
                    break
                except Exception as e:
                    logger.error(f"Fehler im Trading Loop: {e}", exc_info=True)
                    time.sleep(60)  # Warte 1 Min bei Fehler
            
            # Cleanup
            logger.info("Trading Loop beendet")
            if self.bot:
                self.bot.disconnect()
            
        except Exception as e:
            logger.error(f"Fataler Fehler: {e}", exc_info=True)
            sys.exit(1)


if __name__ == '__main__':
    service = IBTradingBotService()
    
    try:
        service.run()
    except KeyboardInterrupt:
        print("\n\nBeende Bot...")
        service.stop()
    
    logger.info("Service beendet.")
