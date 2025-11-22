"""
Pushover Benachrichtigungsmodul für Trading Signale.
"""

import logging
from pushover_complete import PushoverAPI
import config

logger = logging.getLogger(__name__)


class PushoverNotifier:
    """Sendet Trading-Signale via Pushover."""
    
    def __init__(self, user_key: str = None, api_token: str = None):
        """
        Initialisiert Pushover Notifier.
        
        Args:
            user_key: Pushover User Key (optional, nutzt config.PUSHOVER_USER_KEY)
            api_token: Pushover API Token (optional, nutzt config.PUSHOVER_API_TOKEN)
        """
        self.user_key = user_key or config.PUSHOVER_USER_KEY
        self.api_token = api_token or config.PUSHOVER_API_TOKEN
        
        if not self.user_key or not self.api_token:
            logger.warning("⚠️ Pushover Credentials fehlen! Benachrichtigungen deaktiviert.")
            self.enabled = False
        else:
            self.enabled = True
            logger.info("✓ Pushover Benachrichtigungen aktiviert")
    
    def send_entry_signal(self, symbol: str, price: float, quantity: int, 
                         reason: str, stop_loss: float = None, take_profit: float = None):
        """
        Sendet Entry-Signal Benachrichtigung.
        
        Args:
            symbol: Ticker Symbol (z.B. "AAPL")
            price: Entry Price
            quantity: Anzahl Aktien
            reason: Grund für Entry (z.B. "MA Crossover + RSI Oversold")
            stop_loss: Stop Loss Price
            take_profit: Take Profit Price
        """
        if not self.enabled:
            logger.info(f"[DRY RUN] Entry Signal: BUY {quantity} {symbol} @ ${price:.2f}")
            return
        
        title = f"🟢 BUY Signal: {symbol}"
        
        message = f"Entry: ${price:.2f}\n"
        message += f"Anzahl: {quantity}\n"
        message += f"Wert: ${price * quantity:,.2f}\n"
        message += f"\nGrund: {reason}"
        
        if stop_loss:
            message += f"\n\n🛑 Stop Loss: ${stop_loss:.2f}"
        if take_profit:
            message += f"\n🎯 Take Profit: ${take_profit:.2f}"
        
        self._send_notification(title, message, priority=1)  # High priority für Entry
    
    def send_exit_signal(self, symbol: str, price: float, quantity: int,
                        entry_price: float, pnl: float, pnl_pct: float, reason: str):
        """
        Sendet Exit-Signal Benachrichtigung.
        
        Args:
            symbol: Ticker Symbol
            price: Exit Price
            quantity: Anzahl Aktien
            entry_price: Entry Price
            pnl: Profit/Loss in USD
            pnl_pct: Profit/Loss in Prozent
            reason: Grund für Exit (z.B. "Stop Loss erreicht")
        """
        if not self.enabled:
            logger.info(f"[DRY RUN] Exit Signal: SELL {quantity} {symbol} @ ${price:.2f} | P&L: ${pnl:.2f} ({pnl_pct:.2f}%)")
            return
        
        # Icon basierend auf Gewinn/Verlust
        if pnl > 0:
            icon = "🟢"
            title = f"{icon} SELL (Gewinn): {symbol}"
        else:
            icon = "🔴"
            title = f"{icon} SELL (Verlust): {symbol}"
        
        message = f"Exit: ${price:.2f}\n"
        message += f"Entry: ${entry_price:.2f}\n"
        message += f"Anzahl: {quantity}\n"
        message += f"\n{icon} P&L: ${pnl:+,.2f} ({pnl_pct:+.2f}%)"
        message += f"\n\nGrund: {reason}"
        
        # Priority basierend auf Verlust
        priority = 1 if pnl < 0 else 0  # High priority bei Verlust
        
        self._send_notification(title, message, priority=priority)
    
    def send_alert(self, title: str, message: str, priority: int = 0):
        """
        Sendet allgemeine Alert-Benachrichtigung.
        
        Args:
            title: Titel der Benachrichtigung
            message: Nachrichtentext
            priority: -2=lowest, -1=low, 0=normal, 1=high, 2=emergency
        """
        if not self.enabled:
            logger.info(f"[DRY RUN] Alert: {title} - {message}")
            return
        
        self._send_notification(title, message, priority=priority)
    
    def _send_notification(self, title: str, message: str, priority: int = None):
        """
        Sendet Pushover Notification.
        
        Args:
            title: Titel
            message: Nachricht
            priority: Priority Level
        """
        if not self.enabled:
            return
        
        try:
            priority = priority if priority is not None else config.PUSHOVER_PRIORITY
            
            api = PushoverAPI(self.api_token)
            api.send_message(
                self.user_key,
                message,
                title=title,
                priority=priority,
                sound=config.PUSHOVER_SOUND
            )
            
            logger.info(f"✓ Pushover gesendet: {title}")
            
        except Exception as e:
            logger.error(f"❌ Pushover Fehler: {e}")
    
    def test_notification(self):
        """Sendet Test-Benachrichtigung."""
        if not self.enabled:
            logger.error("❌ Pushover nicht konfiguriert!")
            return False
        
        try:
            self._send_notification(
                "🧪 TWS Signal Service",
                "Test-Benachrichtigung erfolgreich!\n\nDer Signal-Service ist bereit.",
                priority=0
            )
            logger.info("✓ Test-Benachrichtigung gesendet")
            return True
        except Exception as e:
            logger.error(f"❌ Test fehlgeschlagen: {e}")
            return False


if __name__ == "__main__":
    """Test Script für Pushover Notifications."""
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print(" PUSHOVER NOTIFICATION TEST")
    print("="*60)
    
    notifier = PushoverNotifier()
    
    if not notifier.enabled:
        print("\n❌ Pushover nicht konfiguriert!")
        print("\nBitte in .env eintragen:")
        print("  PUSHOVER_USER_KEY=your_user_key")
        print("  PUSHOVER_API_TOKEN=your_api_token")
        print("\nKeys erhältlich unter: https://pushover.net/")
    else:
        print("\n[1/3] Sende Test-Benachrichtigung...")
        notifier.test_notification()
        
        print("\n[2/3] Simuliere Entry-Signal...")
        notifier.send_entry_signal(
            symbol="AAPL",
            price=175.50,
            quantity=10,
            reason="MA Crossover + RSI < 30",
            stop_loss=171.60,
            take_profit=184.00
        )
        
        print("\n[3/3] Simuliere Exit-Signal (Gewinn)...")
        notifier.send_exit_signal(
            symbol="AAPL",
            price=184.50,
            quantity=10,
            entry_price=175.50,
            pnl=90.00,
            pnl_pct=5.13,
            reason="Take Profit erreicht"
        )
        
        print("\n✓ Alle Benachrichtigungen gesendet!")
        print("Prüfe dein Smartphone 📱")
