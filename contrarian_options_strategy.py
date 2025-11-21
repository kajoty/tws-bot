"""
Konträre Optionsstrategie basierend auf 52-Wochen-Extrema und Fundamentaldaten.
Implementiert Long Put (Short am 52W-Hoch) und Long Call (Long am 52W-Tief) Strategien.
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, Optional, Tuple, List
from datetime import datetime, timedelta
import config

logger = logging.getLogger(__name__)


class ContrarianOptionsStrategy:
    """
    Konträre Optionsstrategie für Mean-Reversion Trades.
    
    - Long Put: Aktien nahe 52W-Hoch mit fundamentaler Überbewertung
    - Long Call: Aktien nahe 52W-Tief mit fundamentaler Unterbewertung
    """
    
    def __init__(self):
        self.min_market_cap = config.MIN_MARKET_CAP
        self.min_avg_volume = config.MIN_AVG_VOLUME
        self.trigger_distance = config.TRIGGER_DISTANCE_52W_PERCENT
        
        logger.info(
            f"ContrarianOptionsStrategy initialisiert: "
            f"MinMktCap=${self.min_market_cap/1e9:.1f}B, "
            f"MinVol={self.min_avg_volume:,}, "
            f"TriggerDist={self.trigger_distance*100:.1f}%"
        )
    
    def calculate_iv_rank(self, current_iv: float, iv_history: pd.Series) -> float:
        """
        Berechnet IV Rank als Perzentil der aktuellen IV in 52-Wochen Historie.
        
        IV Rank = (IV_current - IV_min_52W) / (IV_max_52W - IV_min_52W) * 100
        
        Args:
            current_iv: Aktuelle implizite Volatilität
            iv_history: Serie der historischen IV-Werte (52 Wochen)
            
        Returns:
            IV Rank zwischen 0 und 100
        """
        if iv_history.empty or len(iv_history) < 2:
            logger.warning("Nicht genug IV-Historie für IV Rank Berechnung")
            return 50.0  # Default Mittelwert
        
        iv_min = iv_history.min()
        iv_max = iv_history.max()
        
        if iv_max == iv_min:
            return 50.0
        
        iv_rank = ((current_iv - iv_min) / (iv_max - iv_min)) * 100
        return max(0.0, min(100.0, iv_rank))
    
    def calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> float:
        """
        Berechnet RSI (Relative Strength Index).
        
        Args:
            df: DataFrame mit 'close' Spalte
            period: RSI Periode (default 14)
            
        Returns:
            RSI Wert (0-100)
        """
        if len(df) < period + 1:
            return 50.0  # Neutral
        
        close = df['close'].values
        deltas = np.diff(close)
        
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def check_earnings_window(self, earnings_date: Optional[str]) -> Tuple[bool, str]:
        """
        Prüft ob Earnings innerhalb der nächsten 7 Tage sind.
        
        Args:
            earnings_date: Earnings Datum als String (YYYY-MM-DD) oder None
            
        Returns:
            (is_safe, reason)
        """
        if not earnings_date:
            return True, "Kein Earnings Datum verfügbar"
        
        try:
            earnings_dt = datetime.strptime(earnings_date, "%Y-%m-%d")
            today = datetime.now()
            days_until = (earnings_dt - today).days
            
            if 0 <= days_until <= 7:
                return False, f"Earnings in {days_until} Tagen (zu riskant)"
            elif -3 <= days_until < 0:
                return False, f"Earnings vor {-days_until} Tagen (IV Crush)"
            
            return True, f"Earnings in {days_until} Tagen (OK)"
            
        except Exception as e:
            logger.warning(f"Fehler beim Parsen des Earnings Datums: {e}")
            return True, "Earnings Check fehlgeschlagen (erlaubt)"
    
    def check_universe_filter(self, symbol: str, market_cap: float, 
                             avg_volume: float) -> Tuple[bool, str]:
        """
        Prüft ob Symbol die Universe-Filter erfüllt.
        
        Args:
            symbol: Ticker Symbol
            market_cap: Marktkapitalisierung in USD
            avg_volume: Durchschnittliches tägliches Handelsvolumen
            
        Returns:
            (passes_filter, reason)
        """
        if market_cap < self.min_market_cap:
            return False, f"Marktkapitalisierung ${market_cap/1e9:.1f}B < ${self.min_market_cap/1e9:.1f}B"
        
        if avg_volume < self.min_avg_volume:
            return False, f"Avg Volume {avg_volume:,.0f} < {self.min_avg_volume:,.0f}"
        
        return True, "Universe Filter bestanden"
    
    def check_long_put_criteria(self, symbol: str, df: pd.DataFrame, 
                                fundamental_data: Dict) -> Tuple[bool, float, Dict]:
        """
        Prüft Long Put Kriterien (Short am 52W-Hoch).
        
        Kriterien:
        1. Preis innerhalb 2% des 52W-Hochs
        2. RSI > 70 (überkauft)
        3. P/E > 150% des Branchen-Medians
        4. IV Rank 30-80 (nicht zu niedrig, nicht zu hoch)
        5. Keine Earnings in 7 Tagen
        
        Args:
            symbol: Ticker Symbol
            df: DataFrame mit Preisdaten (inkl. 52w_high)
            fundamental_data: Dict mit P/E, Branchen-P/E, IV-Daten
            
        Returns:
            (signal_triggered, confidence, details)
        """
        if df.empty or len(df) < 252:  # Mind. 1 Jahr Daten
            return False, 0.0, {"reason": "Nicht genug Preisdaten"}
        
        latest = df.iloc[-1]
        current_price = latest['close']
        high_52w = latest['52w_high']
        
        # 1. Prüfe Nähe zu 52W-Hoch
        distance_to_high = (current_price - high_52w) / high_52w
        price_trigger = current_price >= high_52w * (1 - self.trigger_distance)
        
        if not price_trigger:
            return False, 0.0, {
                "reason": f"Preis {distance_to_high*100:.1f}% vom 52W-Hoch entfernt (>2% benötigt)"
            }
        
        # 2. Prüfe RSI (überkauft)
        rsi = self.calculate_rsi(df)
        rsi_overbought = rsi > 70
        
        if not rsi_overbought:
            return False, 0.0, {
                "reason": f"RSI {rsi:.1f} < 70 (nicht überkauft)"
            }
        
        # 3. Prüfe P/E Überbewertung
        pe_ratio = fundamental_data.get('pe_ratio')
        sector_pe_median = fundamental_data.get('sector_pe_median')
        
        if pe_ratio is None or sector_pe_median is None or sector_pe_median <= 0:
            return False, 0.0, {"reason": "P/E Daten nicht verfügbar"}
        
        pe_threshold = sector_pe_median * config.LONG_PUT_PE_OVERVALUATION
        pe_overvalued = pe_ratio > pe_threshold
        
        if not pe_overvalued:
            return False, 0.0, {
                "reason": f"P/E {pe_ratio:.1f} < {pe_threshold:.1f} (150% von {sector_pe_median:.1f})"
            }
        
        # 4. Prüfe IV Rank (30-80 Range)
        current_iv = fundamental_data.get('current_iv')
        iv_history = fundamental_data.get('iv_history')
        
        if current_iv is None:
            # Ohne IV Daten erlauben wir Trade (konservativ)
            iv_rank = 50.0
            logger.warning(f"{symbol}: Keine IV Daten, verwende Default IV Rank 50")
        elif iv_history is None or len(iv_history) < 10:
            # Nicht genug Historie für IV Rank
            iv_rank = 50.0
            logger.warning(f"{symbol}: Keine IV Historie, verwende Default IV Rank 50")
        else:
            iv_rank = self.calculate_iv_rank(current_iv, iv_history)
        
        # IV Rank zwischen 30-80 (nicht zu niedrig, nicht zu hoch)
        iv_in_range = 30 <= iv_rank <= 80
        
        if not iv_in_range:
            return False, 0.0, {
                "reason": f"IV Rank {iv_rank:.1f} außerhalb 30-80 Range (zu extrem)"
            }
        
        # 5. Prüfe Earnings Window
        earnings_date = fundamental_data.get('next_earnings_date')
        earnings_safe, earnings_reason = self.check_earnings_window(earnings_date)
        
        if not earnings_safe:
            return False, 0.0, {"reason": earnings_reason}
        
        # Alle Kriterien erfüllt - berechne Confidence
        # Höhere Confidence bei stärkeren Signalen
        pe_overvaluation_factor = min(1.0, (pe_ratio / sector_pe_median - 1.0) / 0.5)  # Normalisiert
        rsi_strength = min(1.0, (rsi - 70) / 30)  # Stärker überkauft = höher
        iv_strength = (iv_rank - 30) / 50  # In der 30-80 Range
        price_extremity = 1 - abs(distance_to_high) / self.trigger_distance  # Näher = besser
        
        confidence = np.mean([
            pe_overvaluation_factor * 0.3,  # 30% Gewichtung
            rsi_strength * 0.3,  # 30% Gewichtung  
            iv_strength * 0.2,   # 20% Gewichtung
            price_extremity * 0.2  # 20% Gewichtung
        ])
        
        details = {
            "strategy": "LONG_PUT",
            "current_price": current_price,
            "52w_high": high_52w,
            "distance_to_high_pct": distance_to_high * 100,
            "pe_ratio": pe_ratio,
            "sector_pe_median": sector_pe_median,
            "pe_overvaluation_pct": (pe_ratio / sector_pe_median - 1) * 100,
            "iv_rank": iv_rank,
            "current_iv": current_iv,
            "confidence": confidence,
            "signals": [
                f"Nahe 52W-Hoch ({distance_to_high*100:.1f}%)",
                f"P/E {pe_ratio:.1f} vs Sektor {sector_pe_median:.1f} (überbewertet)",
                f"IV Rank {iv_rank:.1f} (hoch)"
            ]
        }
        
        logger.info(
            f"✓ LONG PUT Signal: {symbol} @ ${current_price:.2f} "
            f"(Confidence: {confidence:.2%}, IV Rank: {iv_rank:.1f}, P/E: {pe_ratio:.1f})"
        )
        
        return True, confidence, details
    
    def check_long_call_criteria(self, symbol: str, df: pd.DataFrame,
                                 fundamental_data: Dict) -> Tuple[bool, float, Dict]:
        """
        Prüft Long Call Kriterien (Long am 52W-Tief).
        
        Kriterien:
        1. Preis innerhalb 2% des 52W-Tiefs
        2. RSI < 30 (überverkauft)
        3. Positive FCF Rendite
        4. IV Rank 30-80 (nicht zu niedrig, nicht zu hoch)
        5. Keine Earnings in 7 Tagen
        
        Args:
            symbol: Ticker Symbol
            df: DataFrame mit Preisdaten (inkl. 52w_low)
            fundamental_data: Dict mit FCF, Marktkapitalisierung, IV-Daten
            
        Returns:
            (signal_triggered, confidence, details)
        """
        if df.empty or len(df) < 252:
            return False, 0.0, {"reason": "Nicht genug Preisdaten"}
        
        latest = df.iloc[-1]
        current_price = latest['close']
        low_52w = latest['52w_low']
        
        # 1. Prüfe Nähe zu 52W-Tief
        distance_to_low = (current_price - low_52w) / low_52w
        price_trigger = current_price <= low_52w * (1 + self.trigger_distance)
        
        if not price_trigger:
            return False, 0.0, {
                "reason": f"Preis {distance_to_low*100:.1f}% vom 52W-Tief entfernt (>2% benötigt)"
            }
        
        # 2. Prüfe RSI (überverkauft)
        rsi = self.calculate_rsi(df)
        rsi_oversold = rsi < 30
        
        if not rsi_oversold:
            return False, 0.0, {
                "reason": f"RSI {rsi:.1f} > 30 (nicht überverkauft)"
            }
        
        # 3. Prüfe FCF Rendite
        fcf = fundamental_data.get('free_cash_flow')
        market_cap = fundamental_data.get('market_cap')
        
        if fcf is None or market_cap is None or market_cap <= 0:
            return False, 0.0, {"reason": "FCF oder Marktkapitalisierung nicht verfügbar"}
        
        fcf_yield = fcf / market_cap
        fcf_positive = fcf_yield > 0
        
        if not fcf_positive:
            return False, 0.0, {
                "reason": f"FCF Rendite {fcf_yield*100:.2f}% nicht positiv"
            }
        
        # 4. Prüfe IV Rank (30-80 Range)
        current_iv = fundamental_data.get('current_iv')
        iv_history = fundamental_data.get('iv_history')
        
        if current_iv is None:
            iv_rank = 50.0
            logger.warning(f"{symbol}: Keine IV Daten, verwende Default IV Rank 50")
        elif iv_history is None or len(iv_history) < 10:
            iv_rank = 50.0
            logger.warning(f"{symbol}: Keine IV Historie, verwende Default IV Rank 50")
        else:
            iv_rank = self.calculate_iv_rank(current_iv, iv_history)
        
        # IV Rank zwischen 30-80
        iv_in_range = 30 <= iv_rank <= 80
        
        if not iv_in_range:
            return False, 0.0, {
                "reason": f"IV Rank {iv_rank:.1f} außerhalb 30-80 Range (zu extrem)"
            }
        
        # 5. Prüfe Earnings Window
        earnings_date = fundamental_data.get('next_earnings_date')
        earnings_safe, earnings_reason = self.check_earnings_window(earnings_date)
        
        if not earnings_safe:
            return False, 0.0, {"reason": earnings_reason}
        
        # Alle Kriterien erfüllt - berechne Confidence
        fcf_strength = min(1.0, fcf_yield * 10)  # FCF Yield normalisiert
        rsi_strength = min(1.0, (30 - rsi) / 30)  # Stärker überverkauft = höher
        iv_strength = (iv_rank - 30) / 50  # In der 30-80 Range
        price_extremity = 1 - abs(distance_to_low) / self.trigger_distance
        
        # Gewichtung: FCF 30%, RSI 30%, IV 20%, Price 20%
        confidence = (fcf_strength * 0.3) + (rsi_strength * 0.3) + (iv_strength * 0.2) + (price_extremity * 0.2)
        
        details = {
            "strategy": "LONG_CALL",
            "current_price": current_price,
            "52w_low": low_52w,
            "distance_to_low_pct": distance_to_low * 100,
            "rsi": rsi,
            "fcf": fcf,
            "market_cap": market_cap,
            "fcf_yield_pct": fcf_yield * 100,
            "iv_rank": iv_rank,
            "current_iv": current_iv,
            "earnings_date": earnings_date,
            "confidence": confidence,
            "signals": [
                f"Nahe 52W-Tief ({distance_to_low*100:.1f}%)",
                f"RSI {rsi:.1f} (überverkauft)",
                f"FCF Rendite {fcf_yield*100:.2f}% (positiv)",
                f"IV Rank {iv_rank:.1f} (30-80 Range)",
                f"Earnings sicher (nicht in ±7 Tagen)"
            ]
        }
        
        logger.info(
            f"✓ LONG CALL Signal: {symbol} @ ${current_price:.2f} "
            f"(Confidence: {confidence:.2%}, IV Rank: {iv_rank:.1f}, FCF Yield: {fcf_yield*100:.2f}%)"
        )
        
        return True, confidence, details
    
    def calculate_stop_loss(self, strategy_type: str, reference_price: float) -> float:
        """
        Berechnet Stop-Loss Preis basierend auf Strategie.
        
        Args:
            strategy_type: "LONG_PUT" oder "LONG_CALL"
            reference_price: 52W-Hoch (PUT) oder 52W-Tief (CALL)
            
        Returns:
            Stop-Loss Preis
        """
        if strategy_type == "LONG_PUT":
            # Stop wenn Aktie 1.5% über 52W-Hoch schließt
            return reference_price * (1 + config.LONG_PUT_STOP_LOSS_PCT)
        elif strategy_type == "LONG_CALL":
            # Stop wenn Aktie 1.5% unter 52W-Tief schließt
            return reference_price * (1 - config.LONG_CALL_STOP_LOSS_PCT)
        else:
            raise ValueError(f"Unbekannter Strategie-Typ: {strategy_type}")
    
    def should_close_position(self, position: Dict, current_stock_price: float,
                             current_option_value: float) -> Tuple[bool, str]:
        """
        Prüft ob Position geschlossen werden soll.
        
        Gründe:
        1. Stop-Loss getroffen (Aktienpreis)
        2. Take-Profit erreicht (Optionswert)
        3. Zeitwertverlust-Schwelle (DTE)
        
        Args:
            position: Dict mit Position-Details (strategy, entry_premium, stop_loss, etc.)
            current_stock_price: Aktueller Aktienkurs
            current_option_value: Aktueller Optionswert
            
        Returns:
            (should_close, reason)
        """
        strategy = position.get('strategy')
        stop_loss = position.get('stop_loss_price')
        entry_premium = position.get('entry_premium')
        dte = position.get('days_to_expiration')
        
        # 1. Stop-Loss Check
        if strategy == "LONG_PUT" and current_stock_price > stop_loss:
            return True, f"Stop-Loss: Aktie über ${stop_loss:.2f} (aktuell ${current_stock_price:.2f})"
        
        if strategy == "LONG_CALL" and current_stock_price < stop_loss:
            return True, f"Stop-Loss: Aktie unter ${stop_loss:.2f} (aktuell ${current_stock_price:.2f})"
        
        # 2. Take-Profit Check
        if strategy == "LONG_PUT":
            take_profit_threshold = entry_premium * (1 + config.LONG_PUT_TAKE_PROFIT_PCT)
            if current_option_value >= take_profit_threshold:
                pnl_pct = (current_option_value / entry_premium - 1) * 100
                return True, f"Take-Profit: {pnl_pct:.1f}% Gewinn erreicht"
        
        if strategy == "LONG_CALL":
            take_profit_threshold = entry_premium * (1 + config.LONG_CALL_TAKE_PROFIT_PCT)
            if current_option_value >= take_profit_threshold:
                pnl_pct = (current_option_value / entry_premium - 1) * 100
                return True, f"Take-Profit: {pnl_pct:.1f}% Gewinn erreicht"
        
        # 3. DTE Auto-Close (ALLE Positionen bei DTE < 7 - Theta-Schutz)
        if dte is not None and dte < 7:
            pnl_pct = (current_option_value / entry_premium - 1) * 100 if entry_premium > 0 else 0
            return True, f"Auto-Close: DTE={dte} (Theta-Schutz, P&L: {pnl_pct:+.1f}%)"
        
        return False, "Positionhalten"
    
    def check_strategy(self, symbol: str, df: pd.DataFrame,
                      fundamental_data: Dict) -> Tuple[str, float, Optional[Dict]]:
        """
        Hauptfunktion - prüft beide Strategien und gibt stärkstes Signal zurück.
        
        Args:
            symbol: Ticker Symbol
            df: Price DataFrame
            fundamental_data: Fundamentaldaten Dict
            
        Returns:
            (signal, confidence, details) - signal ist "LONG_PUT", "LONG_CALL" oder "HOLD"
        """
        # Prüfe Universe Filter
        market_cap = fundamental_data.get('market_cap', 0)
        avg_volume = fundamental_data.get('avg_volume', 0)
        
        passes_filter, filter_reason = self.check_universe_filter(symbol, market_cap, avg_volume)
        if not passes_filter:
            logger.debug(f"{symbol}: {filter_reason}")
            return "HOLD", 0.0, None
        
        # Prüfe Long Put
        put_triggered, put_confidence, put_details = self.check_long_put_criteria(
            symbol, df, fundamental_data
        )
        
        # Prüfe Long Call
        call_triggered, call_confidence, call_details = self.check_long_call_criteria(
            symbol, df, fundamental_data
        )
        
        # Wähle stärkstes Signal
        if put_triggered and call_triggered:
            # Beide getriggert - wähle mit höherer Confidence
            if put_confidence >= call_confidence:
                return "LONG_PUT", put_confidence, put_details
            else:
                return "LONG_CALL", call_confidence, call_details
        
        if put_triggered:
            return "LONG_PUT", put_confidence, put_details
        
        if call_triggered:
            return "LONG_CALL", call_confidence, call_details
        
        return "HOLD", 0.0, None
