# Quick Start Guide - IB Trading Bot

## 🚀 In 5 Minuten starten

### Schritt 1: Installation
```bash
# Repository klonen
git clone https://github.com/kajoty/tws-bot.git
cd tws-bot

# Virtual Environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# ODER: venv\Scripts\activate  # Windows

# Dependencies installieren
pip install -r requirements.txt
```

### Schritt 2: TWS/Gateway starten
1. Öffne Interactive Brokers TWS oder IB Gateway
2. **Einstellungen → API → Settings**
3. ✅ Aktiviere: **"Enable ActiveX and Socket Clients"**
4. Port: **7497** (Paper Trading)

### Schritt 3: Konfiguration
Öffne `config.py`:

```python
# Deine Watchlist
WATCHLIST_STOCKS = ["AAPL", "MSFT", "GOOGL"]

# Startkapital
ACCOUNT_SIZE = 100000.0

# Für erste Tests:
DRY_RUN = True  # Keine echten Orders!
```

### Schritt 4: Bot starten
```bash
python main.py
```

**Erwartete Ausgabe:**
```
✓ Erfolgreich mit TWS verbunden
Lade historische Daten...
  - AAPL... ✓
TRADING GESTARTET
```

**Bot beenden:** Ctrl+C

---

## 📊 Performance analysieren

### Charts anzeigen
```bash
ls -lt plots/  # Neueste Charts
```

### Datenbank abfragen
```bash
sqlite3 data/trading_data.db
SELECT * FROM trades ORDER BY timestamp DESC LIMIT 10;
```

---

## 🔧 Erweiterte Nutzung

### Eigene Strategie
1. Öffne `strategy.py`
2. Finde `check_strategy()` (Zeile ~170)
3. Füge deine Logik hinzu:

```python
# Beispiel: Volumen-Filter
if latest['volume'] > latest['volume_ma'] * 3:
    buy_score += 0.25
    signals.append("Hohes Volumen")
```

### Backtest
```python
from strategy import TradingStrategy
from database import DatabaseManager

db = DatabaseManager()
strategy = TradingStrategy()

df = db.load_historical_data('AAPL', start_date='2023-01-01')
result = strategy.backtest(df, initial_capital=10000)

print(f"Final Equity: ${result['equity'].iloc[-1]:.2f}")
```

---

## 🐛 Troubleshooting

| Problem | Lösung |
|---------|--------|
| Verbindung fehlgeschlagen | TWS API-Einstellungen prüfen, Port 7497 |
| Keine Orders | `DRY_RUN = False` in config.py |
| Keine Daten | TWS eingeloggt? Market Data Subscription? |

**Logs prüfen:**
```bash
tail -f logs/trading_bot_*.log
```

---

## 🎯 Von Paper zu Live Trading

⚠️ **NUR wenn ausgiebig getestet!**

1. Teste **Wochen/Monate** im Paper-Modus
2. Analysiere Performance-Metriken
3. In `config.py`:
```python
IS_PAPER_TRADING = False  # ⚠️ LIVE!
ACCOUNT_SIZE = 5000.0     # Klein starten
MAX_RISK_PER_TRADE_PCT = 0.005  # 0.5%
```

Bot fragt nach Bestätigung vor Live-Trading.

---

## 📚 Weitere Hilfe

- [README.md](README.md) - Vollständige Dokumentation
- [.github/copilot-instructions.md](.github/copilot-instructions.md) - Für Entwickler
- [IB API Docs](https://interactivebrokers.github.io/tws-api/)
