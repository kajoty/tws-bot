"""
Zentrale Konfigurationsdatei für den IB Trading Bot.
Alle wichtigen Parameter sind hier definiert und können angepasst werden.
"""

import os
from datetime import datetime

# ============================================================================
# IB TWS API VERBINDUNG
# ============================================================================

# TWS/Gateway Verbindungsparameter
IB_HOST = "127.0.0.1"
IB_PORT_PAPER = 7497      # Paper Trading Port
IB_PORT_LIVE = 7496       # Live Trading Port
IB_CLIENT_ID = 1          # Eindeutige Client-ID (0-32)

# Trading-Modus (WICHTIG: False = LIVE TRADING!)
IS_PAPER_TRADING = True

# Live Trading Bestätigung überspringen (NUR FÜR ERFAHRENE NUTZER!)
# True = Keine Sicherheitsabfrage beim Start im Live-Modus
# False = Bestätigung erforderlich (empfohlen)
SKIP_LIVE_TRADING_CONFIRMATION = False

# Automatische Port-Auswahl basierend auf Modus
IB_PORT = IB_PORT_PAPER if IS_PAPER_TRADING else IB_PORT_LIVE

# ============================================================================
# KAPITAL & RISIKOMANAGEMENT
# ============================================================================

# Starkapital (nur für Tracking, nicht für echte Kontoverwaltung)
ACCOUNT_SIZE = 100000.0

# Maximales Risiko pro Trade (als Prozentsatz des Kapitals)
MAX_RISK_PER_TRADE_PCT = 0.01  # 1% des Kapitals

# Maximale Anzahl gleichzeitiger Positionen
MAX_CONCURRENT_POSITIONS = 5

# Minimale Positionsgröße (in USD)
MIN_POSITION_SIZE = 100.0

# ============================================================================
# STRATEGIE-AUSWAHL
# ============================================================================

# Welche Trading-Strategie verwenden?
# 'STOCK' = Klassische Aktienstrategie (MA, RSI, MACD)
# 'OPTIONS' = Konträre Optionsstrategie (52W-Extrema + Fundamentals)
TRADING_STRATEGY = 'OPTIONS'  # Ändern zu 'STOCK' für Aktienhandel

# ============================================================================
# GEBÜHREN & KOSTEN
# ============================================================================

# AKTIEN-TRADING KOSTEN
# Kommission pro Aktien-Order
STOCK_COMMISSION_PER_ORDER = 5.00  # USD pro Order (Buy oder Sell)
STOCK_MIN_COMMISSION = 1.00        # Minimum Kommission pro Order
STOCK_MAX_COMMISSION = None        # Optional: Maximum Cap

# OPTIONEN-TRADING KOSTEN
# Kommission pro Options-Contract
OPTION_COMMISSION_PER_CONTRACT = 2.50  # EUR 2,50 pro Contract
OPTION_MIN_COMMISSION = 2.50           # Minimum Kommission pro Order
OPTION_MAX_COMMISSION = None           # Optional: Maximum Cap

# Beispiel Interactive Brokers Tiered Pricing:
# - US Stocks: $0.005 per share, min $1.00, max 0.5% of trade value
# - US Options: $0.65-0.70 per contract
# - European Options: ~€2.00-2.50 per contract

# WEITERE KOSTEN
# Regulatory Fees (USA)
SEC_FEE_PER_MILLION = 27.80  # USD pro Million USD Verkaufswert (nur Sells)
FINRA_TAF_PER_SHARE = 0.000166  # USD pro Aktie (nur Sells, max $8.30)

# Börsendatengebühren (monatlich)
MONTHLY_MARKET_DATA_FEE = 10.00  # USD pro Monat

# Währungsumrechnung
EUR_TO_USD_RATE = 1.10  # Wechselkurs EUR/USD (aktualisieren!)
CURRENCY_CONVERSION_SPREAD = 0.0002  # 0.02% Spread bei FX

# Slippage-Schätzung (als Prozentsatz)
SLIPPAGE_PCT = 0.001  # 0.1%

# Legacy (für Rückwärtskompatibilität)
COMMISSION_PER_ORDER = STOCK_COMMISSION_PER_ORDER

# ============================================================================
# DATENBANK
# ============================================================================

# Datenbankpfad
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "trading_data.db")

# Erstelle data-Verzeichnis, falls nicht vorhanden
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Datenmanagement
DATA_MAX_AGE_DAYS = 1       # Daten älter als X Tage werden als veraltet betrachtet
DATA_RETENTION_DAYS = 730   # Alte Daten werden nach X Tagen gelöscht (2 Jahre)

# ============================================================================
# LOGGING
# ============================================================================

# Log-Level: DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_LEVEL = "INFO"

# Log-Datei
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"trading_bot_{datetime.now().strftime('%Y%m%d')}.log")

# ============================================================================
# STRATEGIE-AUSWAHL
# ============================================================================

# Welche Trading-Strategie verwenden?
# 'STOCK' = Klassische Aktienstrategie (MA, RSI, MACD)
# 'OPTIONS' = Konträre Optionsstrategie (52W-Extrema + Fundamentals)
TRADING_STRATEGY = 'OPTIONS'  # Ändern zu 'STOCK' für Aktienhandel

