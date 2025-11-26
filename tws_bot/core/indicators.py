"""
Technische Indikatoren für Trading Signale.
"""

import pandas as pd
from ..config.settings import (
    MA_SHORT_PERIOD, MA_LONG_PERIOD, RSI_PERIOD, USE_MACD,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL, USE_ATR, ATR_PERIOD,
    USE_BB, BB_PERIOD, BB_STD_DEV
)


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Berechnet technische Indikatoren.

    Args:
        df: DataFrame mit OHLCV Daten

    Returns:
        DataFrame mit Indikatoren
    """
    df = df.copy()

    # Moving Averages
    df['ma_short'] = df['close'].rolling(window=MA_SHORT_PERIOD).mean()
    df['ma_long'] = df['close'].rolling(window=MA_LONG_PERIOD).mean()

    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=RSI_PERIOD).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))

    # MACD
    if USE_MACD:
        exp1 = df['close'].ewm(span=MACD_FAST, adjust=False).mean()
        exp2 = df['close'].ewm(span=MACD_SLOW, adjust=False).mean()
        df['macd'] = exp1 - exp2
        df['macd_signal'] = df['macd'].ewm(span=MACD_SIGNAL, adjust=False).mean()

    # ATR (Average True Range)
    if USE_ATR:
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift(1)).abs()
        low_close = (df['low'] - df['close'].shift(1)).abs()
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr'] = true_range.rolling(window=ATR_PERIOD).mean()

    # Bollinger Bands
    if USE_BB:
        sma = df['close'].rolling(window=BB_PERIOD).mean()
        std = df['close'].rolling(window=BB_PERIOD).std()
        df['bb_upper'] = sma + (std * BB_STD_DEV)
        df['bb_lower'] = sma - (std * BB_STD_DEV)
        df['bb_middle'] = sma

    return df