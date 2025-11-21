# Interactive Brokers Trading Bot

[![GitHub](https://img.shields.io/badge/GitHub-kajoty%2Ftws--bot-blue?logo=github)](https://github.com/kajoty/tws-bot)
[![Python](https://img.shields.io/badge/Python-3.8+-blue?logo=python)](https://www.python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Vollständig funktionsfähiger, modularer Trading-Bot für Interactive Brokers TWS. Handelt Aktien und Optionen, nutzt SQLite für Daten und bietet umfangreiche Performance-Analyse.

## 🚀 Features

- **IB TWS API Integration**: Vollständige `EClient`/`EWrapper` Implementation
- **Multi-Asset**: Aktien (STK) und Optionen (OPT)
- **Risikomanagement**: Automatische Positionsgrößenberechnung
- **Technische Indikatoren**: MA, RSI, MACD, Bollinger Bands, ATR
- **Datenbank**: SQLite für historische Daten, Trades, Performance
- **Visualisierung**: Equity Curve, Drawdown, Trade-Statistiken
- **Paper & Live Trading**: Beide Modi unterstützt
- **Logging**: Umfangreich für Debugging

## 📋 Voraussetzungen

- Python 3.8+
- Interactive Brokers TWS oder IB Gateway
- Aktiver IB-Account (Paper oder Live)

## 🔧 Installation

```bash
# Virtual Environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate    # Windows

# Dependencies
pip install -r requirements.txt
```

## ⚙️ Konfiguration

TWS/Gateway einrichten:
1. Starte TWS oder IB Gateway
2. Einstellungen → API → Settings
3. Aktiviere "Enable ActiveX and Socket Clients"
4. Port: 7497 (Paper), 7496 (Live)

`config.py` anpassen:
```python
IS_PAPER_TRADING = True  # False für Live!
ACCOUNT_SIZE = 100000.0
MAX_RISK_PER_TRADE_PCT = 0.01  # 1% pro Trade
WATCHLIST_STOCKS = ["AAPL", "MSFT", "GOOGL"]
DRY_RUN = True  # Keine echten Orders
```

## 🎯 Verwendung

```bash
python main.py
```

Bot beenden mit `Ctrl+C` (erstellt Performance-Report).

## 📁 Projektstruktur

```
tws-bot/
├── config.py              # Konfiguration
├── ib_trading_bot.py      # Haupt-Bot (EClient/EWrapper)
├── database.py            # SQLite-Management
├── risk_management.py     # Risiko & Positionsgrößen
├── strategy.py            # Trading-Strategie
├── performance.py         # Performance-Analyse
├── main.py                # Entry-Point
├── data/                  # SQLite-Datenbank
├── logs/                  # Log-Dateien
└── plots/                 # Performance-Charts
```

## 🔄 Workflow

1. **Initialisierung**: Verbindung mit TWS
2. **Datenabfrage**: Historische Daten laden
3. **Strategieprüfung**: Technische Analyse
4. **Risikobewertung**: Limits prüfen, Größe berechnen
5. **Order-Placement**: Automatische Orders
6. **Monitoring**: Stop-Loss, Performance-Tracking

## 🛠️ Module

### IBTradingBot (`ib_trading_bot.py`)
- Erbt von `EClient` + `EWrapper`
- Verwaltet Verbindung und Callbacks
- Orchestriert alle Komponenten

### DatabaseManager (`database.py`)
- Tabellen: `historical_data`, `trades`, `positions`, `performance`
- Methoden: `save_historical_data()`, `load_historical_data()`, `save_trade()`

### RiskManager (`risk_management.py`)
- `calculate_position_size()`: Optimale Größe basierend auf Risiko
- `can_open_position()`: Prüft Limits
- `check_stop_loss()`: Überwacht Stop-Loss

### TradingStrategy (`strategy.py`)
- `calculate_indicators()`: MA, RSI, MACD, Bollinger, ATR
- `check_strategy()`: BUY/SELL/HOLD mit Confidence-Score

### PerformanceAnalyzer (`performance.py`)
- `plot_performance()`: Equity, Drawdown, Returns
- `calculate_metrics()`: Sharpe, Sortino, Max Drawdown

## 📊 Performance-Metriken

- Total Return (%)
- Maximum Drawdown (%)
- Sharpe Ratio (annualisiert)
- Sortino Ratio
- Win Rate (%)
- Profit Factor

## 🔐 Sicherheit

- **Paper Trading zuerst!** Immer erst testen
- **DRY_RUN**: Strategie-Tests ohne Orders
- **Stop-Loss**: Automatisch basierend auf ATR
- **Positionslimits**: Maximale Anzahl konfigurierbar
- **Risikolimit**: Pro Trade einstellbar

## 🐛 Debugging

```bash
# Logs prüfen
tail -f logs/trading_bot_*.log

# Verbose Logging (config.py)
VERBOSE_API_LOGGING = True
LOG_LEVEL = "DEBUG"
```

**Häufige Probleme**:
- Verbindung fehlgeschlagen → TWS API-Settings prüfen
- Order nicht ausgeführt → DRY_RUN prüfen
- Keine Daten → TWS-Subscription prüfen

## 📚 Ressourcen

- [IB API Documentation](https://interactivebrokers.github.io/tws-api/)
- [Python API Guide](https://interactivebrokers.github.io/tws-api/introduction.html)
- `.github/copilot-instructions.md` - Für Entwickler/AI

## ⚠️ Disclaimer

Für Bildungszwecke. Trading birgt Risiken. Verwendung auf eigene Gefahr. Keine Haftung für Verluste.

## 📝 Lizenz

MIT License