# Minimale Confidence für Optionssignale (0.0 - 1.0)
MIN_CONFIDENCE_OPTIONS = 0.7  # 70% Mindest-Confidence für Options-Trades

# ============================================================================
# STRATEGIE-PARAMETER (STOCK)
# ============================================================================

# Symbole zum Handeln (Beispiele) - DEPRECATED: Nutze watchlist.csv stattdessen
WATCHLIST_STOCKS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]

# Watchlist CSV Pfad
WATCHLIST_CSV_PATH = "watchlist.csv"

# Optionshandel aktivieren
ENABLE_OPTIONS_TRADING = True

# Historische Daten Parameter
HISTORICAL_DATA_DURATION = "1 Y"  # z.B. "1 M", "3 M", "1 Y"
HISTORICAL_DATA_BAR_SIZE = "1 day"  # z.B. "1 min", "5 mins", "1 hour", "1 day"

# Technische Indikatoren
MA_SHORT_PERIOD = 20   # Kurzer gleitender Durchschnitt
MA_LONG_PERIOD = 50    # Langer gleitender Durchschnitt
RSI_PERIOD = 14        # RSI Periode
RSI_OVERSOLD = 30      # RSI Überverkauft-Level
RSI_OVERBOUGHT = 70    # RSI Überkauft-Level

# Volatilitäts-Filter
MIN_IV_PERCENTILE = 30  # Minimum implizite Volatilität (Perzentil)
MAX_IV_PERCENTILE = 70  # Maximum implizite Volatilität (Perzentil)

# ============================================================================
# KONTRÄRE OPTIONSSTRATEGIE (52-WOCHEN-EXTREMA)
# ============================================================================

# Handelsuniversum Filter
MIN_MARKET_CAP = 1_000_000_000  # $1 Milliarde Minimum (TEMPORÄR gelockert - avg_volume_20d fehlt in DB)
MIN_AVG_VOLUME = 0              # Deaktiviert (TEMPORÄR - avg_volume_20d fehlt in DB)

# 52-Wochen-Extrema Trigger
TRIGGER_DISTANCE_52W_PERCENT = 0.02  # 2% Nähe zu 52W Hoch/Tief

# Long Put Strategie (Short am 52W-Hoch)
LONG_PUT_PE_OVERVALUATION = 1.5      # 150% über Branchen-Median P/E
LONG_PUT_IV_RANK_MIN = 30            # IV Rank 30-80 Range (nicht Extreme)
LONG_PUT_IV_RANK_MAX = 80            # IV Rank 30-80 Range (nicht Extreme)
LONG_PUT_DTE_MIN = 60                # Minimum Days to Expiration
LONG_PUT_DTE_MAX = 90                # Maximum Days to Expiration
LONG_PUT_STOP_LOSS_PCT = 0.015       # 1.5% über 52W-Hoch
LONG_PUT_TAKE_PROFIT_PCT = 0.50      # 50% Gewinn auf Prämie
LONG_PUT_AUTO_CLOSE_DTE = 7          # Schließe ALLE bei DTE=7 (Theta-Schutz)

# Long Call Strategie (Long am 52W-Tief)
LONG_CALL_IV_RANK_MIN = 30           # IV Rank 30-80 Range (nicht Extreme)
LONG_CALL_IV_RANK_MAX = 80           # IV Rank 30-80 Range (nicht Extreme)
LONG_CALL_DTE_MIN = 90               # Minimum Days to Expiration
LONG_CALL_DTE_MAX = 120              # Maximum Days to Expiration
LONG_CALL_DELTA_TARGET = 0.40        # Delta für OTM Strike
LONG_CALL_STOP_LOSS_PCT = 0.015      # 1.5% unter 52W-Tief
LONG_CALL_TAKE_PROFIT_PCT = 0.75     # 75% Gewinn auf Prämie
LONG_CALL_AUTO_CLOSE_DTE = 7         # Schließe ALLE bei DTE=7 (Theta-Schutz)

# Position Sizing für Optionen
MAX_OPTION_RISK_PER_TRADE_PCT = 0.01 # 1% des Kapitals pro Optionsposition (Prämie)

# Handelszeiten (EST)
TRADING_START_HOUR = 9               # 9:30 AM EST (als float: 9.5)
TRADING_START_MINUTE = 30
TRADING_END_HOUR = 16                # 4:00 PM EST
TRADING_END_MINUTE = 0

# ============================================================================
# PERFORMANCE & REPORTING
# ============================================================================

# Plot-Verzeichnis
PLOT_DIR = os.path.join(os.path.dirname(__file__), "plots")
os.makedirs(PLOT_DIR, exist_ok=True)

# Performance-Update-Intervall (in Sekunden)
PERFORMANCE_UPDATE_INTERVAL = 3600  # Stündlich

# ============================================================================
# ENTWICKLUNG & DEBUG
# ============================================================================

# Dry-Run Modus (keine echten Orders, nur Simulation)
DRY_RUN = False

# Verbose Logging für API-Calls
VERBOSE_API_LOGGING = False
