"""
Zeigt das Trading-Tagebuch an: Alle offenen und geschlossenen Trades.
"""

import pandas as pd
import logging
from database import DatabaseManager
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def show_trading_journal(days: int = 7):
    """Zeigt Trading-Tagebuch der letzten N Tage."""
    
    db = DatabaseManager()
    
    try:
        # Hole alle Trades aus DB
        conn = db.conn
        cursor = conn.cursor()
        
        # Berechne Startdatum
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        query = """
            SELECT 
                timestamp,
                symbol,
                sec_type,
                action,
                quantity,
                price,
                commission,
                strategy
            FROM trades
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
        """
        
        cursor.execute(query, (start_date,))
        trades = cursor.fetchall()
        
        if not trades:
            logger.info(f"\n❌ Keine Trades in den letzten {days} Tagen gefunden.")
            return
        
        # Erstelle DataFrame
        df = pd.DataFrame(trades, columns=[
            'Timestamp', 'Symbol', 'Type', 'Action', 
            'Quantity', 'Price', 'Commission', 'Strategy'
        ])
        
        # Formatierung
        df['Timestamp'] = pd.to_datetime(df['Timestamp'])
        df['Value'] = df['Quantity'] * df['Price']
        
        # Zeige Zusammenfassung
        logger.info(f"\n{'='*80}")
        logger.info(f" TRADING-TAGEBUCH (Letzte {days} Tage)")
        logger.info(f"{'='*80}\n")
        
        logger.info(f"\nAnzahl Trades: {len(df)}")
        logger.info(f"Trades BUY:    {len(df[df['Action'] == 'BUY'])}")
        logger.info(f"Trades SELL:   {len(df[df['Action'] == 'SELL'])}")
        
        total_commission = df['Commission'].sum()
        
        logger.info(f"\nTotal Commission:  ${total_commission:,.2f}")
        
        # Gruppiere nach Strategie
        strategy_counts = df.groupby('Strategy').size().sort_values(ascending=False)
        if not strategy_counts.empty:
            logger.info(f"\nTrades nach Strategie:")
            for strategy, count in strategy_counts.items():
                logger.info(f"  {strategy:30s}: {count:>3d} Trades")
        
        # Zeige alle Trades
        logger.info(f"\n{'='*80}")
        logger.info(" ALLE TRADES")
        logger.info(f"{'='*80}\n")
        
        for _, trade in df.iterrows():
            timestamp = trade['Timestamp'].strftime('%Y-%m-%d %H:%M:%S')
            symbol = trade['Symbol']
            action = trade['Action']
            quantity = int(trade['Quantity'])
            price = trade['Price']
            value = trade['Value']
            commission = trade['Commission']
            strategy = trade['Strategy']
            
            action_symbol = "📈" if action == "BUY" else "📉"
            
            logger.info(
                f"{timestamp} | {action_symbol} {action:4s} {quantity:4d} {symbol:6s} "
                f"@ ${price:>8.2f} = ${value:>10,.2f} | "
                f"Com: ${commission:>6.2f} | {strategy}"
            )
        
        logger.info(f"\n{'='*80}\n")
        
    except Exception as e:
        logger.error(f"✗ Fehler beim Laden des Trading-Tagebuchs: {e}", exc_info=True)


def show_open_positions():
    """Zeigt alle offenen Positionen aus der positions-Tabelle."""
    
    db = DatabaseManager()
    
    try:
        conn = db.conn
        cursor = conn.cursor()
        
        query = """
            SELECT 
                timestamp,
                symbol,
                sec_type,
                quantity,
                entry_price,
                stop_loss,
                take_profit,
                current_price,
                unrealized_pnl
            FROM positions
            WHERE status = 'OPEN'
            ORDER BY timestamp DESC
        """
        
        cursor.execute(query)
        positions = cursor.fetchall()
        
        if not positions:
            logger.info(f"\n❌ Keine offenen Positionen.")
            return
        
        logger.info(f"\n{'='*80}")
        logger.info(f" OFFENE POSITIONEN")
        logger.info(f"{'='*80}\n")
        
        total_value = 0
        total_unrealized_pnl = 0
        
        for pos in positions:
            timestamp, symbol, sec_type, quantity, entry_price, stop_loss, take_profit, current_price, unrealized_pnl = pos
            
            if current_price is None:
                current_price = entry_price
                unrealized_pnl = 0.0
            
            value = quantity * current_price
            total_value += value
            total_unrealized_pnl += (unrealized_pnl or 0)
            
            pnl_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
            pnl_symbol = "🟢" if unrealized_pnl and unrealized_pnl > 0 else "🔴" if unrealized_pnl and unrealized_pnl < 0 else "⚪"
            
            logger.info(
                f"{symbol:6s} ({sec_type:3s}) | "
                f"Qty: {quantity:4d} | Entry: ${entry_price:>8.2f} | "
                f"Current: ${current_price:>8.2f} | "
                f"{pnl_symbol} PnL: ${(unrealized_pnl or 0):>9.2f} ({pnl_pct:>+6.2f}%) | "
                f"Stop: ${stop_loss:>8.2f}"
            )
        
        logger.info(f"\n{'-'*80}")
        logger.info(f"Total Position Value:    ${total_value:>12,.2f}")
        logger.info(f"Total Unrealized PnL:    ${total_unrealized_pnl:>12,.2f}")
        logger.info(f"{'='*80}\n")
        
    except Exception as e:
        logger.error(f"✗ Fehler beim Laden der offenen Positionen: {e}", exc_info=True)


if __name__ == '__main__':
    import sys
    
    days = 7
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except ValueError:
            logger.error("Ungültige Tagesanzahl. Verwende Standard: 7 Tage")
    
    # Zeige offene Positionen
    show_open_positions()
    
    # Zeige Trading-Tagebuch
    show_trading_journal(days=days)
