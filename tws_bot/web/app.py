from flask import Flask, render_template, request
import pandas as pd
from ..data.database import DatabaseManager
from ..config.settings import *
from ..core.indicators import calculate_indicators
from ..core.signals import check_entry_signal
from ..api.tws_connector import TWSConnector
import logging
import plotly.graph_objects as go
from plotly.utils import PlotlyJSONEncoder
import json
from datetime import datetime

# Import OptionsScanner lazy (vermeidet zirkuläre Imports)
_options_scanner = None

def get_options_scanner():
    """Lazy loading des OptionsScanner."""
    global _options_scanner
    if _options_scanner is None:
        import sys
        import os
        sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from options_scanner import OptionsScanner
        _options_scanner = OptionsScanner()
        _options_scanner.connect_to_tws()  # Verbindung herstellen für fundamentale Daten
    return _options_scanner

app = Flask(__name__, template_folder='templates')

# Logger konfigurieren
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Datenbank initialisieren
db = DatabaseManager()

# OptionsScanner lazy initialisieren
# scanner = OptionsScanner()  # Entfernt wegen zirkulärem Import
# scanner.connect_to_tws()  # Verbindung herstellen für fundamentale Daten

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
        'ma_long': latest['ma_long']
    }
    if USE_MACD:
        current_values['macd'] = latest['macd']
        current_values['macd_signal'] = latest['macd_signal']

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

def get_historical_signals(limit=50):
    """Lade historische Signale aus DB"""
    try:
        df = db.get_signals(days=90)  # Letzte 90 Tage statt 30
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

def get_market_overview():
    """Erstelle Marktübersicht für alle Watchlist-Symbole"""
    overview = []
    try:
        for symbol in WATCHLIST_STOCKS:  # Alle verfügbaren Ticker
            try:
                df = db.load_historical_data(symbol)
                if df is None or df.empty:
                    continue
                
                df = calculate_indicators(df)
                latest = df.iloc[-1]
                
                # Berechne Performance
                if len(df) > 1:
                    prev_day = df.iloc[-2]['close']
                    daily_change = ((latest['close'] - prev_day) / prev_day) * 100
                else:
                    daily_change = 0
                
                # Fundamentale Scores laden
                fundamental_scores = {'overall': 0, 'value': 0, 'growth': 0, 'quality': 0, 'momentum': 0, 'risk': 0}
                fundamental_ratings = {'value': 'N/A', 'growth': 'N/A', 'quality': 'N/A', 'risk': 'N/A'}
                recommendations = []
                
                try:
                    scanner = get_options_scanner()
                    if symbol in scanner.fundamental_data_cache:
                        fundamental_scores = scanner._calculate_fundamental_score(symbol)
                        report = scanner.get_fundamental_analysis_report(symbol)
                        fundamental_ratings = report.get('ratings', {})
                        recommendations = report.get('recommendations', [])
                except Exception as e:
                    logger.warning(f"Fundamentale Analyse fehlgeschlagen für {symbol}: {e}")
                
                overview.append({
                    'symbol': symbol,
                    'price': latest['close'],
                    'change': daily_change,
                    'rsi': latest.get('rsi', 0),
                    'ma_short': latest.get('ma_short', 0),
                    'ma_long': latest.get('ma_long', 0),
                    'volume': latest.get('volume', 0),
                    'atr': latest.get('atr', 0) if 'atr' in latest and not pd.isna(latest['atr']) else 0,
                    # Fundamentale Daten
                    'fundamental_scores': fundamental_scores,
                    'fundamental_ratings': fundamental_ratings,
                    'recommendations': recommendations
                })
            except Exception as e:
                logger.error(f"Fehler bei {symbol}: {e}")
                continue
    except Exception as e:
        logger.error(f"Fehler bei Marktübersicht: {e}")
    
    return overview

def get_performance_stats():
    """Berechne Performance-Statistiken"""
    try:
        signals_df = db.get_signals(days=365)  # Letzte 365 Tage
        
        if signals_df.empty:
            return {
                'total_signals': 0,
                'win_rate': 0,
                'avg_return': 0,
                'total_return': 0,
                'best_symbol': 'N/A',
                'worst_symbol': 'N/A'
            }
        
        # Gruppiere nach Symbol
        symbol_stats = signals_df.groupby('symbol').agg({
            'price': ['count', 'mean'],
            'signal_type': lambda x: (x == 'ENTRY').sum()
        }).round(2)
        
        total_signals = len(signals_df)
        entry_signals = len(signals_df[signals_df['signal_type'] == 'ENTRY'])
        
        # Vereinfachte Win-Rate Berechnung (könnte komplexer sein)
        win_rate = (entry_signals / max(total_signals, 1)) * 100
        
        return {
            'total_signals': total_signals,
            'win_rate': round(win_rate, 1),
            'avg_return': 0,  # Placeholder für komplexere Berechnung
            'total_return': 0,  # Placeholder für komplexere Berechnung
            'best_symbol': symbol_stats.index[0] if not symbol_stats.empty else 'N/A',
            'worst_symbol': symbol_stats.index[-1] if len(symbol_stats) > 1 else 'N/A'
        }
    except Exception as e:
        logger.error(f"Fehler bei Performance-Stats: {e}")
        return {
            'total_signals': 0,
            'win_rate': 0,
            'avg_return': 0,
            'total_return': 0,
            'best_symbol': 'N/A',
            'worst_symbol': 'N/A'
        }

