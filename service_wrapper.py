"""
Windows Service Wrapper für IB Trading Bot.
Ermöglicht Ausführung als Hintergrunddienst.
"""

import sys
import os
import time
import logging
from pathlib import Path

# Setze Working Directory
os.chdir(Path(__file__).parent)

try:
    import win32serviceutil
    import win32service
    import win32event
    import servicemanager
    WINDOWS_SERVICE = True
except ImportError:
    WINDOWS_SERVICE = False
    print("WARNUNG: pywin32 nicht installiert. Installiere mit: pip install pywin32")

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
    """Windows Service für IB Trading Bot."""
    
    _svc_name_ = "IBTradingBot"
    _svc_display_name_ = "IB Trading Bot Service"
    _svc_description_ = "Automated trading bot for Interactive Brokers TWS"
    
    def __init__(self, args=None):
        if WINDOWS_SERVICE:
            win32serviceutil.ServiceFramework.__init__(self, args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.running = False
        self.bot = None
    
    def SvcStop(self):
        """Dienst stoppen."""
        logger.info("Service Stop angefordert")
        if WINDOWS_SERVICE:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self.stop_event)
        self.running = False
        
        # Bot sauber beenden
        if self.bot:
            try:
                logger.info("Beende Trading Bot...")
                self.bot.disconnect()
            except Exception as e:
                logger.error(f"Fehler beim Beenden: {e}")
    
    def SvcDoRun(self):
        """Dienst ausführen."""
        logger.info("="*60)
        logger.info("IB Trading Bot Service gestartet")
        logger.info("="*60)
        
        if WINDOWS_SERVICE:
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, '')
            )
        
        self.running = True
        self.main()
    
    def main(self):
        """Hauptlogik - Trading Bot ausführen."""
        try:
            # Importiere Bot (verzögert, damit Working Directory gesetzt ist)
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
                    # Prüfe ob Service gestoppt werden soll
                    if WINDOWS_SERVICE:
                        rc = win32event.WaitForSingleObject(self.stop_event, 0)
                        if rc == win32event.WAIT_OBJECT_0:
                            logger.info("Stop-Event empfangen")
                            break
                    
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
            if WINDOWS_SERVICE:
                servicemanager.LogErrorMsg(f"Service Error: {e}")


# Für Windows Service
if WINDOWS_SERVICE:
    class IBTradingBotServiceWrapper(win32serviceutil.ServiceFramework, IBTradingBotService):
        """Windows Service Framework Wrapper."""
        pass


def run_as_console():
    """Führe als Konsolen-Anwendung aus (für Testing)."""
    print("="*60)
    print(" IB TRADING BOT - CONSOLE MODE")
    print("="*60)
    print(" Drücke Ctrl+C zum Beenden")
    print("="*60)
    
    service = IBTradingBotService()
    service.running = True
    
    try:
        service.main()
    except KeyboardInterrupt:
        print("\n\nBeende Bot...")
        service.running = False
        if service.bot:
            service.bot.disconnect()
    
    print("Bot beendet.")


if __name__ == '__main__':
    if len(sys.argv) == 1:
        # Keine Argumente - starte als Konsolen-App
        run_as_console()
    else:
        # Service-Kommandos (install, start, stop, remove)
        if WINDOWS_SERVICE:
            win32serviceutil.HandleCommandLine(IBTradingBotServiceWrapper)
        else:
            print("ERROR: pywin32 nicht installiert!")
            print("Installiere mit: pip install pywin32")
            sys.exit(1)
