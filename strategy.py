"""Strategie-Modul für den IB Trading Bot."""
import pandas as pd
import numpy as np
import logging
from typing import Dict, Optional, Tuple
import config

logger = logging.getLogger(__name__)

class TradingStrategy:
    def __init__(self, ma_short: int = config.MA_SHORT_PERIOD, ma_long: int = config.MA_LONG_PERIOD, rsi_period: int = config.RSI_PERIOD, rsi_oversold: int = config.RSI_OVERSOLD, rsi_overbought: int = config.RSI_OVERBOUGHT):
        self.ma_short, self.ma_long, self.rsi_period = ma_short, ma_long, rsi_period
        self.rsi_oversold, self.rsi_overbought = rsi_oversold, rsi_overbought
        logger.info(f"Strategie initialisiert: MA({ma_short}/{ma_long}), RSI({rsi_period}, {rsi_oversold}/{rsi_overbought})")

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or len(df) < max(self.ma_long, self.rsi_period):
            return df
        df = df.copy()
        try:
            df['ma_short'] = df['close'].rolling(window=self.ma_short).mean()
            df['ma_long'] = df['close'].rolling(window=self.ma_long).mean()
            df['rsi'] = self._calculate_rsi(df['close'], self.rsi_period)
            df['atr'] = self._calculate_atr(df, period=14)
            df['bb_middle'] = df['close'].rolling(window=20).mean()
            bb_std = df['close'].rolling(window=20).std()
            df['bb_upper'], df['bb_lower'] = df['bb_middle'] + (bb_std * 2), df['bb_middle'] - (bb_std * 2)
            df['macd'], df['macd_signal'], df['macd_hist'] = self._calculate_macd(df['close'])
            df['volume_ma'] = df['volume'].rolling(window=20).mean()
            # 52W High/Low: use all available data instead of rolling(252) to avoid NaN with less data
            df['52w_high'] = df['close'].expanding().max()
            df['52w_low'] = df['close'].expanding().min()
        except Exception as e:
            logger.error(f"Fehler bei Indikatorberechnung: {e}")
        return df

    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return true_range.rolling(window=period).mean()

    def _calculate_macd(self, prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        ema_fast, ema_slow = prices.ewm(span=fast, adjust=False).mean(), prices.ewm(span=slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        signal_line = macd.ewm(span=signal, adjust=False).mean()
        return macd, signal_line, macd - signal_line

    def check_strategy(self, symbol: str, df: pd.DataFrame, market_data: Optional[Dict] = None) -> Tuple[str, float, Optional[Dict]]:
        if df.empty or len(df) < self.ma_long:
            return 'HOLD', 0.0, None
        try:
            if 'rsi' not in df.columns:
                df = self.calculate_indicators(df)
            latest, prev = df.iloc[-1], df.iloc[-2] if len(df) > 1 else df.iloc[-1]
            buy_score, sell_score, signals = 0.0, 0.0, []
            
            if latest['ma_short'] > latest['ma_long'] and prev['ma_short'] <= prev['ma_long']:
                buy_score += 0.3; signals.append("MA Bullish Crossover")
            elif latest['ma_short'] < latest['ma_long'] and prev['ma_short'] >= prev['ma_long']:
                sell_score += 0.3; signals.append("MA Bearish Crossover")
            
            if latest['ma_short'] > latest['ma_long']: buy_score += 0.1
            else: sell_score += 0.1
            
            if latest['rsi'] < self.rsi_oversold: buy_score += 0.2; signals.append(f"RSI Oversold ({latest['rsi']:.1f})")
            elif latest['rsi'] > self.rsi_overbought: sell_score += 0.2; signals.append(f"RSI Overbought ({latest['rsi']:.1f})")
            
            if latest['macd'] > latest['macd_signal'] and prev['macd'] <= prev['macd_signal']:
                buy_score += 0.15; signals.append("MACD Bullish Crossover")
            elif latest['macd'] < latest['macd_signal'] and prev['macd'] >= prev['macd_signal']:
                sell_score += 0.15; signals.append("MACD Bearish Crossover")
            
            if latest['close'] < latest['bb_lower']: buy_score += 0.15; signals.append("Preis unter Bollinger Band")
            elif latest['close'] > latest['bb_upper']: sell_score += 0.15; signals.append("Preis über Bollinger Band")
            
            if latest['volume'] > latest['volume_ma'] * 1.5:
                if buy_score > sell_score: buy_score += 0.1; signals.append("Hohes Volumen (Bestätigung)")
                elif sell_score > buy_score: sell_score += 0.1; signals.append("Hohes Volumen (Bestätigung)")
            
            price_range = latest['52w_high'] - latest['52w_low']
            if price_range > 0:
                position_in_range = (latest['close'] - latest['52w_low']) / price_range
                if position_in_range < 0.2: buy_score += 0.1; signals.append("Nahe 52-Wochen-Tief")
                elif position_in_range > 0.8: sell_score += 0.05; signals.append("Nahe 52-Wochen-Hoch")
            
            if buy_score > sell_score and buy_score > 0.5: signal, confidence = 'BUY', min(buy_score, 1.0)
            elif sell_score > buy_score and sell_score > 0.5: signal, confidence = 'SELL', min(sell_score, 1.0)
            else: signal, confidence = 'HOLD', 0.0
            
            stop_loss_price = None
            if signal == 'BUY': stop_loss_price = latest['close'] - (2 * latest['atr'])
            elif signal == 'SELL': stop_loss_price = latest['close'] + (2 * latest['atr'])
            
            details = {'symbol': symbol, 'signal': signal, 'confidence': confidence, 'current_price': latest['close'], 'buy_score': buy_score, 'sell_score': sell_score, 'signals': signals, 'rsi': latest['rsi'], 'macd': latest['macd'], 'ma_short': latest['ma_short'], 'ma_long': latest['ma_long'], 'atr': latest['atr'], 'stop_loss_price': stop_loss_price, 'market_data': market_data}
            
            if signal != 'HOLD':
                logger.info(f"Signal für {symbol}: {signal} (Confidence: {confidence:.2f}) - Gründe: {', '.join(signals)}")
            
            return signal, confidence, details
        except Exception as e:
            logger.error(f"Fehler bei Strategieprüfung für {symbol}: {e}")
            return 'HOLD', 0.0, None
