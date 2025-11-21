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
# GEBÜHREN & KOSTEN
# ============================================================================

# Geschätzte Kommission pro Order (Buy/Sell = 2 Orders)
COMMISSION_PER_ORDER = 1.0  # USD pro Order

# Slippage-Schätzung (als Prozentsatz)
SLIPPAGE_PCT = 0.001  # 0.1%

# ============================================================================
# DATENBANK
# ============================================================================

# Datenbankpfad
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "trading_data.db")

# Erstelle data-Verzeichnis, falls nicht vorhanden
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

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
# STRATEGIE-PARAMETER
# ============================================================================

# Symbole zum Handeln (Beispiele)
WATCHLIST_STOCKS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]

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