def create_price_chart(df, symbol):
    """Erstelle Preis-Chart mit Plotly - vereinfachte Version für Web"""
    # Begrenze Daten auf letzte 50 Einträge für Performance
    df = df.tail(50) if len(df) > 50 else df

    # Konvertiere Daten zu Listen - handle verschiedene Index-Typen
    if hasattr(df.index, 'strftime'):
        # DatetimeIndex
        dates = [d.strftime('%Y-%m-%d') for d in df.index]
    else:
        # Integer oder anderer Index
        dates = [str(i) for i in range(len(df))]
    close_prices = df['close'].round(2).tolist()
    ma_short = df['ma_short'].round(2).tolist()
    ma_long = df['ma_long'].round(2).tolist()

    # Erstelle einfaches JSON ohne komplexe Plotly-Objekte
    chart_data = {
        'data': [
            {
                'x': dates,
                'y': close_prices,
                'type': 'scatter',
                'mode': 'lines',
                'name': 'Preis',
                'line': {'color': 'blue'}
            },
            {
                'x': dates,
                'y': ma_short,
                'type': 'scatter',
                'mode': 'lines',
                'name': 'MA Kurz',
                'line': {'color': 'orange', 'dash': 'dot'}
            },
            {
                'x': dates,
                'y': ma_long,
                'type': 'scatter',
                'mode': 'lines',
                'name': 'MA Lang',
                'line': {'color': 'red', 'dash': 'dash'}
            }
        ],
        'layout': {
            'title': f'Preis-Chart {symbol}',
            'xaxis': {'title': 'Datum'},
            'yaxis': {'title': 'Preis'},
            'showlegend': True,
            'margin': {'l': 50, 'r': 50, 't': 50, 'b': 50}
        }
    }

    return json.dumps(chart_data)

