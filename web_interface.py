"""
Einfaches Web-Interface für IB Trading Bot Monitoring.
Zeigt Live-Status, Positionen, Trades und Performance.
"""

from flask import Flask, render_template, jsonify
from database import DatabaseManager
import config
from datetime import datetime, timedelta
import pandas as pd
import os

app = Flask(__name__)
db = DatabaseManager()


@app.route('/')
def index():
    """Hauptseite - Dashboard."""
    return render_template('dashboard.html')


@app.route('/api/status')
def api_status():
    """API: Aktueller Bot-Status."""
    try:
        # Hole letzte Performance-Daten
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT equity, cash, positions_value, total_pnl, timestamp
            FROM performance
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        
        perf = cursor.fetchone()
        
        if perf:
            equity, cash, pos_value, total_pnl, timestamp = perf
        else:
            equity = config.ACCOUNT_SIZE
            cash = config.ACCOUNT_SIZE
            pos_value = 0
            total_pnl = 0
            timestamp = datetime.now().isoformat()
        
        # Anzahl offener Positionen
        cursor.execute("SELECT COUNT(*) FROM positions WHERE status = 'OPEN'")
        open_positions = cursor.fetchone()[0]
        
        # Anzahl Trades heute
        cursor.execute("""
            SELECT COUNT(*) FROM trades 
            WHERE DATE(timestamp) = DATE('now')
        """)
        trades_today = cursor.fetchone()[0]
        
        return jsonify({
            'status': 'running',
            'timestamp': timestamp,
            'equity': float(equity),
            'cash': float(cash),
            'positions_value': float(pos_value),
            'total_pnl': float(total_pnl),
            'open_positions': open_positions,
            'trades_today': trades_today,
            'mode': 'PAPER' if config.IS_PAPER_TRADING else 'LIVE',
            'strategy': config.TRADING_STRATEGY
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/positions')
def api_positions():
    """API: Offene Positionen."""
    try:
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT symbol, sec_type, quantity, avg_cost, current_price, 
                   unrealized_pnl, realized_pnl, last_updated
            FROM positions
            WHERE status = 'OPEN'
            ORDER BY unrealized_pnl DESC
        """)
        
        positions = []
        for row in cursor.fetchall():
            symbol, sec_type, qty, avg_cost, curr_price, unreal_pnl, real_pnl, updated = row
            
            if curr_price:
                pnl_pct = ((curr_price - avg_cost) / avg_cost * 100) if avg_cost else 0
            else:
                curr_price = avg_cost
                pnl_pct = 0
            
            positions.append({
                'symbol': symbol,
                'type': sec_type,
                'quantity': qty,
                'avg_cost': float(avg_cost),
                'current_price': float(curr_price),
                'value': float(curr_price * qty),
                'unrealized_pnl': float(unreal_pnl or 0),
                'pnl_pct': float(pnl_pct),
                'updated': updated
            })
        
        return jsonify(positions)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/trades')
def api_trades():
    """API: Letzte Trades."""
    try:
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT timestamp, symbol, sec_type, action, quantity, 
                   price, commission, strategy
            FROM trades
            ORDER BY timestamp DESC
            LIMIT 50
        """)
        
        trades = []
        for row in cursor.fetchall():
            ts, symbol, sec_type, action, qty, price, comm, strategy = row
            
            trades.append({
                'timestamp': ts,
                'symbol': symbol,
                'type': sec_type,
                'action': action,
                'quantity': qty,
                'price': float(price),
                'value': float(price * qty),
                'commission': float(comm),
                'strategy': strategy
            })
        
        return jsonify(trades)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/performance')
def api_performance():
    """API: Performance-Historie."""
    try:
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT timestamp, equity, cash, positions_value, 
                   total_pnl, daily_pnl
            FROM performance
            ORDER BY timestamp DESC
            LIMIT 100
        """)
        
        data = []
        for row in cursor.fetchall():
            ts, equity, cash, pos_val, total_pnl, daily_pnl = row
            data.append({
                'timestamp': ts,
                'equity': float(equity),
                'cash': float(cash),
                'positions_value': float(pos_val),
                'total_pnl': float(total_pnl or 0),
                'daily_pnl': float(daily_pnl or 0)
            })
        
        return jsonify(data)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/watchlist')
def api_watchlist():
    """API: Watchlist-Symbole."""
    try:
        watchlist_df = pd.read_csv('watchlist.csv')
        
        # Nur aktive Symbole
        active = watchlist_df[watchlist_df['enabled'] == True]
        
        symbols = []
        for _, row in active.head(20).iterrows():
            symbols.append({
                'symbol': row['symbol'],
                'name': row.get('name', ''),
                'sector': row.get('sector', ''),
                'market_cap': float(row.get('market_cap', 0))
            })
        
        return jsonify({
            'total': len(active),
            'symbols': symbols
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    print("="*60)
    print(" IB TRADING BOT - WEB INTERFACE")
    print("="*60)
    print(f" URL: http://localhost:5000")
    print(f" Mode: {'PAPER' if config.IS_PAPER_TRADING else 'LIVE'} Trading")
    print(f" Strategy: {config.TRADING_STRATEGY}")
    print("="*60)
    
    # Erstelle templates-Verzeichnis
    os.makedirs('templates', exist_ok=True)
    
    app.run(debug=False, host='0.0.0.0', port=5000)
