"""
Signal-Generierungslogik für Trading Signale.
"""

from typing import Optional, Dict
import pandas as pd
from datetime import datetime
from .indicators import calculate_indicators
from ..config.settings import (
    MA_LONG_PERIOD, USE_MA_CROSSOVER, USE_RSI, USE_MACD,
    RSI_OVERSOLD, MIN_SIGNALS_FOR_ENTRY, STOP_LOSS_PCT,
    TAKE_PROFIT_PCT, ACCOUNT_SIZE, MAX_RISK_PER_TRADE_PCT,
    MIN_POSITION_SIZE, USE_VIX_FILTER, VIX_MAX_LEVEL, VIX_HIGH_LEVEL,
    USE_ATR, ATR_MULTIPLIER, USE_BB, MIN_CUSHION_FOR_SIGNALS, MAX_POSITIONS
)


def calculate_options_trade_cushion_impact(portfolio_data: dict, max_risk: float, strategy_type: str) -> dict:
    """
    Berechne den Impact eines Options-Trades auf den Cushion-Wert.

    Args:
        portfolio_data: Aktuelle Portfolio-Daten von TWS
        max_risk: Maximales Risiko des Options-Trades
        strategy_type: Art der Options-Strategie

    Returns:
        Dictionary mit Impact-Analyse
    """
    current_cushion = portfolio_data.get('cushion', 0)
    current_net_liq = portfolio_data.get('net_liquidation', 0)
    current_buying_power = portfolio_data.get('buying_power', 0)

    # Margin-Anforderungen basierend auf Strategie-Typ
    if strategy_type in ['long_put', 'long_call']:
        # Long Options: Max Risk = Premium bezahlt
        margin_required = max_risk
    elif strategy_type in ['short_put']:
        # Cash-Secured Put: Margin = Strike - Premium
        margin_required = max_risk  # Bereits im max_risk enthalten
    elif strategy_type in ['bull_put_spread']:
        # Spread: Max Risk = Strike Difference - Net Premium
        margin_required = max_risk
    else:
        margin_required = max_risk * 0.5  # Konservative Schätzung

    # Neue Werte nach Trade berechnen
    new_net_liq = current_net_liq - max_risk  # Bei Long Options: Premium bezahlen
    new_buying_power = current_buying_power - margin_required

    # Maintenance Margin aktualisieren
    current_maintenance_margin = current_net_liq * (1 - current_cushion) if current_cushion > 0 else 0
    new_maintenance_margin = current_maintenance_margin + margin_required

    # Neuer Cushion berechnen
    if new_net_liq > 0:
        new_cushion = (new_net_liq - new_maintenance_margin) / new_net_liq
    else:
        new_cushion = 0

    cushion_change = new_cushion - current_cushion
    cushion_change_pct = (cushion_change / current_cushion) * 100 if current_cushion > 0 else 0

    # Risiko-Level für Options-Trades (strengere Kriterien)
    risk_level = "LOW"
    if new_cushion < 0.10:  # Unter 10% für Options
        risk_level = "CRITICAL"
    elif new_cushion < 0.20:  # Unter 20%
        risk_level = "HIGH"
    elif new_cushion < 0.30:  # Unter 30%
        risk_level = "MEDIUM"

    return {
        'current_cushion': current_cushion,
        'new_cushion': new_cushion,
        'cushion_change': cushion_change,
        'cushion_change_pct': cushion_change_pct,
        'new_buying_power': new_buying_power,
        'margin_required': margin_required,
        'max_risk': max_risk,
        'strategy_type': strategy_type,
        'risk_level': risk_level,
        'acceptable': new_cushion >= 0.10  # Mindestens 10% Cushion für Options
    }


