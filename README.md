# Interactive Brokers Trading Bot

[![GitHub](https://img.shields.io/badge/GitHub-kajoty%2Ftws--bot-blue?logo=github)](https://github.com/kajoty/tws-bot)
[![Python](https://img.shields.io/badge/Python-3.8+-blue?logo=python)](https://www.python.org)
[![Docker](https://img.shields.io/badge/Docker-ready-blue?logo=docker)](https://www.docker.com)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Vollständig funktionsfähiger, modularer Trading-Bot für Interactive Brokers. Handelt Aktien und Optionen, nutzt SQLite für Daten und bietet umfangreiche Performance-Analyse. **Läuft in Docker mit IB Gateway (headless).**

## 🚀 Features

- **IB Gateway Integration**: Headless Betrieb in Docker
- **Multi-Asset**: Aktien (STK) und Optionen (OPT)
- **Risikomanagement**: Automatische Positionsgrößenberechnung
- **Technische Indikatoren**: MA, RSI, MACD, Bollinger Bands, ATR, Volume, 52-Week High/Low
- **Datenbank**: SQLite für historische Daten, Trades, Performance
- **Visualisierung**: Equity Curve, Drawdown, Trade-Statistiken
- **Paper & Live Trading**: Beide Modi unterstützt
- **Docker-Ready**: Vollständig containerisiert mit docker-compose
- **Umfangreiches Logging**: DEBUG bis CRITICAL für Debugging

## 📋 Voraussetzungen

### Docker Setup (Empfohlen)
- Docker & Docker Compose
- Interactive Brokers Account (Paper oder Live)
- IB Gateway Credentials

### Lokaler Betrieb
- Python 3.8+
- Interactive Brokers TWS oder IB Gateway
- Aktiver IB-Account (Paper oder Live)

## 🐳 Docker Installation (Empfohlen)

### Quick Start

```bash
# 1. Repository klonen
git clone https://github.com/kajoty/tws-bot.git
cd tws-bot

# 2. Umgebungsvariablen einrichten
cp .env.example .env
nano .env  # IB Credentials eintragen!

# 3. Container starten
./start-docker.sh
# oder manuell:
docker-compose up -d

# 4. Logs verfolgen
docker-compose logs -f trading-bot
```

### Konfiguration (.env)

Erstelle `.env` aus `.env.example` und passe an:

```env
# IB Gateway Credentials
TWS_USERID=your_username
TWS_PASSWORD=your_password
TRADING_MODE=paper  # oder "live"

# Trading Bot
IB_HOST=ib-gateway  # Docker Container Name
IB_PORT=4002        # 4002=Paper, 4001=Live
IS_PAPER_TRADING=True
DRY_RUN=False       # True = Simulation ohne Orders
WATCHLIST_STOCKS=AAPL,MSFT,GOOGL,AMZN,TSLA
ACCOUNT_SIZE=100000.0
MAX_RISK_PER_TRADE_PCT=0.01
```

**Wichtig**: `.env` enthält sensible Daten - niemals in Git committen!

### Docker Befehle

```bash
# Status prüfen
docker-compose ps

# Logs
docker-compose logs -f          # Alle Logs
docker-compose logs -f trading-bot  # Nur Bot

# Stoppen/Starten
docker-compose down
docker-compose up -d
docker-compose restart

# Neu bauen
docker-compose up -d --build

# VNC Zugriff auf Gateway
# VNC Client → localhost:5900 (Passwort aus .env)
```

**Detaillierte Anleitung**: Siehe [DOCKER.md](DOCKER.md)  
**Quick Reference**: Siehe [DOCKER_QUICKREF.md](DOCKER_QUICKREF.md)

## 💻 Lokale Installation

```bash
# Virtual Environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate    # Windows

# Dependencies
pip install -r requirements.txt

# Konfiguration
cp .env.example .env
nano .env  # Anpassen für lokalen Betrieb:
# IB_HOST=localhost
# IB_PORT=4002  # Gateway oder 7497 für TWS

# Starten
python main.py
```

### TWS/Gateway einrichten
1. Starte TWS oder IB Gateway
2. Einstellungen → API → Settings
3. Aktiviere "Enable ActiveX and Socket Clients"
4. Ports: Gateway 4002/4001, TWS 7497/7496

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
