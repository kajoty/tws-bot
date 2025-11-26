"""
Position Manager für Options-Trading.
Ermöglicht manuelles Eintragen von Positionen und trackt diese.
"""

import os
import sys
from datetime import datetime
from typing import Optional, Dict, List
import logging

import config
import options_config as opt_config
from database import DatabaseManager
from pushover_notifier import PushoverNotifier

# Logging Setup
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PositionManager:
    """Verwaltet Options-Positionen: Entry, Tracking, Exit."""
    
    def __init__(self, use_tws_account_size: bool = False):
        """
        Args:
            use_tws_account_size: Wenn True, wird Account Size von TWS geholt statt aus .env
        """
        self.db = DatabaseManager()
        self.notifier = PushoverNotifier() if config.PUSHOVER_USER_KEY else None
        
        # Account Size Management
        self.account_size = config.ACCOUNT_SIZE
        self.use_tws_account_size = use_tws_account_size
        
        if use_tws_account_size:
            self._update_account_size_from_tws()
        
        logger.info(f"[OK] Position Manager initialisiert (Account Size: ${self.account_size:,.2f})")
    
    def _update_account_size_from_tws(self):
        """Holt aktuelle Account Size von TWS."""
        try:
            from account_data_manager import get_account_size_from_tws
            
            tws_account_size = get_account_size_from_tws()
            
            if tws_account_size and tws_account_size > 0:
                self.account_size = tws_account_size
                logger.info(f"[OK] Account Size von TWS aktualisiert: ${self.account_size:,.2f}")
            else:
                logger.warning(f"[WARNUNG] Konnte Account Size nicht von TWS holen - nutze .env Wert: ${self.account_size:,.2f}")
                
        except Exception as e:
            logger.error(f"[FEHLER] TWS Account Size Abruf fehlgeschlagen: {e}")
            logger.info(f"[INFO] Nutze Account Size aus .env: ${self.account_size:,.2f}")
    
    # ========================================================================
    # POSITION ENTRY (Manuell nach Trade-Ausführung)
    # ========================================================================
    
    def enter_position(self, 
                       symbol: str,
                       position_type: str,  # LONG_PUT, LONG_CALL, BEAR_CALL_SPREAD
                       entry_premium: float,
                       entry_underlying_price: float,
                       strike: float,
                       expiry: str,  # YYYYMMDD
                       right: str,  # P oder C
                       quantity: int = 1,
                       short_strike: Optional[float] = None,
                       long_strike: Optional[float] = None) -> int:
        """
        Trägt neue Position in Datenbank ein.
        
        Args:
            symbol: Underlying (z.B. AAPL)
            position_type: LONG_PUT, LONG_CALL, BEAR_CALL_SPREAD
            entry_premium: Gezahlte/Erhaltene Prämie (USD)
            entry_underlying_price: Aktienkurs bei Entry
            strike: Strike Price (für Single Options)
            expiry: Expiration Date (YYYYMMDD)
            right: P (Put) oder C (Call)
            quantity: Anzahl Kontrakte (Standard: 1)
            short_strike: Für Spreads - Short Strike
            long_strike: Für Spreads - Long Strike
            
        Returns:
            Position ID
        """
        # Berechne DTE
        try:
            exp_date = datetime.strptime(expiry, '%Y%m%d')
            dte = (exp_date - datetime.now()).days
        except:
            logger.error(f"[FEHLER] Ungültiges Expiry-Format: {expiry}")
            return -1
        
        # Berechne Stop-Loss und Take-Profit basierend auf Strategie
        if position_type == "LONG_PUT":
            # Stop Loss: Underlying steigt über 52W-Hoch + X%
            stop_loss_underlying = entry_underlying_price * (1 + opt_config.PUT_STOP_LOSS_PCT)
            # Take Profit: Premium steigt um X%
            take_profit_premium = entry_premium * (1 + opt_config.PUT_TAKE_PROFIT_PCT)
            auto_close_dte = opt_config.PUT_AUTO_CLOSE_DTE
            
        elif position_type == "LONG_CALL":
            # Stop Loss: Underlying fällt unter 52W-Tief - X%
            stop_loss_underlying = entry_underlying_price * (1 - opt_config.CALL_STOP_LOSS_PCT)
            # Take Profit: Premium steigt um X%
            take_profit_premium = entry_premium * (1 + opt_config.CALL_TAKE_PROFIT_PCT)
            auto_close_dte = opt_config.CALL_AUTO_CLOSE_DTE
            
        elif position_type == "BEAR_CALL_SPREAD":
            # Stop Loss: Underlying erreicht Long Strike
            stop_loss_underlying = long_strike if long_strike else strike * 1.1
            # Take Profit: 50-75% der eingenommenen Prämie als Gewinn
            take_profit_premium = entry_premium * (1 - opt_config.SPREAD_TAKE_PROFIT_MIN_PCT)
            auto_close_dte = opt_config.SPREAD_AUTO_CLOSE_DTE
            
        else:
            logger.error(f"[FEHLER] Unbekannter Position Type: {position_type}")
            return -1
        
        # Berechne Max Risk
        if position_type == "BEAR_CALL_SPREAD" and short_strike and long_strike:
            spread_type = "BEAR_CALL_SPREAD"
            strike_diff = long_strike - short_strike
            max_risk = (strike_diff * 100) - entry_premium  # Premium ist Credit
            net_premium = entry_premium  # Bei Spread = Credit received
        else:
            spread_type = None
            max_risk = entry_premium * quantity * 100  # Bei Long Options = Prämie gezahlt
            net_premium = entry_premium
        
        # Position in DB speichern
        position_data = {
            'symbol': symbol,
            'position_type': position_type,
            'option_type': position_type,
            'strike': strike,
            'expiry': expiry,
            'right': right,
            'entry_premium': entry_premium,
            'entry_underlying_price': entry_underlying_price,
            'dte_at_entry': dte,
            'quantity': quantity,
            'stop_loss_underlying': stop_loss_underlying,
            'take_profit_premium': take_profit_premium,
            'auto_close_dte': auto_close_dte,
            'current_premium': entry_premium,
            'current_underlying_price': entry_underlying_price,
            'current_dte': dte,
            'pnl': 0.0,
            'pnl_pct': 0.0,
            'status': 'OPEN',
            'short_strike': short_strike,
            'long_strike': long_strike,
            'spread_type': spread_type,
            'net_premium': net_premium,
            'max_risk': max_risk
        }
        
        position_id = self.db.save_options_position(position_data)
        
        logger.info(f"\n{'='*70}")
        logger.info(f"[OK] Position eingetragen: {position_type} {symbol}")
        logger.info(f"  Strike: {strike} | Expiry: {expiry} | DTE: {dte}")
        logger.info(f"  Entry Premium: ${entry_premium:.2f}")
        logger.info(f"  Entry Underlying: ${entry_underlying_price:.2f}")
        logger.info(f"  Stop Loss: ${stop_loss_underlying:.2f}")
        logger.info(f"  Take Profit Premium: ${take_profit_premium:.2f}")
        logger.info(f"  Max Risk: ${max_risk:.2f}")
        logger.info(f"  Position ID: {position_id}")
        logger.info(f"{'='*70}\n")
        
        # Pushover Notification
        if self.notifier:
            self.notifier.send_notification(
                title=f"[POSITION OPENED] {symbol}",
                message=f"{position_type}\\n" +
                       f"Strike: {strike} Expiry: {expiry}\\n" +
                       f"Premium: ${entry_premium:.2f} | Max Risk: ${max_risk:.2f}",
                priority=0
            )
        
        return position_id
    
    # ========================================================================
    # POSITION TRACKING
    # ========================================================================
    
    def get_all_open_positions(self) -> List[Dict]:
        """Holt alle offenen Positionen aus DB."""
        return self.db.get_open_options_positions()
    
    def update_position(self,
                       position_id: int,
                       current_premium: float,
                       current_underlying_price: float) -> Dict:
        """
        Aktualisiert Position mit aktuellen Marktdaten.
        
        Returns:
            Dict mit Status: 'OK', 'STOP_LOSS', 'TAKE_PROFIT', 'AUTO_CLOSE'
        """
        # Hole Position aus DB
        positions = self.get_all_open_positions()
        position = next((p for p in positions if p['id'] == position_id), None)
        
        if not position:
            logger.error(f"[FEHLER] Position {position_id} nicht gefunden")
            return {'status': 'ERROR', 'message': 'Position nicht gefunden'}
        
        # Berechne DTE
        try:
            exp_date = datetime.strptime(position['expiry'], '%Y%m%d')
            current_dte = (exp_date - datetime.now()).days
        except:
            current_dte = position['current_dte']
        
        # Berechne P&L
        position_type = position['position_type']
        
        if position_type in ['LONG_PUT', 'LONG_CALL']:
            # Long Options: P&L = (Current Premium - Entry Premium) * 100 * Quantity
            pnl = (current_premium - position['entry_premium']) * 100 * position['quantity']
            pnl_pct = ((current_premium / position['entry_premium']) - 1) * 100
            
        elif position_type == 'BEAR_CALL_SPREAD':
            # Credit Spread: P&L = Entry Premium - Current Premium (wir wollen Spread günstiger zurückkaufen)
            pnl = (position['entry_premium'] - current_premium) * 100 * position['quantity']
            pnl_pct = (pnl / position['max_risk']) * 100 if position['max_risk'] > 0 else 0
        
        else:
            pnl = 0.0
            pnl_pct = 0.0
        
        # Update DB
        update_data = {
            'current_premium': current_premium,
            'current_underlying_price': current_underlying_price,
            'current_dte': current_dte,
            'pnl': pnl,
            'pnl_pct': pnl_pct
        }
        
        self.db.update_options_position(position_id, update_data)
        
        # Prüfe Exit-Bedingungen
        exit_reason = None
        
        # 1. Stop Loss Check (Underlying)
        if position['stop_loss_underlying']:
            if position_type in ['LONG_PUT', 'BEAR_CALL_SPREAD']:
                # Stop wenn Underlying ÜBER Stop Loss
                if current_underlying_price >= position['stop_loss_underlying']:
                    exit_reason = 'STOP_LOSS'
            elif position_type == 'LONG_CALL':
                # Stop wenn Underlying UNTER Stop Loss
                if current_underlying_price <= position['stop_loss_underlying']:
                    exit_reason = 'STOP_LOSS'
        
        # 2. Take Profit Check (Premium)
        if position['take_profit_premium']:
            if position_type in ['LONG_PUT', 'LONG_CALL']:
                # Take Profit wenn Premium ÜBER Ziel
                if current_premium >= position['take_profit_premium']:
                    exit_reason = 'TAKE_PROFIT'
            elif position_type == 'BEAR_CALL_SPREAD':
                # Take Profit wenn Premium UNTER Ziel (günstiger zurückkaufen)
                if current_premium <= position['take_profit_premium']:
                    exit_reason = 'TAKE_PROFIT'
        
        # 3. Auto Close Check (DTE)
        if current_dte <= position['auto_close_dte'] and pnl < 0:
            exit_reason = 'AUTO_CLOSE_DTE'
        
        # 4. Expiration Check
        if current_dte <= 0:
            exit_reason = 'EXPIRED'
        
        result = {
            'status': exit_reason if exit_reason else 'OK',
            'position_id': position_id,
            'symbol': position['symbol'],
            'position_type': position_type,
            'current_premium': current_premium,
            'current_underlying_price': current_underlying_price,
            'current_dte': current_dte,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'exit_reason': exit_reason
        }
        
        # Sende Alert wenn Exit-Bedingung
        if exit_reason and self.notifier:
            self.notifier.send_notification(
                title=f"[EXIT ALERT] {position['symbol']}",
                message=f"{position_type} - {exit_reason}\\n" +
                       f"P&L: ${pnl:.2f} ({pnl_pct:+.1f}%)\\n" +
                       f"Underlying: ${current_underlying_price:.2f}\\n" +
                       f"Premium: ${current_premium:.2f} | DTE: {current_dte}",
                priority=1
            )
        
        return result
    
    def close_position(self, position_id: int, exit_reason: str = 'MANUAL'):
        """Schließt Position manuell oder automatisch."""
        self.db.close_options_position(position_id, exit_reason)
        logger.info(f"[OK] Position {position_id} geschlossen: {exit_reason}")
    
    # ========================================================================
    # PORTFOLIO TRACKING
    # ========================================================================
    
    def get_portfolio_summary(self, refresh_account_size: bool = False) -> Dict:
        """
        Berechnet Portfolio-Kennzahlen.
        
        Args:
            refresh_account_size: Wenn True UND use_tws_account_size=True, 
                                 wird Account Size von TWS neu geholt
        """
        # Optional: Account Size aktualisieren
        if refresh_account_size and self.use_tws_account_size:
            self._update_account_size_from_tws()
        
        positions = self.get_all_open_positions()
        
        total_max_risk = sum(p.get('max_risk', 0) for p in positions)
        total_pnl = sum(p.get('pnl', 0) for p in positions)
        
        account_size = self.account_size
        used_capital_pct = (total_max_risk / account_size) * 100 if account_size > 0 else 0
        available_capital = account_size - total_max_risk
        
        # Cushion = Verfügbares Kapital als % vom Account
        cushion_pct = (available_capital / account_size) * 100 if account_size > 0 else 0
        
        return {
            'account_size': account_size,
            'open_positions': len(positions),
            'total_max_risk': total_max_risk,
            'used_capital_pct': used_capital_pct,
            'available_capital': available_capital,
            'cushion_pct': cushion_pct,
            'total_pnl': total_pnl,
            'total_pnl_pct': (total_pnl / account_size) * 100 if account_size > 0 else 0
        }
    
    def print_portfolio_summary(self):
        """Gibt Portfolio-Übersicht aus."""
        summary = self.get_portfolio_summary()
        positions = self.get_all_open_positions()
        
        print("\n" + "="*70)
        print("  PORTFOLIO ÜBERSICHT")
        print("="*70)
        print(f"Account Size:        ${summary['account_size']:,.2f}")
        print(f"Offene Positionen:   {summary['open_positions']}")
        print(f"Total Max Risk:      ${summary['total_max_risk']:,.2f} ({summary['used_capital_pct']:.1f}%)")
        print(f"Verfügbar:           ${summary['available_capital']:,.2f}")
        print(f"Cushion:             {summary['cushion_pct']:.1f}%")
        print(f"Total P&L:           ${summary['total_pnl']:,.2f} ({summary['total_pnl_pct']:+.2f}%)")
        print("="*70)
        
        if positions:
            print("\nOFFENE POSITIONEN:")
            print("-"*70)
            for pos in positions:
                print(f"\n[{pos['id']}] {pos['symbol']} - {pos['position_type']}")
                print(f"  Strike: {pos['strike']} | Expiry: {pos['expiry']} | DTE: {pos.get('current_dte', 0)}")
                print(f"  Entry Premium: ${pos['entry_premium']:.2f} | Current: ${pos.get('current_premium', 0):.2f}")
                print(f"  P&L: ${pos.get('pnl', 0):.2f} ({pos.get('pnl_pct', 0):+.1f}%)")
                print(f"  Max Risk: ${pos.get('max_risk', 0):.2f}")
        
        print("\n" + "="*70 + "\n")


