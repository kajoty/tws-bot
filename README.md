# TWS Signal Service

Signal-Service für Interactive Brokers TWS. Generiert Entry/Exit Trading-Signale basierend auf technischen Indikatoren und sendet Pushover-Benachrichtigungen auf dein Smartphone. **Kein automatisches Trading - nur Signale!**

## 🚀 Features

- **📊 Technische Analyse**: Moving Average Crossover, RSI, MACD
- **📱 Pushover Benachrichtigungen**: Echtzeit Entry/Exit Signale auf dein Smartphone
- **💾 Signal-Tracking**: SQLite Datenbank für Historie und Statistiken
- **🔄 Automatischer Scan**: Konfigurierbare Scan-Intervalle
- **📈 Position Tracking**: Exit-Signale für aktive Positionen (Stop Loss, Take Profit)
- **🎯 Multi-Signal Logic**: Kombiniert mehrere Indikatoren für bessere Qualität
- **🌐 Web Dashboard**: Live-Monitoring mit Charts und Statistiken
- **📈 Interaktive Charts**: Plotly-basierte Preis-Charts mit Indikatoren
- **📊 Echtzeit-Daten**: Live-Updates alle 60 Sekunden

## 📋 Voraussetzungen

1. **Interactive Brokers Account** (Paper oder Live)
2. **TWS (Trader Workstation)** - Lokale Installation
3. **Python 3.8+**
4. **Pushover Account** - Für Push-Benachrichtigungen

## ⚡ Setup

### 1. TWS API aktivieren

1. TWS starten und anmelden
2. **File → Global Configuration → API → Settings**
3. ✅ **Enable ActiveX and Socket Clients** aktivieren
4. ✅ **Read-Only API** deaktivieren (wenn du Orders senden willst)
5. **Trusted IPs** hinzufügen: `127.0.0.1`
6. **Socket Port**: `7497` (Paper) oder `7496` (Live)
7. TWS neu starten

### 2. Pushover Setup