def check_entry_signal(symbol: str, df: pd.DataFrame, tws_connector=None, portfolio_data=None) -> Optional[Dict]:
    """
    Prüft Entry-Signal Bedingungen.

    Args:
        symbol: Ticker Symbol
        df: DataFrame mit OHLCV Daten

    Returns:
        Signal-Dict oder None
    """
    # Portfolio-basierte Signal-Filterung
    if portfolio_data:
        cushion = portfolio_data.get('cushion', 0)
        num_positions = portfolio_data.get('num_positions', 0)
        
        # Signal ablehnen bei zu niedrigem Cushion
        if cushion < MIN_CUSHION_FOR_SIGNALS:
            print(f"Signal für {symbol} abgelehnt - Cushion zu niedrig ({cushion:.1%} < {MIN_CUSHION_FOR_SIGNALS:.1%})")
            return None
            
        # Signal ablehnen bei zu vielen Positionen
        if num_positions >= MAX_POSITIONS:
            print(f"Signal für {symbol} abgelehnt - Zu viele Positionen ({num_positions} >= {MAX_POSITIONS})")
            return None

    # Indikatoren berechnen
    df = calculate_indicators(df)

    if len(df) < MA_LONG_PERIOD + 1:
        return None

    # VIX Filter prüfen
    if USE_VIX_FILTER and tws_connector:
        vix_level = get_vix_level(tws_connector)
        if vix_level is None:
            print(f"VIX Daten nicht verfügbar für {symbol}")
            return None
        if vix_level > VIX_MAX_LEVEL:
            print(f"VIX zu hoch ({vix_level:.1f} > {VIX_MAX_LEVEL}) - kein Entry für {symbol}")
            return None

    current = df.iloc[-1]
    previous = df.iloc[-2]

    signals = []
    reasons = []

    # MA Crossover
    if USE_MA_CROSSOVER:
        if (previous['ma_short'] <= previous['ma_long'] and
            current['ma_short'] > current['ma_long']):
            signals.append(True)
            reasons.append("MA Crossover")
        else:
            signals.append(False)

    # RSI Oversold
    if USE_RSI:
        if current['rsi'] < RSI_OVERSOLD:
            signals.append(True)
            reasons.append(f"RSI {current['rsi']:.1f} < {RSI_OVERSOLD}")
        else:
            signals.append(False)

    # MACD Crossover
    if USE_MACD:
        if (previous['macd'] <= previous['macd_signal'] and
            current['macd'] > current['macd_signal']):
            signals.append(True)
            reasons.append("MACD Crossover")
        else:
            signals.append(False)

    # Bollinger Band Squeeze (Preis berührt unteres Band)
    if USE_BB:
        if ('bb_lower' in current and not pd.isna(current['bb_lower']) and
            current['close'] <= current['bb_lower'] * 1.01):  # 1% Toleranz
            signals.append(True)
            reasons.append("BB Lower Touch")
        else:
            signals.append(False)

    # Mindestanzahl Signale
    signal_count = sum(signals)

    if signal_count >= MIN_SIGNALS_FOR_ENTRY:
        price = current['close']

        # Stop-Loss berechnen (ATR-basiert wenn verfügbar)
        if USE_ATR and 'atr' in current and not pd.isna(current['atr']):
            # ATR-basierter Stop-Loss (volatilitätsadjustiert)
            stop_distance = current['atr'] * ATR_MULTIPLIER
            stop_loss = price - stop_distance
            take_profit = price + (stop_distance * 2)  # 2:1 Reward/Risk Ratio
        else:
            # Fallback auf prozentualen Stop-Loss
            stop_loss = price * (1 - STOP_LOSS_PCT)
            take_profit = price * (1 + TAKE_PROFIT_PCT)

        # Position Size berechnen (VIX-adjustiert + Portfolio-basiert)
        risk_amount = ACCOUNT_SIZE * MAX_RISK_PER_TRADE_PCT

        # Portfolio-basierte Risiko-Anpassung
        if portfolio_data:
            cushion = portfolio_data.get('cushion', 0)
            buying_power = portfolio_data.get('buying_power', ACCOUNT_SIZE)
            num_positions = portfolio_data.get('num_positions', 0)
            
            # Cushion-basierte Risiko-Anpassung
            if cushion < 0.1:  # Sehr niedriger Cushion (< 10%)
                risk_amount *= 0.3  # Stark reduzieren
                print(f"Sehr niedriger Cushion ({cushion:.1%}) - Risiko stark reduziert auf {risk_amount:.0f}")
            elif cushion < 0.3:  # Niedriger Cushion (< 30%)
                risk_amount *= 0.6  # Moderat reduzieren
                print(f"Niedriger Cushion ({cushion:.1%}) - Risiko reduziert auf {risk_amount:.0f}")
            
            # Positions-basierte Anpassung (mehr Positionen = weniger Risiko pro Trade)
            if num_positions > 10:
                risk_amount *= 0.8
                print(f"Viele Positionen ({num_positions}) - Risiko pro Trade reduziert")
            
            # Buying Power Limit
            max_risk_from_buying_power = buying_power * 0.02  # Max 2% der Buying Power
            risk_amount = min(risk_amount, max_risk_from_buying_power)
            
        # Bei hohem VIX Risiko reduzieren
        if USE_VIX_FILTER and tws_connector:
            vix_level = get_vix_level(tws_connector)
            if vix_level and vix_level > VIX_HIGH_LEVEL:
                risk_amount *= 0.5  # 50% Risiko-Reduzierung bei hohem VIX
                print(f"Hoher VIX ({vix_level:.1f}) - Risiko reduziert auf {risk_amount:.0f}")

        stop_distance = price - stop_loss
        quantity = int(risk_amount / stop_distance)

        if quantity * price < MIN_POSITION_SIZE:
            return None

        # PRE-TRADE CUSHION ANALYSIS
        trade_value = quantity * price
        margin_required = trade_value * 0.25  # Vereinfachte Margin-Anforderung (25% für Long)

        cushion_impact = calculate_trade_cushion_impact(portfolio_data, trade_value, margin_required)

        # Trade ablehnen wenn Cushion unter kritische Grenze fällt
        if not cushion_impact['acceptable']:
            print(f"Trade abgelehnt - Cushion würde auf {cushion_impact['new_cushion']:.1%} fallen (kritisch)")
            return None

        # Warnung bei hohem Risiko
        if cushion_impact['risk_level'] in ['HIGH', 'CRITICAL']:
            print(f"Warnung: Trade würde Cushion auf {cushion_impact['new_cushion']:.1%} reduzieren ({cushion_impact['risk_level']} Risiko)")

        return {
            'type': 'ENTRY',
            'symbol': symbol,
            'price': price,
            'quantity': quantity,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'reason': " + ".join(reasons),
            'cushion_impact': cushion_impact,  # Impact-Analyse hinzufügen
            'timestamp': datetime.now()
        }

    return None