# ========================================================================
# CLI INTERFACE
# ========================================================================

def main():
    """Interaktives CLI für Position Management."""
    
    # Frage nach Account Size Quelle
    print("\n" + "="*70)
    print("  POSITION MANAGER - SETUP")
    print("="*70)
    print("Account Size Quelle:")
    print("1. Aus .env Datei (manuell konfiguriert)")
    print("2. Automatisch von TWS abrufen")
    print("="*70)
    
    choice = input("\nWahl (1 oder 2): ").strip()
    use_tws = (choice == "2")
    
    if use_tws:
        print("\n[INFO] Hole Account Size von TWS...")
    else:
        print(f"\n[INFO] Nutze Account Size aus .env: ${config.ACCOUNT_SIZE:,.2f}")
    
    manager = PositionManager(use_tws_account_size=use_tws)
    
    while True:
        print("\n" + "="*70)
        print("  POSITION MANAGER - TWS OPTIONS TRADING")
        print("="*70)
        print("1. Neue Position eintragen")
        print("2. Alle Positionen anzeigen")
        print("3. Position updaten")
        print("4. Position schließen")
        print("5. Portfolio-Übersicht")
        print("6. Account Size aktualisieren (von TWS)")
        print("0. Beenden")
        print("="*70)
        
        choice = input("\nWahl: ").strip()
        
        if choice == "1":
            # Neue Position
            print("\n--- NEUE POSITION EINTRAGEN ---")
            symbol = input("Symbol (z.B. AAPL): ").upper()
            
            print("Position Type:")
            print("1. LONG_PUT")
            print("2. LONG_CALL")
            print("3. BEAR_CALL_SPREAD")
            pos_type_choice = input("Wahl: ").strip()
            
            if pos_type_choice == "1":
                position_type = "LONG_PUT"
            elif pos_type_choice == "2":
                position_type = "LONG_CALL"
            elif pos_type_choice == "3":
                position_type = "BEAR_CALL_SPREAD"
            else:
                print("[FEHLER] Ungültige Wahl!")
                continue
            
            try:
                strike = float(input("Strike: "))
                expiry = input("Expiry (YYYYMMDD, z.B. 20250115): ")
                right = input("Right (P oder C): ").upper()
                entry_premium = float(input("Entry Premium (USD pro Kontrakt): "))
                entry_underlying = float(input("Underlying Preis bei Entry: "))
                quantity = int(input("Quantity (Anzahl Kontrakte, Standard 1): ") or "1")
                
                if position_type == "BEAR_CALL_SPREAD":
                    short_strike = float(input("Short Strike: "))
                    long_strike = float(input("Long Strike: "))
                else:
                    short_strike = None
                    long_strike = None
                
                position_id = manager.enter_position(
                    symbol=symbol,
                    position_type=position_type,
                    entry_premium=entry_premium,
                    entry_underlying_price=entry_underlying,
                    strike=strike,
                    expiry=expiry,
                    right=right,
                    quantity=quantity,
                    short_strike=short_strike,
                    long_strike=long_strike
                )
                
                print(f"\n[OK] Position {position_id} eingetragen!")
                
            except Exception as e:
                print(f"[FEHLER] {e}")
        
        elif choice == "2":
            # Alle Positionen
            positions = manager.get_all_open_positions()
            if not positions:
                print("\n[INFO] Keine offenen Positionen")
            else:
                print(f"\n[OK] {len(positions)} offene Position(en):")
                for pos in positions:
                    print(f"\n[{pos['id']}] {pos['symbol']} - {pos['position_type']}")
                    print(f"  Strike: {pos['strike']} | Expiry: {pos['expiry']}")
                    print(f"  Entry: ${pos['entry_premium']:.2f} | Current: ${pos.get('current_premium', 0):.2f}")
                    print(f"  P&L: ${pos.get('pnl', 0):.2f} ({pos.get('pnl_pct', 0):+.1f}%)")
        
        elif choice == "3":
            # Position updaten
            position_id = int(input("\nPosition ID: "))
            current_premium = float(input("Aktueller Premium: "))
            current_underlying = float(input("Aktueller Underlying Preis: "))
            
            result = manager.update_position(position_id, current_premium, current_underlying)
            
            print(f"\n[{result['status']}] {result['symbol']}")
            print(f"  P&L: ${result['pnl']:.2f} ({result['pnl_pct']:+.1f}%)")
            print(f"  DTE: {result['current_dte']}")
            
            if result['exit_reason']:
                print(f"  [ALERT] Exit-Bedingung: {result['exit_reason']}")
                close_now = input("  Position jetzt schließen? (j/n): ").lower()
                if close_now == 'j':
                    manager.close_position(position_id, result['exit_reason'])
        
        elif choice == "4":
            # Position schließen
            position_id = int(input("\nPosition ID: "))
            exit_reason = input("Exit Reason (z.B. MANUAL, STOP_LOSS): ") or "MANUAL"
            manager.close_position(position_id, exit_reason)
            print("[OK] Position geschlossen")
        
        elif choice == "5":
            # Portfolio-Übersicht
            manager.print_portfolio_summary()
        
        elif choice == "6":
            # Account Size aktualisieren
            if manager.use_tws_account_size:
                print("\n[INFO] Aktualisiere Account Size von TWS...")
                manager._update_account_size_from_tws()
                print(f"[OK] Account Size: ${manager.account_size:,.2f}")
            else:
                print("\n[INFO] TWS Account Size nicht aktiviert.")
                print("Beim Start Option 2 wählen um TWS Account Size zu nutzen.")
        
        elif choice == "0":
            print("\n[OK] Beende Position Manager")
            break
        
        else:
            print("[FEHLER] Ungültige Wahl!")


if __name__ == "__main__":
    main()