1. Registriere dich auf [pushover.net](https://pushover.net/)
2. Notiere deinen **User Key** (Dashboard)
3. Erstelle neue Application → Erhalte **API Token**
4. Installiere Pushover App auf deinem Smartphone

### 3. Installation

```bash
# Repository klonen
git clone https://github.com/kajoty/tws-bot.git
cd tws-bot

# Abhängigkeiten installieren
pip3 install -r requirements.txt

# Konfiguration erstellen
cp .env.example .env
nano .env  # Credentials eintragen
```

### 4. Konfiguration (.env)

```bash
# TWS Verbindung
IB_HOST=127.0.0.1
IB_PORT=7497  # 7497 für Paper, 7496 für Live
IB_CLIENT_ID=1
IS_PAPER_TRADING=True

# Pushover Credentials
PUSHOVER_USER_KEY=your_user_key_here
PUSHOVER_API_TOKEN=your_api_token_here

# Watchlist (kommasepariert, keine Leerzeichen!)
WATCHLIST_STOCKS=AAPL,MSFT,GOOGL,AMZN,TSLA

# Risikomanagement
ACCOUNT_SIZE=100000.0
MAX_RISK_PER_TRADE_PCT=0.01  # 1% Risiko pro Trade
MAX_CONCURRENT_POSITIONS=5
```

### 5. Test Pushover

```bash
# Teste Pushover-Benachrichtigungen
python3 pushover_notifier.py
```

Du solltest eine Test-Nachricht auf deinem Smartphone erhalten.

### 6. Service starten

```bash
# TWS muss bereits laufen!
python3 signal_service.py
```

## 🌐 Web Dashboard

### Features
- **📊 Live-Dashboard**: Aktuelle Signale und Trefferquoten
- **📈 Interaktive Charts**: Preis-Charts mit Moving Averages
- **📱 Responsive Design**: Funktioniert auf Desktop und Mobile
- **🔄 Auto-Refresh**: Echtzeit-Updates alle 60 Sekunden
- **🎯 Filter-Optionen**: Nach Trefferquoten filtern

### Dashboard starten

```bash
# Web-App starten (läuft parallel zum Signal-Service)
python3 web_app.py
```

Öffne http://127.0.0.1:5000 in deinem Browser.

### Dashboard-Features

#### Haupt-Dashboard
- **Zusammenfassungsstatistiken**: Gesamt-Ticker, durchschnittliche Trefferquote, aktive Signale
- **Aktuelle Signale**: Entry/Exit Signale mit Risikomanagement-Details
- **Trefferquoten-Übersicht**: Gefilterte Ansicht nach Signal-Qualität
- **Historische Signale**: Letzte Trading-Signale aus der Datenbank

#### Chart-Ansichten
- **Preis-Charts**: Mit Moving Average Indikatoren
- **Direkte Links**: Von jedem Ticker zur Chart-Ansicht
- **Zoom & Pan**: Interaktive Chart-Navigation

## 🔔 Benachrichtigungen

### Entry Signal
```
🟢 AAPL ENTRY SIGNAL
Preis: $175.50
Anzahl: 10 Aktien
Stop Loss: $171.50 (-2.28%)
Take Profit: $184.28 (+5.00%)

Grund: MA Crossover + RSI < 30
```

### Exit Signal
```
🟢 AAPL EXIT SIGNAL
Preis: $184.50
Anzahl: 10 Aktien
Entry: $175.50
P&L: +$90.00 (+5.13%)

Grund: Take Profit erreicht
```

## 📊 Signal-Logik

### Entry Bedingungen (min. 2 müssen erfüllt sein)

1. **MA Crossover**: Short MA > Long MA (Aufwärtstrend)
2. **RSI Oversold**: RSI < 30 (überverkauft)
3. **MACD Bullish**: MACD > Signal Line (Kaufsignal)

### Exit Bedingungen (eine muss erfüllt sein)

1. **Stop Loss**: Preis < Entry - 2%
2. **Take Profit**: Preis > Entry + 5%
3. **RSI Overbought**: RSI > 70 (überkauft)

## ⚙️ Konfiguration (config.py)

```python
# Technische Indikatoren
MA_SHORT_PERIOD = 20
MA_LONG_PERIOD = 50
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

# Signal-Logik
MIN_SIGNALS_FOR_ENTRY = 2  # Minimum Anzahl Bedingungen
STOP_LOSS_PCT = 0.02       # 2% Stop Loss
TAKE_PROFIT_PCT = 0.05     # 5% Take Profit

# Scan-Intervall
SCAN_INTERVAL = 300  # 5 Minuten (in Sekunden)
```

## 📁 Projektstruktur

```
tws-bot/
├── signal_service.py      # Hauptprogramm (Signal-Generierung)
├── web_app.py            # Flask Web-Dashboard
├── pushover_notifier.py   # Pushover Benachrichtigungen
├── database.py            # SQLite Datenbank
├── config.py              # Konfiguration
├── requirements.txt       # Python Dependencies
├── templates/             # HTML Templates
│   ├── dashboard.html     # Haupt-Dashboard
│   └── chart.html         # Chart-Ansicht
├── .env                   # Credentials (nicht in Git!)
├── .env.example           # Beispiel-Konfiguration
└── README.md              # Diese Datei
```

## 🔍 Datenbank

Signale werden in `trading.db` gespeichert:

```python
# Signale abrufen
from database import DatabaseManager
db = DatabaseManager()

# Letzte 7 Tage
signals = db.get_signals(days=7)

# Statistiken
stats = db.get_signal_stats(days=30)
print(f"Win Rate: {stats['win_rate']:.1f}%")
print(f"Total P&L: ${stats['total_pnl']:.2f}")
```

## 🛠️ Troubleshooting

### TWS Connection Error
```
❌ Keine Verbindung zu TWS
```
**Lösung:**
- TWS läuft und ist angemeldet?
- API-Zugriff in TWS aktiviert?
- Port korrekt? (7497 Paper, 7496 Live)
- `127.0.0.1` in Trusted IPs?

### Pushover Error
```
❌ Pushover-Fehler: invalid user/token
```
**Lösung:**
- User Key und API Token korrekt in `.env`?
- Test: `python3 pushover_notifier.py`

### No Historical Data
```
⚠️ Keine historischen Daten für AAPL
```
**Lösung:**
- TWS Marktdaten-Abonnement aktiv?
- Symbol existiert? (US-Aktien: Ticker ohne Exchange)
- Paper Trading Account hat verzögerte Daten (15-20 Min)

## ⚠️ Wichtige Hinweise

1. **Kein automatisches Trading**: Dieser Service sendet nur Signale, führt keine Orders aus
2. **Manuelle Ausführung**: Du entscheidest, ob du das Signal tradest
3. **Paper Trading empfohlen**: Teste zuerst mit Paper Account
4. **Marktdaten**: Paper Trading hat verzögerte Daten (15-20 Min)
5. **Position Tracking**: Service merkt sich Entry-Signale für Exit-Berechnung

## 📝 Lizenz

MIT License - siehe [LICENSE](LICENSE)

## 🤝 Support

- GitHub Issues: [github.com/kajoty/tws-bot/issues](https://github.com/kajoty/tws-bot/issues)
- IBKR API Docs: [interactivebrokers.github.io](https://interactivebrokers.github.io/)
- Pushover Docs: [pushover.net/api](https://pushover.net/api)
