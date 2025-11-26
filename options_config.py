"""
Options-Trading Konfiguration für konträre 52-Wochen-Extrem-Strategie.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# RISIKOMANAGEMENT - CUSHION EINSTELLUNGEN
# ============================================================================

# Mindest-Cushion für Options-Signale (Sicherheitsreserve)
MIN_CUSHION_PCT = float(os.getenv("MIN_CUSHION_PCT", "1.0"))  # 1% statt 5% - angepasst für aktuelle Marktbedingungen

# ============================================================================
# HANDELSUNIVERSUM FILTER
# ============================================================================

# Marktkapitalisierung Minimum (in USD)
MIN_MARKET_CAP = float(os.getenv("MIN_MARKET_CAP", "5000000000"))  # 5 Milliarden

# Durchschnittliches tägliches Volumen (Aktien)
MIN_AVG_VOLUME = int(os.getenv("MIN_AVG_VOLUME", "500000"))

# Positionsgröße (% des Kapitals pro Trade)
MAX_POSITION_SIZE_PCT = float(os.getenv("MAX_POSITION_SIZE_PCT", "0.01"))  # 1%

# ============================================================================
# LONG PUT STRATEGIE (SHORT AM 52W-HOCH)
# ============================================================================

# Technischer Trigger: Nähe zum 52-Wochen-Hoch
PUT_PROXIMITY_TO_HIGH_PCT = float(os.getenv("PUT_PROXIMITY_TO_HIGH_PCT", "0.02"))  # 2%

# Fundamentale Überbewertung: P/E über Branchen-Median
PUT_PE_RATIO_MULTIPLIER = float(os.getenv("PUT_PE_RATIO_MULTIPLIER", "1.5"))  # 150%

# Implizite Volatilität: IV Rank Minimum
PUT_MIN_IV_RANK = float(os.getenv("PUT_MIN_IV_RANK", "70"))  # Oberes Drittel

# Options-Parameter
PUT_MIN_DTE = int(os.getenv("PUT_MIN_DTE", "60"))
PUT_MAX_DTE = int(os.getenv("PUT_MAX_DTE", "90"))
PUT_STRIKE_TYPE = "ATM"  # At-the-Money

# Risikomanagement
PUT_STOP_LOSS_PCT = float(os.getenv("PUT_STOP_LOSS_PCT", "0.015"))  # 1.5% über 52W-Hoch
PUT_TAKE_PROFIT_PCT = float(os.getenv("PUT_TAKE_PROFIT_PCT", "0.50"))  # 50% der Prämie
PUT_AUTO_CLOSE_DTE = int(os.getenv("PUT_AUTO_CLOSE_DTE", "10"))  # 10 Tage vor Verfall

# ============================================================================
# LONG CALL STRATEGIE (LONG AM 52W-TIEF)
# ============================================================================

# Technischer Trigger: Nähe zum 52-Wochen-Tief
CALL_PROXIMITY_TO_LOW_PCT = float(os.getenv("CALL_PROXIMITY_TO_LOW_PCT", "0.02"))  # 2%

# Fundamentale Unterbewertung: FCF > 0
CALL_MIN_FCF_YIELD = float(os.getenv("CALL_MIN_FCF_YIELD", "0.0"))  # Positive FCF

# Implizite Volatilität: IV Rank Maximum
CALL_MAX_IV_RANK = float(os.getenv("CALL_MAX_IV_RANK", "30"))  # Unteres Drittel

# Options-Parameter
CALL_MIN_DTE = int(os.getenv("CALL_MIN_DTE", "90"))
CALL_MAX_DTE = int(os.getenv("CALL_MAX_DTE", "120"))
CALL_TARGET_DELTA = float(os.getenv("CALL_TARGET_DELTA", "0.40"))  # OTM mit Delta ~0.40

# Risikomanagement
CALL_STOP_LOSS_PCT = float(os.getenv("CALL_STOP_LOSS_PCT", "0.015"))  # 1.5% unter 52W-Tief
CALL_TAKE_PROFIT_PCT = float(os.getenv("CALL_TAKE_PROFIT_PCT", "0.75"))  # 75% der Prämie
CALL_AUTO_CLOSE_DTE = int(os.getenv("CALL_AUTO_CLOSE_DTE", "20"))  # 20 Tage vor Verfall

# ============================================================================
# BEAR CALL SPREAD STRATEGIE (SHORT AM 52W-HOCH)
# ============================================================================

# Technischer Trigger: Nähe zum 52-Wochen-Hoch (wie Long Put)
SPREAD_PROXIMITY_TO_HIGH_PCT = float(os.getenv("SPREAD_PROXIMITY_TO_HIGH_PCT", "0.02"))  # 2%

# Fundamentale Überbewertung: P/E über Branchen-Median (wie Long Put)
SPREAD_PE_RATIO_MULTIPLIER = float(os.getenv("SPREAD_PE_RATIO_MULTIPLIER", "1.5"))  # 150%

# BULL PUT SPREAD STRATEGIE (SHORT AM 52W-TIEF)
# ============================================================================

# Technischer Trigger: Nähe zum 52-Wochen-Tief (wie Long Call)
SPREAD_PROXIMITY_TO_LOW_PCT = float(os.getenv("SPREAD_PROXIMITY_TO_LOW_PCT", "0.02"))  # 2%

# Fundamentale Unterbewertung: P/E unter Branchen-Median
SPREAD_PE_RATIO_MULTIPLIER_LOW = float(os.getenv("SPREAD_PE_RATIO_MULTIPLIER_LOW", "0.8"))  # 80%

# FCF Yield Minimum (für Bull Put Spread)
SPREAD_MIN_FCF_YIELD = float(os.getenv("SPREAD_MIN_FCF_YIELD", "0.03"))  # 3%

# Implizite Volatilität: IV Rank Minimum

# Options-Parameter
SPREAD_MIN_DTE = int(os.getenv("SPREAD_MIN_DTE", "30"))
SPREAD_MAX_DTE = int(os.getenv("SPREAD_MAX_DTE", "45"))

# Strike-Auswahl
SPREAD_SHORT_DELTA_MIN = float(os.getenv("SPREAD_SHORT_DELTA_MIN", "0.25"))  # Verkaufter Call
SPREAD_SHORT_DELTA_MAX = float(os.getenv("SPREAD_SHORT_DELTA_MAX", "0.35"))  # Verkaufter Call
SPREAD_STRIKE_WIDTH = float(os.getenv("SPREAD_STRIKE_WIDTH", "5.0"))  # $5 zwischen Short/Long

# Risikomanagement
SPREAD_STOP_LOSS_STRIKE_BREACH = True  # Close wenn Aktienkurs den Long Strike erreicht
SPREAD_TAKE_PROFIT_MIN_PCT = float(os.getenv("SPREAD_TAKE_PROFIT_MIN_PCT", "0.50"))  # 50%
SPREAD_TAKE_PROFIT_MAX_PCT = float(os.getenv("SPREAD_TAKE_PROFIT_MAX_PCT", "0.75"))  # 75%
SPREAD_AUTO_CLOSE_DTE = int(os.getenv("SPREAD_AUTO_CLOSE_DTE", "7"))  # 7 Tage vor Verfall

# ============================================================================
# COVERED CALL STRATEGIE (VERKAUF VON CALLS AUF EIGENE AKTIEN)
# ============================================================================

# Technischer Trigger: Nähe zum 52-Wochen-Hoch
COVERED_CALL_PROXIMITY_TO_HIGH_PCT = float(os.getenv("COVERED_CALL_PROXIMITY_TO_HIGH_PCT", "0.02"))  # 2%

# Fundamentale Bewertung: Nicht extrem überbewertet
COVERED_CALL_PE_RATIO_MULTIPLIER = float(os.getenv("COVERED_CALL_PE_RATIO_MULTIPLIER", "1.3"))  # 130%

# Options-Parameter
COVERED_CALL_MIN_DTE = int(os.getenv("COVERED_CALL_MIN_DTE", "30"))
COVERED_CALL_MAX_DTE = int(os.getenv("COVERED_CALL_MAX_DTE", "60"))

# Strike-Auswahl
COVERED_CALL_MIN_OTM_PCT = float(os.getenv("COVERED_CALL_MIN_OTM_PCT", "0.05"))  # 5% OTM
COVERED_CALL_MAX_OTM_PCT = float(os.getenv("COVERED_CALL_MAX_OTM_PCT", "0.15"))  # 15% OTM

# IV Rank Minimum
COVERED_CALL_MIN_IV_RANK = float(os.getenv("COVERED_CALL_MIN_IV_RANK", "60"))  # Mittleres Niveau

# Risikomanagement
COVERED_CALL_MAX_LOSS_PCT = float(os.getenv("COVERED_CALL_MAX_LOSS_PCT", "0.10"))  # Max 10% Verlust auf Position

# ============================================================================
# SCANNER EINSTELLUNGEN
# ============================================================================

# Scan-Intervall für Options (länger als Aktien-Scanner)
OPTIONS_SCAN_INTERVAL = int(os.getenv("OPTIONS_SCAN_INTERVAL", "3600"))  # 1 Stunde

# Historische Daten für 52-Wochen-Berechnung
WEEKS_52_DAYS = 252  # Handelstage in 52 Wochen

# IV Rank Berechnung (52 Wochen)
IV_RANK_PERIOD_DAYS = 252

# Handelszeiten (EST) - Optional
ENFORCE_TRADING_HOURS = os.getenv("ENFORCE_TRADING_HOURS", "False").lower() in ("true", "1", "yes")
TRADING_START_HOUR = int(os.getenv("TRADING_START_HOUR", "9"))
TRADING_START_MINUTE = int(os.getenv("TRADING_START_MINUTE", "30"))
TRADING_END_HOUR = int(os.getenv("TRADING_END_HOUR", "16"))
TRADING_END_MINUTE = int(os.getenv("TRADING_END_MINUTE", "0"))

# ============================================================================
# TREND-FILTER (VERMEIDET STARKE TRENDS)
# ============================================================================

# Trend-Filter aktivieren/deaktivieren
USE_TREND_FILTER = os.getenv("USE_TREND_FILTER", "True").lower() in ("true", "1", "yes")

# Maximale Trend-Stärke (ADX normalisiert auf 0-1, 0.25 = 25 ADX)
TREND_STRENGTH_MAX = float(os.getenv("TREND_STRENGTH_MAX", "0.7"))  # 70% = starker Trend

# ADX Periode für Trend-Messung
ADX_PERIOD = int(os.getenv("ADX_PERIOD", "14"))

# ============================================================================
# SEITWÄRTS-STRATEGIEN (KEINE 52W-EXTREME)
# ============================================================================

# Seitwärts-Strategien aktivieren
USE_SIDEWAYS_STRATEGIES = os.getenv("USE_SIDEWAYS_STRATEGIES", "True").lower() in ("true", "1", "yes")

# Iron Condor Parameter
IRON_CONDOR_MIN_DTE = int(os.getenv("IRON_CONDOR_MIN_DTE", "30"))
IRON_CONDOR_MAX_DTE = int(os.getenv("IRON_CONDOR_MAX_DTE", "60"))
IRON_CONDOR_TARGET_DTE = int(os.getenv("IRON_CONDOR_TARGET_DTE", "45"))

# Wing Width für Iron Condor (Abstand zwischen Short/Long Strikes)
IRON_CONDOR_WING_WIDTH_PCT = float(os.getenv("IRON_CONDOR_WING_WIDTH_PCT", "0.08"))  # 8%

# Strike-Offset (Abstand vom aktuellen Preis für Short Strikes)
IRON_CONDOR_STRIKE_OFFSET_PCT = float(os.getenv("IRON_CONDOR_STRIKE_OFFSET_PCT", "0.05"))  # 5%

# ============================================================================
# VOLATILITÄTS-STRATEGIEN (HOHE VOLATILITÄT)
# ============================================================================

# Volatilitäts-Strategien aktivieren
USE_VOLATILITY_STRATEGIES = os.getenv("USE_VOLATILITY_STRATEGIES", "True").lower() in ("true", "1", "yes")

# Long Straddle/Strangle Parameter
VOLATILITY_STRATEGY_MIN_DTE = int(os.getenv("VOLATILITY_STRATEGY_MIN_DTE", "15"))
VOLATILITY_STRATEGY_MAX_DTE = int(os.getenv("VOLATILITY_STRATEGY_MAX_DTE", "45"))
VOLATILITY_STRATEGY_MIN_IV_RANK = float(os.getenv("VOLATILITY_STRATEGY_MIN_IV_RANK", "60"))

# Strike-Offset für Strangle (Abstand vom aktuellen Preis)
VOLATILITY_STRANGLE_OFFSET_PCT = float(os.getenv("VOLATILITY_STRANGLE_OFFSET_PCT", "0.05"))  # 5%

# ============================================================================
# VIX MARKTRISIKO FILTER
# ============================================================================

# VIX-basierte Risikoanalyse aktivieren
USE_VIX_FILTER = os.getenv("USE_VIX_FILTER", "True").lower() in ("true", "1", "yes")

# VIX Schwellenwerte für Risikostufen
VIX_VERY_LOW_MAX = float(os.getenv("VIX_VERY_LOW_MAX", "15.0"))    # Sehr niedrig
VIX_LOW_MAX = float(os.getenv("VIX_LOW_MAX", "20.0"))             # Niedrig
VIX_MODERATE_MAX = float(os.getenv("VIX_MODERATE_MAX", "25.0"))   # Moderate
VIX_HIGH_MAX = float(os.getenv("VIX_HIGH_MAX", "30.0"))           # Hoch
VIX_VERY_HIGH_MAX = float(os.getenv("VIX_VERY_HIGH_MAX", "35.0")) # Sehr hoch
# Über 35 = Extrem

# Risiko-Multiplikatoren für verschiedene VIX-Level
VIX_RISK_MULTIPLIER_VERY_LOW = float(os.getenv("VIX_RISK_MULTIPLIER_VERY_LOW", "1.5"))
VIX_RISK_MULTIPLIER_LOW = float(os.getenv("VIX_RISK_MULTIPLIER_LOW", "1.2"))
VIX_RISK_MULTIPLIER_MODERATE = float(os.getenv("VIX_RISK_MULTIPLIER_MODERATE", "1.0"))
VIX_RISK_MULTIPLIER_HIGH = float(os.getenv("VIX_RISK_MULTIPLIER_HIGH", "0.7"))
VIX_RISK_MULTIPLIER_VERY_HIGH = float(os.getenv("VIX_RISK_MULTIPLIER_VERY_HIGH", "0.5"))
VIX_RISK_MULTIPLIER_EXTREME = float(os.getenv("VIX_RISK_MULTIPLIER_EXTREME", "0.0"))

# ============================================================================
# DATENBANK
# ============================================================================

# Options-Positionen und Signale
OPTIONS_DATABASE_PATH = os.path.join(os.path.dirname(__file__), "data", "options_trading.db")
os.makedirs(os.path.dirname(OPTIONS_DATABASE_PATH), exist_ok=True)