@app.route('/')
def dashboard():
    """Haupt-Dashboard"""
    try:
        # Initialisiere Variablen
        signals = []
        hit_rates = {'50': [], '60': [], '70': [], '80': [], '90': [], '100': []}
        watchlist = WATCHLIST_STOCKS
        total_tickers = len(watchlist)
        avg_rate = 0
        total_signals = 0

        # Initialisiere portfolio_data frühzeitig
        portfolio_data = {
            'net_liquidation': 0,
            'total_cash': 0,
            'buying_power': 0,
            'available_funds': 0,
            'cushion': 0,
            'portfolio_value': 0,
            'num_positions': 0,
            'positions': []
        }

        # Prüfe Datenbankverbindung
        try:
            db_status = db.health_check()
            if db_status.get('status') != 'healthy':
                logger.error("Datenbankverbindung fehlgeschlagen")
                return render_template('error.html',
                                     error_message="Datenbank ist nicht verfügbar. Bitte prüfen Sie die Datenbankverbindung.",
                                     error_type="DatabaseError",
                                     timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                     show_details=True)
        except Exception as db_error:
            logger.error(f"Datenbank-Health-Check fehlgeschlagen: {db_error}")
            return render_template('error.html',
                                 error_message="Datenbankverbindung konnte nicht überprüft werden.",
                                 error_type="DatabaseError",
                                 error_details=str(db_error),
                                 timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                 show_details=True)

        # Verarbeite ALLE Watchlist-Symbole (vollständige Abdeckung)
        processed_count = 0
        for symbol in watchlist:  # Alle 425 Ticker verarbeiten
            try:
                processed_count += 1
                if processed_count % 50 == 0:
                    logger.info(f"Verarbeitet: {processed_count}/{len(watchlist)} Symbole")
                
                # Lade historische Daten
                df = db.load_historical_data(symbol)
                if df is None or df.empty:
                    logger.warning(f"Keine historischen Daten für {symbol} verfügbar")
                    continue

                # Berechne Indikatoren
                df = calculate_indicators(df)

                # Berechne Trefferquote und aktive Indikatoren
                rate, active, current = calculate_hit_rate(df, symbol)
                avg_rate += rate
                entry = {'symbol': symbol, 'rate': rate, 'indicators': ', '.join(active) if active else 'Keine', 'current': current}

                # Prüfe auf Entry-Signal
                entry_signal = check_entry_signal(symbol, df, None, portfolio_data)
                if entry_signal:
                    logger.info(f"Entry-Signal generiert für {symbol}: {entry_signal}")
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

            except Exception as symbol_error:
                logger.error(f"Fehler bei der Verarbeitung von {symbol}: {symbol_error}")
                # Fortfahren mit nächstem Symbol statt komplett abzubrechen

        logger.info(f"Verarbeitung abgeschlossen: {processed_count} Symbole, {total_signals} Signale generiert")

        # Berechne Durchschnittsrate
        avg_rate = avg_rate / max(total_tickers, 1)

        # Lade historische Signale (erhöht auf 50)
        try:
            historical_signals = get_historical_signals(limit=50)
        except Exception as hist_error:
            logger.error(f"Fehler beim Laden historischer Signale: {hist_error}")
            historical_signals = []

        # Lade Marktübersicht
        try:
            market_overview = get_market_overview()
        except Exception as market_error:
            logger.error(f"Fehler beim Laden der Marktübersicht: {market_error}")
            market_overview = []

        # Lade Performance-Statistiken
        try:
            performance_stats = get_performance_stats()
        except Exception as perf_error:
            logger.error(f"Fehler beim Laden der Performance-Stats: {perf_error}")
            performance_stats = {
                'total_signals': 0, 'win_rate': 0, 'avg_return': 0,
                'total_return': 0, 'best_symbol': 'N/A', 'worst_symbol': 'N/A'
            }

        # Lade Options-Signale
        try:
            options_signals = db.get_options_signals(days=30)  # Letzte 30 Tage
            options_signals_list = options_signals.to_dict('records') if not options_signals.empty else []
        except Exception as opt_error:
            logger.error(f"Fehler beim Laden von Options-Signalen: {opt_error}")
            options_signals_list = []

        # Lade Options-Statistiken
        try:
            options_stats = db.get_options_signal_stats(days=30)
        except Exception as opt_stats_error:
            logger.error(f"Fehler beim Laden von Options-Statistiken: {opt_stats_error}")
            options_stats = {
                'total_signals': 0, 'long_put_signals': 0, 'long_call_signals': 0,
                'bear_call_spread_signals': 0, 'avg_iv_rank': 0, 'avg_proximity_pct': 0
            }

        # Lade Portfolio-Daten von TWS
        try:
            tws_connector = TWSConnector()
            if tws_connector.connect_to_tws():
                portfolio_data = tws_connector.get_portfolio_data()
                tws_connector.disconnect()
                logger.info("Portfolio-Daten erfolgreich geladen")
            else:
                logger.warning("TWS Verbindung für Portfolio-Daten fehlgeschlagen")
                # portfolio_data bleibt mit Default-Werten
        except Exception as port_error:
            logger.error(f"Fehler beim Laden von Portfolio-Daten: {port_error}")
            # portfolio_data bleibt mit Default-Werten

        # Pagination für Marktübersicht
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 25))
        total_pages = (len(market_overview) + per_page - 1) // per_page
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_overview = market_overview[start_idx:end_idx]

        # Filter basierend auf Query-Param
        filter_level = request.args.get('filter', 'all')
        if filter_level != 'all':
            hit_rates = {k: v for k, v in hit_rates.items() if k == filter_level}

        return render_template('dashboard.html',
                             signals=signals,
                             hit_rates=hit_rates,
                             watchlist=watchlist[:20],  # Erhöht auf 20
                             total_tickers=total_tickers,
                             avg_rate=avg_rate,
                             total_signals=total_signals,
                             historical_signals=historical_signals,
                             market_overview=paginated_overview,
                             performance_stats=performance_stats,
                             options_signals=options_signals_list,
                             options_stats=options_stats,
                             portfolio_data=portfolio_data,
                             filter_level=filter_level,
                             current_page=page,
                             total_pages=total_pages,
                             per_page=per_page)

    except Exception as e:
        logger.error(f"Kritischer Fehler im Dashboard: {e}", exc_info=True)
        return render_template('error.html',
                             error_message="Ein unerwarteter Fehler ist aufgetreten. Das Dashboard konnte nicht geladen werden.",
                             error_type=type(e).__name__,
                             error_details=str(e),
                             timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                             show_details=True)