def check_exit_signal(symbol: str, df: pd.DataFrame, position: Dict) -> Optional[Dict]:
    """
    Prüft Exit-Signal Bedingungen.

    Args:
        symbol: Ticker Symbol
        df: DataFrame mit OHLCV Daten
        position: Aktive Position

    Returns:
        Signal-Dict oder None
    """
    # Indikatoren berechnen
    df = calculate_indicators(df)

    if len(df) < 2:
        return None

    current = df.iloc[-1]
    entry_price = position['entry_price']

    # Stop Loss
    if current['close'] <= position['stop_loss']:
        pnl_pct = (current['close'] - entry_price) / entry_price * 100
        pnl_usd = (current['close'] - entry_price) * position['quantity']
        return {
            'type': 'EXIT',
            'symbol': symbol,
            'price': current['close'],
            'quantity': position['quantity'],
            'pnl_pct': pnl_pct,
            'pnl_usd': pnl_usd,
            'reason': f"Stop Loss erreicht ({current['close']:.2f} <= {position['stop_loss']:.2f})",
            'timestamp': datetime.now()
        }

    # Take Profit
    if current['close'] >= position['take_profit']:
        pnl_pct = (current['close'] - entry_price) / entry_price * 100
        pnl_usd = (current['close'] - entry_price) * position['quantity']
        return {
            'type': 'EXIT',
            'symbol': symbol,
            'price': current['close'],
            'quantity': position['quantity'],
            'pnl_pct': pnl_pct,
            'pnl_usd': pnl_usd,
            'reason': f"Take Profit erreicht ({current['close']:.2f} >= {position['take_profit']:.2f})",
            'timestamp': datetime.now()
        }

    # RSI Overbought
    if current['rsi'] > 70:  # RSI_OVERBOUGHT
        pnl_pct = (current['close'] - entry_price) / entry_price * 100
        pnl_usd = (current['close'] - entry_price) * position['quantity']
        return {
            'type': 'EXIT',
            'symbol': symbol,
            'price': current['close'],
            'quantity': position['quantity'],
            'pnl_pct': pnl_pct,
            'pnl_usd': pnl_usd,
            'reason': f"RSI Overbought ({current['rsi']:.1f} > 70)",
            'timestamp': datetime.now()
        }

    return None