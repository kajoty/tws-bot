"""
Risiko- und Positionsmanagement für den IB Trading Bot.
Berechnet Positionsgrößen, verwaltet Risikolimits und überwacht das Portfolio.
"""

import logging
from typing import Dict, Optional, Tuple
import config

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Verwaltet Risiko- und Positionsgrößenberechnungen basierend auf
    Kontogröße, Risikotoleranz und aktuellen Positionen.
    """

    def __init__(
        self,
        account_size: float = config.ACCOUNT_SIZE,
        max_risk_per_trade_pct: float = config.MAX_RISK_PER_TRADE_PCT,
        max_positions: int = config.MAX_CONCURRENT_POSITIONS,
        commission_per_order: float = config.COMMISSION_PER_ORDER
    ):
        """
        Initialisiert den RiskManager.

        Args:
            account_size: Startkapital
            max_risk_per_trade_pct: Maximales Risiko pro Trade (z.B. 0.01 = 1%)
            max_positions: Maximale Anzahl gleichzeitiger Positionen
            commission_per_order: Kommission pro Order
        """
        self.account_size = account_size
        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.max_positions = max_positions
        self.commission_per_order = commission_per_order

        # Aktueller Portfolio-Status
        self.current_positions: Dict[str, Dict] = {}
        self.cash_available = account_size
        self.total_equity = account_size

        logger.info(
            f"RiskManager initialisiert: "
            f"Account=${account_size:,.2f}, "
            f"Max Risk/Trade={max_risk_per_trade_pct*100:.2f}%, "
            f"Max Positions={max_positions}"
        )

    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss_price: float,
        risk_multiplier: float = 1.0
    ) -> Tuple[int, float, Dict]:
        """
        Berechnet die optimale Positionsgröße basierend auf Risiko.

        Formel: Position Size = (Account Size * Risk%) / (Entry - Stop Loss)

        Args:
            symbol: Tickersymbol
            entry_price: Geplanter Einstiegspreis
            stop_loss_price: Stop-Loss-Preis
            risk_multiplier: Multiplikator für das Risiko (Standard: 1.0)

        Returns:
            Tuple: (quantity, risk_amount, details_dict)
        """
        try:
            if entry_price <= 0 or stop_loss_price <= 0:
                logger.error(f"Ungültige Preise: Entry={entry_price}, Stop={stop_loss_price}")
                return 0, 0.0, {}

            risk_per_share = abs(entry_price - stop_loss_price)
            if risk_per_share == 0:
                logger.warning(f"Kein Risiko definiert für {symbol}")
                return 0, 0.0, {}

            max_risk_amount = self.total_equity * self.max_risk_per_trade_pct * risk_multiplier
            quantity = int(max_risk_amount / risk_per_share)
            total_commission = 2 * self.commission_per_order
            required_capital = (quantity * entry_price) + total_commission

            if required_capital > self.cash_available:
                affordable_quantity = int((self.cash_available - total_commission) / entry_price)
                quantity = min(quantity, affordable_quantity)
                logger.warning(f"Positionsgröße reduziert wegen Kapitalmangel: {quantity} Aktien")

            if quantity * entry_price < config.MIN_POSITION_SIZE:
                logger.warning(f"Position zu klein für {symbol}: ${quantity * entry_price:.2f} < ${config.MIN_POSITION_SIZE}")
                return 0, 0.0, {}

            actual_risk = quantity * risk_per_share
            actual_cost = quantity * entry_price + total_commission

            details = {
                'symbol': symbol,
                'quantity': quantity,
                'entry_price': entry_price,
                'stop_loss_price': stop_loss_price,
                'risk_per_share': risk_per_share,
                'max_risk_amount': max_risk_amount,
                'actual_risk': actual_risk,
                'actual_cost': actual_cost,
                'commission': total_commission,
                'risk_pct': (actual_risk / self.total_equity) * 100
            }

            logger.info(
                f"Position berechnet für {symbol}: "
                f"{quantity} Aktien @ ${entry_price:.2f}, "
                f"Risiko=${actual_risk:.2f} ({details['risk_pct']:.2f}%)"
            )

            return quantity, actual_risk, details

        except Exception as e:
            logger.error(f"Fehler bei Positionsberechnung für {symbol}: {e}")
            return 0, 0.0, {}

    def can_open_position(self, symbol: str) -> Tuple[bool, str]:
        """Prüft, ob eine neue Position eröffnet werden kann."""
        if len(self.current_positions) >= self.max_positions:
            return False, f"Maximale Anzahl Positionen erreicht ({self.max_positions})"

        if symbol in self.current_positions:
            return False, f"Position in {symbol} bereits vorhanden"

        if self.cash_available < config.MIN_POSITION_SIZE:
            return False, f"Unzureichendes Kapital: ${self.cash_available:.2f}"

        return True, "OK"

    def add_position(self, symbol: str, quantity: int, entry_price: float, stop_loss: Optional[float] = None) -> bool:
        """Fügt eine Position zum Portfolio hinzu."""
        try:
            position_value = quantity * entry_price
            commission = self.commission_per_order

            self.current_positions[symbol] = {
                'quantity': quantity,
                'entry_price': entry_price,
                'current_price': entry_price,
                'stop_loss': stop_loss,
                'position_value': position_value,
                'unrealized_pnl': 0.0,
                'commission_paid': commission
            }

            self.cash_available -= (position_value + commission)

            logger.info(
                f"Position hinzugefügt: {quantity} {symbol} @ ${entry_price:.2f}, "
                f"Verbleibendes Cash: ${self.cash_available:.2f}"
            )

            return True

        except Exception as e:
            logger.error(f"Fehler beim Hinzufügen der Position {symbol}: {e}")
            return False

    def remove_position(self, symbol: str, exit_price: float) -> Optional[Dict]:
        """Entfernt eine Position aus dem Portfolio."""
        try:
            if symbol not in self.current_positions:
                logger.warning(f"Position {symbol} nicht gefunden")
                return None

            position = self.current_positions[symbol]
            quantity = position['quantity']
            entry_price = position['entry_price']

            gross_pnl = (exit_price - entry_price) * quantity
            commission = position['commission_paid'] + self.commission_per_order
            net_pnl = gross_pnl - commission

            proceeds = quantity * exit_price - self.commission_per_order
            self.cash_available += proceeds

            del self.current_positions[symbol]

            pnl_details = {
                'symbol': symbol,
                'quantity': quantity,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'gross_pnl': gross_pnl,
                'commission': commission,
                'net_pnl': net_pnl,
                'return_pct': (net_pnl / (quantity * entry_price)) * 100
            }

            logger.info(f"Position geschlossen: {symbol}, PnL=${net_pnl:.2f} ({pnl_details['return_pct']:.2f}%)")

            return pnl_details

        except Exception as e:
            logger.error(f"Fehler beim Entfernen der Position {symbol}: {e}")
            return None

    def update_position_price(self, symbol: str, current_price: float) -> bool:
        """Aktualisiert den aktuellen Preis einer Position."""
        try:
            if symbol not in self.current_positions:
                return False

            position = self.current_positions[symbol]
            position['current_price'] = current_price

            quantity = position['quantity']
            entry_price = position['entry_price']
            position['unrealized_pnl'] = (current_price - entry_price) * quantity
            position['position_value'] = current_price * quantity

            return True

        except Exception as e:
            logger.error(f"Fehler beim Aktualisieren des Preises für {symbol}: {e}")
            return False

    def get_portfolio_summary(self) -> Dict:
        """Erstellt eine Zusammenfassung des Portfolios."""
        total_position_value = sum(pos['position_value'] for pos in self.current_positions.values())
        total_unrealized_pnl = sum(pos['unrealized_pnl'] for pos in self.current_positions.values())

        self.total_equity = self.cash_available + total_position_value

        summary = {
            'total_equity': self.total_equity,
            'cash_available': self.cash_available,
            'positions_value': total_position_value,
            'unrealized_pnl': total_unrealized_pnl,
            'num_positions': len(self.current_positions),
            'cash_pct': (self.cash_available / self.total_equity) * 100,
            'positions': self.current_positions.copy()
        }

        return summary

    def check_stop_loss(self, symbol: str, current_price: float) -> bool:
        """Prüft, ob der Stop-Loss für eine Position ausgelöst wurde."""
        if symbol not in self.current_positions:
            return False

        position = self.current_positions[symbol]
        stop_loss = position.get('stop_loss')

        if stop_loss is None:
            return False

        if position['quantity'] > 0 and current_price <= stop_loss:
            logger.warning(f"Stop-Loss ausgelöst für {symbol}: Preis ${current_price:.2f} <= Stop ${stop_loss:.2f}")
            return True

        if position['quantity'] < 0 and current_price >= stop_loss:
            logger.warning(f"Stop-Loss ausgelöst für {symbol}: Preis ${current_price:.2f} >= Stop ${stop_loss:.2f}")
            return True

        return False

    def update_account_size(self, new_equity: float):
        """Aktualisiert die Kontogröße."""
        old_equity = self.total_equity
        self.total_equity = new_equity
        self.account_size = new_equity

        logger.info(f"Account Size aktualisiert: ${old_equity:,.2f} -> ${new_equity:,.2f}")
