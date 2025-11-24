from flask import Flask, render_template, request
import pandas as pd
from database import DatabaseManager
from config import *
import logging
import plotly.graph_objects as go
from plotly.utils import PlotlyJSONEncoder
import json

app = Flask(__name__)

# Logger konfigurieren
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Datenbank initialisieren
db = DatabaseManager()

def calculate_indicators(df):
    """Berechne Indikatoren für DataFrame"""
    if len(df) < MA_LONG_PERIOD + 1:
        return df

    # Moving Averages
    df['ma_short'] = df['close'].ewm(span=MA_SHORT_PERIOD, adjust=False).mean()
    df['ma_long'] = df['close'].ewm(span=MA_LONG_PERIOD, adjust=False).mean()

    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(span=RSI_PERIOD, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(span=RSI_PERIOD, adjust=False).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = ema12 - ema26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

    return df

def check_entry_signal(df, symbol):
    """Prüfe auf Entry-Signal"""
    if len(df) < 2:
        return None

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    signals = []

    # MA Crossover
    if USE_MA_CROSSOVER and prev['ma_short'] <= prev['ma_long'] and latest['ma_short'] > latest['ma_long']:
        signals.append("MA Crossover")

    # RSI Oversold
    if USE_RSI and latest['rsi'] < RSI_OVERSOLD:
        signals.append("RSI Oversold")

    # MACD Crossover
    if USE_MACD and prev['macd'] <= prev['macd_signal'] and latest['macd'] > latest['macd_signal']:
        signals.append("MACD Crossover")

    if len(signals) >= MIN_SIGNALS_FOR_ENTRY:
        return {
            'type': 'entry',
            'symbol': symbol,
            'price': latest['close'],
            'signals': signals,
            'reason': ', '.join(signals)
        }
    return None

def calculate_hit_rate(df, symbol):
    """Berechne Trefferquote und aktive Indikatoren für Indikatoren"""
    if len(df) < 2:
        return 0.0, [], {}

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    active_indicators = []
    total_indicators = 0
    current_values = {
        'price': latest['close'],
        'rsi': latest['rsi'],
        'ma_short': latest['ma_short'],
        'ma_long': latest['ma_long'],
        'macd': latest['macd'],
        'macd_signal': latest['macd_signal']
    }

    # MA Crossover
    if USE_MA_CROSSOVER:
        total_indicators += 1
        if prev['ma_short'] <= prev['ma_long'] and latest['ma_short'] > latest['ma_long']:
            active_indicators.append("MA Crossover")

    # RSI Oversold
    if USE_RSI:
        total_indicators += 1
        if latest['rsi'] < RSI_OVERSOLD:
            active_indicators.append("RSI Oversold")

    # MACD Crossover
    if USE_MACD:
        total_indicators += 1
        if prev['macd'] <= prev['macd_signal'] and latest['macd'] > latest['macd_signal']:
            active_indicators.append("MACD Crossover")

    if total_indicators == 0:
        return 0.0, [], current_values

    rate = (len(active_indicators) / total_indicators) * 100
    return rate, active_indicators, current_values

def calculate_position_size(price):
    """Berechne Positionsgröße basierend auf Risiko"""
    risk_amount = ACCOUNT_SIZE * MAX_RISK_PER_TRADE_PCT
    stop_distance = price * 0.02  # Beispiel: 2% Stop-Loss
    quantity = int(risk_amount / stop_distance)
    stop_loss = price * 0.98
    take_profit = price * 1.04  # Beispiel: 4% Take-Profit
    return quantity, stop_loss, take_profit

def get_historical_signals(limit=10):
    """Lade historische Signale aus DB"""
    try:
        df = db.get_signals(days=30)  # Letzte 30 Tage
        if df.empty:
            return []
        
        signals = []
        for _, row in df.head(limit).iterrows():
            signals.append({
                'timestamp': row['timestamp'].strftime('%Y-%m-%d %H:%M:%S'),
                'symbol': row['symbol'],
                'type': row['signal_type'].lower(),
                'price': row['price'],
                'quantity': row['quantity'],
                'reason': row['reason'] or 'Unbekannt'
            })
        return signals
    except Exception as e:
        logger.error(f"Fehler beim Laden historischer Signale: {e}")
        return []

def create_price_chart(df, symbol):
    """Erstelle Preis-Chart mit Plotly"""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df['close'], mode='lines', name='Preis'))
    fig.add_trace(go.Scatter(x=df.index, y=df['ma_short'], mode='lines', name='MA Kurz'))
    fig.add_trace(go.Scatter(x=df.index, y=df['ma_long'], mode='lines', name='MA Lang'))
    fig.update_layout(title=f'Preis-Chart {symbol}', xaxis_title='Datum', yaxis_title='Preis')
    return json.dumps(fig, cls=PlotlyJSONEncoder)

@app.route('/')
def dashboard():
    """Haupt-Dashboard"""
    signals = []
    hit_rates = {'50': [], '60': [], '70': [], '80': [], '90': [], '100': []}
    watchlist = WATCHLIST_STOCKS
    total_tickers = len(watchlist)
    avg_rate = 0
    total_signals = 0

    for symbol in watchlist[:50]:  # Erhöht auf 50 für bessere Statistik
        try:
            # Lade historische Daten
            df = db.load_historical_data(symbol)
            if df is None or df.empty:
                continue

            # Berechne Indikatoren
            df = calculate_indicators(df)

            # Berechne Trefferquote und aktive Indikatoren
            rate, active, current = calculate_hit_rate(df, symbol)
            avg_rate += rate
            entry = {'symbol': symbol, 'rate': rate, 'indicators': ', '.join(active) if active else 'Keine', 'current': current}

            # Prüfe auf Entry-Signal
            entry_signal = check_entry_signal(df, symbol)
            if entry_signal:
                quantity, stop_loss, take_profit = calculate_position_size(entry_signal['price'])
                entry_signal.update({
                    'quantity': quantity,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit
                })
                signals.append(entry_signal)
                total_signals += 1

            # Kategorisiere nach Trefferquote
            if rate >= 100:
                hit_rates['100'].append(entry)
            elif rate >= 90:
                hit_rates['90'].append(entry)
            elif rate >= 80:
                hit_rates['80'].append(entry)
            elif rate >= 70:
                hit_rates['70'].append(entry)
            elif rate >= 60:
                hit_rates['60'].append(entry)
            elif rate >= 50:
                hit_rates['50'].append(entry)

        except Exception as e:
            logger.error(f"Fehler bei {symbol}: {e}")

    avg_rate = avg_rate / max(total_tickers, 1)
    historical_signals = get_historical_signals()

    # Filter basierend auf Query-Param
    filter_level = request.args.get('filter', 'all')
    if filter_level != 'all':
        hit_rates = {k: v for k, v in hit_rates.items() if k == filter_level}

    return render_template('dashboard.html', 
                         signals=signals, 
                         hit_rates=hit_rates, 
                         watchlist=watchlist[:10],
                         total_tickers=total_tickers,
                         avg_rate=avg_rate,
                         total_signals=total_signals,
                         historical_signals=historical_signals,
                         filter_level=filter_level)

@app.route('/chart/<symbol>')
def chart(symbol):
    """Zeige Chart für einen Ticker"""
    try:
        df = db.load_historical_data(symbol)
        if df is not None and not df.empty:
            df = calculate_indicators(df)
            chart_json = create_price_chart(df, symbol)
            return render_template('chart.html', symbol=symbol, chart_json=chart_json)
    except Exception as e:
        logger.error(f"Fehler beim Erstellen des Charts für {symbol}: {e}")
    return "Chart nicht verfügbar"

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)