@app.route('/chart/<symbol>')
def chart(symbol):
    """Zeige Chart für einen Ticker"""
    try:
        # Validiere Symbol-Parameter
        if not symbol or not symbol.strip():
            return render_template('error.html',
                                 error_message="Ungültiges Symbol angegeben.",
                                 error_type="ValueError",
                                 timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                 show_details=False)

        # Prüfe Datenbankverbindung
        try:
            db_status = db.health_check()
            if db_status.get('status') != 'healthy':
                logger.error("Datenbankverbindung fehlgeschlagen für Chart")
                return render_template('error.html',
                                     error_message="Datenbank ist nicht verfügbar. Chart kann nicht geladen werden.",
                                     error_type="DatabaseError",
                                     timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                     show_details=True)
        except Exception as db_error:
            logger.error(f"Datenbank-Health-Check für Chart fehlgeschlagen: {db_error}")
            return render_template('error.html',
                                 error_message="Datenbankverbindung konnte nicht überprüft werden.",
                                 error_type="DatabaseError",
                                 error_details=str(db_error),
                                 timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                 show_details=True)

        # Lade Daten und erstelle Chart
        df = db.load_historical_data(symbol)
        if df is None or df.empty:
            return render_template('error.html',
                                 error_message=f"Keine historischen Daten für Symbol '{symbol}' verfügbar.",
                                 error_type="DataError",
                                 timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                 show_details=False)

        df = calculate_indicators(df)
        chart_json = create_price_chart(df, symbol)

        # Parse JSON für separate Übergabe an Template
        chart_data = json.loads(chart_json)
        return render_template('chart.html', symbol=symbol, chart_data=chart_data)

    except Exception as e:
        logger.error(f"Kritischer Fehler beim Erstellen des Charts für {symbol}: {e}", exc_info=True)
        return render_template('error.html',
                             error_message=f"Chart für '{symbol}' konnte nicht erstellt werden.",
                             error_type=type(e).__name__,
                             error_details=str(e),
                             timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                             show_details=True)

@app.route('/fundamentals/<symbol>')
def fundamentals(symbol):
    """Detaillierte fundamentale Analyse für ein Symbol"""
    try:
        # Prüfe ob Symbol in Watchlist
        if symbol not in WATCHLIST_STOCKS:
            return render_template('error.html',
                                 error_message=f"Symbol {symbol} nicht in Watchlist gefunden.",
                                 error_type="SymbolNotFound",
                                 timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                 show_details=False), 404

        # Lade historische Daten für technische Analyse
        df = db.load_historical_data(symbol)
        if df is not None and not df.empty:
            df = calculate_indicators(df)
            latest = df.iloc[-1]
            current_price = latest['close']
        else:
            current_price = 0

        # Fundamentale Analyse
        fundamental_data = {}
        fundamental_scores = {'overall': 0, 'value': 0, 'growth': 0, 'quality': 0, 'momentum': 0, 'risk': 0}
        analysis_report = {}

        try:
            if symbol in scanner.fundamental_data_cache:
                fundamental_data = scanner.fundamental_data_cache[symbol]
                fundamental_scores = scanner._calculate_fundamental_score(symbol)
                analysis_report = scanner.get_fundamental_analysis_report(symbol)
            else:
                # Versuche Daten zu laden
                cached = db.get_fundamental_data(symbol)
                if cached:
                    scanner.fundamental_data_cache[symbol] = cached
                    fundamental_data = cached
                    fundamental_scores = scanner._calculate_fundamental_score(symbol)
                    analysis_report = scanner.get_fundamental_analysis_report(symbol)
        except Exception as e:
            logger.error(f"Fehler bei fundamentaler Analyse für {symbol}: {e}")

        return render_template('fundamentals.html',
                             symbol=symbol,
                             current_price=current_price,
                             fundamental_data=fundamental_data,
                             fundamental_scores=fundamental_scores,
                             analysis_report=analysis_report,
                             timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    except Exception as e:
        logger.error(f"Fehler bei Fundamentals-Page für {symbol}: {e}")
        return render_template('error.html',
                             error_message=f"Fehler beim Laden der fundamentalen Daten für {symbol}.",
                             error_type=type(e).__name__,
                             error_details=str(e),
                             timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                             show_details=True)

@app.errorhandler(404)
def page_not_found(e):
    """Handler für 404 Fehler"""
    return render_template('error.html',
                         error_message="Die angeforderte Seite wurde nicht gefunden.",
                         error_type="404",
                         timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                         show_details=False), 404

@app.errorhandler(500)
def internal_server_error(e):
    """Handler für 500 Fehler"""
    logger.error(f"Interner Serverfehler: {e}", exc_info=True)
    return render_template('error.html',
                         error_message="Ein interner Serverfehler ist aufgetreten.",
                         error_type="500",
                         error_details=str(e),
                         timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                         show_details=True), 500

@app.errorhandler(Exception)
def handle_unexpected_error(e):
    """Globaler Handler für unerwartete Fehler"""
    logger.error(f"Unerwarteter Fehler: {e}", exc_info=True)
    return render_template('error.html',
                         error_message="Ein unerwarteter Fehler ist aufgetreten.",
                         error_type=type(e).__name__,
                         error_details=str(e),
                         timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                         show_details=True), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)