"""
Konfigurationsdatei für TWS Trading Signal Service mit Pushover Benachrichtigungen.
"""

import os
from datetime import datetime
from dotenv import load_dotenv

# Lade .env Datei
load_dotenv(override=True)

# ============================================================================
# TWS VERBINDUNG
# ============================================================================

# TWS Verbindungsparameter
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT_PAPER = 7497      
IB_PORT_LIVE = 7496     
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1"))

# Trading-Modus
IS_PAPER_TRADING = os.getenv("IS_PAPER_TRADING", "True").lower() in ("true", "1", "yes")
IB_PORT = int(os.getenv("IB_PORT", IB_PORT_PAPER if IS_PAPER_TRADING else IB_PORT_LIVE))

# ============================================================================
# PUSHOVER BENACHRICHTIGUNGEN
# ============================================================================

# Pushover API Credentials (https://pushover.net/)
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY", "")
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN", "")

# Benachrichtigungs-Einstellungen
PUSHOVER_PRIORITY = int(os.getenv("PUSHOVER_PRIORITY", "0"))  # -2=lowest, -1=low, 0=normal, 1=high, 2=emergency
PUSHOVER_SOUND = os.getenv("PUSHOVER_SOUND", "pushover")  # pushover, bike, bugle, cashregister, classical, etc.

# ============================================================================
# TRADING STRATEGIE
# ============================================================================

# Watchlist (kommasepariert)
WATCHLIST_STOCKS = os.getenv("WATCHLIST_STOCKS", "AAPL,MSFT,GOOGL,AMZN,TSLA").split(",")

# Risikomanagement
ACCOUNT_SIZE = float(os.getenv("ACCOUNT_SIZE", "100000.0"))
MAX_RISK_PER_TRADE_PCT = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "0.01"))
MAX_CONCURRENT_POSITIONS = int(os.getenv("MAX_CONCURRENT_POSITIONS", "5"))
MIN_POSITION_SIZE = float(os.getenv("MIN_POSITION_SIZE", "100.0"))

# Stop-Loss & Take-Profit (als Prozentsatz)
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.02"))  # 2%
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.05"))  # 5%

# ============================================================================
# TECHNISCHE INDIKATOREN
# ============================================================================

# Moving Averages
MA_SHORT_PERIOD = int(os.getenv("MA_SHORT_PERIOD", "20"))
MA_LONG_PERIOD = int(os.getenv("MA_LONG_PERIOD", "50"))

# RSI
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_OVERSOLD = float(os.getenv("RSI_OVERSOLD", "30"))
RSI_OVERBOUGHT = float(os.getenv("RSI_OVERBOUGHT", "70"))

# MACD
MACD_FAST = int(os.getenv("MACD_FAST", "12"))
MACD_SLOW = int(os.getenv("MACD_SLOW", "26"))
MACD_SIGNAL = int(os.getenv("MACD_SIGNAL", "9"))

# ============================================================================
# SIGNALLOGIK
# ============================================================================

# Wie viele Signale müssen für Entry übereinstimmen?
MIN_SIGNALS_FOR_ENTRY = int(os.getenv("MIN_SIGNALS_FOR_ENTRY", "2"))

# Signale verwenden
USE_MA_CROSSOVER = os.getenv("USE_MA_CROSSOVER", "True").lower() in ("true", "1", "yes")
USE_RSI = os.getenv("USE_RSI", "True").lower() in ("true", "1", "yes")
USE_MACD = os.getenv("USE_MACD", "False").lower() in ("true", "1", "yes")

# ============================================================================
# DATENBANK & LOGGING
# ============================================================================

# Datenbank
DATABASE_PATH = os.path.join(os.path.dirname(__file__), "data", "trading_signals.db")
os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.path.join(os.path.dirname(__file__), "logs", "signal_service.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# ============================================================================
# SCANNER EINSTELLUNGEN
# ============================================================================

# Scan-Intervall in Sekunden
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "300"))  # 5 Minuten

# Historische Daten
HISTORY_DAYS = int(os.getenv("HISTORY_DAYS", "90"))
DATA_MAX_AGE_DAYS = int(os.getenv("DATA_MAX_AGE_DAYS", "1"))

# ============================================================================
# SIGNAL-MODUS
# ============================================================================

# Nur Signale senden (kein automatisches Trading)
SIGNAL_ONLY_MODE = True

# DRY RUN für Tests
DRY_RUN = os.getenv("DRY_RUN", "False").lower() in ("true", "1", "yes")

# ============================================================================
# OPTIONEN-STRATEGIE FILTER (aus .env)
# ============================================================================
# Handelsuniversum
MIN_MARKET_CAP = int(os.getenv("MIN_MARKET_CAP", "5000000000"))
MIN_AVG_VOLUME = int(os.getenv("MIN_AVG_VOLUME", "500000"))
MAX_POSITION_SIZE_PCT = float(os.getenv("MAX_POSITION_SIZE_PCT", "0.01"))

# LONG PUT
PUT_PROXIMITY_TO_HIGH_PCT = float(os.getenv("PUT_PROXIMITY_TO_HIGH_PCT", "0.02"))
PUT_PE_RATIO_MULTIPLIER = float(os.getenv("PUT_PE_RATIO_MULTIPLIER", "1.5"))
PUT_MIN_IV_RANK = int(os.getenv("PUT_MIN_IV_RANK", "70"))
PUT_MIN_DTE = int(os.getenv("PUT_MIN_DTE", "60"))
PUT_MAX_DTE = int(os.getenv("PUT_MAX_DTE", "90"))

# LONG CALL
CALL_PROXIMITY_TO_LOW_PCT = float(os.getenv("CALL_PROXIMITY_TO_LOW_PCT", "0.02"))
CALL_MIN_FCF_YIELD = float(os.getenv("CALL_MIN_FCF_YIELD", "0.0"))
CALL_MAX_IV_RANK = int(os.getenv("CALL_MAX_IV_RANK", "30"))
CALL_MIN_DTE = int(os.getenv("CALL_MIN_DTE", "90"))
CALL_MAX_DTE = int(os.getenv("CALL_MAX_DTE", "120"))
CALL_TARGET_DELTA = float(os.getenv("CALL_TARGET_DELTA", "0.40"))

# BEAR CALL SPREAD
SPREAD_PROXIMITY_TO_HIGH_PCT = float(os.getenv("SPREAD_PROXIMITY_TO_HIGH_PCT", "0.02"))
SPREAD_PE_RATIO_MULTIPLIER = float(os.getenv("SPREAD_PE_RATIO_MULTIPLIER", "1.5"))
SPREAD_MIN_IV_RANK = int(os.getenv("SPREAD_MIN_IV_RANK", "70"))
SPREAD_MIN_DTE = int(os.getenv("SPREAD_MIN_DTE", "30"))
SPREAD_MAX_DTE = int(os.getenv("SPREAD_MAX_DTE", "45"))
SPREAD_SHORT_DELTA_MIN = float(os.getenv("SPREAD_SHORT_DELTA_MIN", "0.25"))
SPREAD_SHORT_DELTA_MAX = float(os.getenv("SPREAD_SHORT_DELTA_MAX", "0.35"))
SPREAD_STRIKE_WIDTH = float(os.getenv("SPREAD_STRIKE_WIDTH", "5.0"))
