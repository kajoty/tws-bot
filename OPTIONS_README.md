# Options-Trading Bot - Konträre 52-Wochen-Extrem-Strategie

## Übersicht

Dieser Options-Scanner implementiert eine konträre Trading-Strategie basierend auf 52-Wochen-Extrema, Fundamentaldaten und impliziter Volatilität.

### Strategien

#### 1. **Long Put** (Short am 52W-Hoch)
- **Trigger**: Aktienkurs innerhalb 2% des 52-Wochen-Hochs
- **Fundamental**: P/E Ratio >150% des Branchen-Medians (Überbewertung)
- **Volatilität**: IV Rank >70 (hohe Volatilität)
- **Options**: ATM Put, 60-90 DTE
- **Exit**: Stop Loss bei +1.5% über 52W-Hoch, Take Profit bei +50% Premium

#### 2. **Long Call** (Long am 52W-Tief)
- **Trigger**: Aktienkurs innerhalb 2% des 52-Wochen-Tiefs
- **Fundamental**: Positive Free Cash Flow Yield (Unterbewertung)
- **Volatilität**: IV Rank <30 (niedrige Volatilität)
- **Options**: OTM Call (Delta ~0.40), 90-120 DTE
- **Exit**: Stop Loss bei -1.5% unter 52W-Tief, Take Profit bei +75% Premium

#### 3. **Bear Call Spread** (Short am 52W-Hoch mit Protection) 🆕
- **Trigger**: Aktienkurs innerhalb 2% des 52-Wochen-Hochs
- **Fundamental**: P/E Ratio >150% des Branchen-Medians (Überbewertung)
- **Volatilität**: IV Rank >70 (hohe Prämieneinnahme)
- **Options**: Sell Call (Delta 0.25-0.35) + Buy Call ($5 höher), 30-45 DTE
- **Exit**: Stop Loss wenn Underlying den Long Strike erreicht, Take Profit bei 50-75% der Prämie
- **Vorteil**: Begrenztes Risiko durch Long Call Protection

## Installation

```bash
# 1. Virtuelle Umgebung bereits erstellt
# 2. Requirements bereits installiert

# 3. Konfiguriere .env mit Options-Parametern
# (Bereits vorhanden - siehe unten)

# 4. TWS muss laufen!
```

## Start

### Windows
```cmd
start_options_scanner.bat
```

### PowerShell
```powershell
.\venv\Scripts\Activate.ps1
python options_scanner.py
```

### Parallel mit Aktien-Scanner
```bash
# Terminal 1: Aktien-Scanner
python signal_service.py

# Terminal 2: Options-Scanner  
python options_scanner.py
```

## Konfiguration (.env)

### Filter
```bash
MIN_MARKET_CAP=5000000000      # Nur Large Caps
MIN_AVG_VOLUME=500000          # Liquide Aktien
MAX_POSITION_SIZE_PCT=0.01     # 1% pro Trade
```

### Long Put (52W-Hoch)
```bash
PUT_PROXIMITY_TO_HIGH_PCT=0.02  # Innerhalb 2% vom Hoch
PUT_PE_RATIO_MULTIPLIER=1.5     # 150% über Branche
PUT_MIN_IV_RANK=70              # Hohe Volatilität
PUT_MIN_DTE=60                  # Mindestens 60 Tage
PUT_MAX_DTE=90                  # Maximal 90 Tage
```

### Long Call (52W-Tief)
```bash
CALL_PROXIMITY_TO_LOW_PCT=0.02  # Innerhalb 2% vom Tief
CALL_MIN_FCF_YIELD=0.0          # Positive FCF
CALL_MAX_IV_RANK=30             # Niedrige Volatilität
CALL_MIN_DTE=90                 # Mindestens 90 Tage
CALL_MAX_DTE=120                # Maximal 120 Tage
CALL_TAKE_PROFIT_PCT=0.75       # 75% Take Profit
```

### Bear Call Spread (52W-Hoch) 🆕
```bash
SPREAD_PROXIMITY_TO_HIGH_PCT=0.02  # Innerhalb 2% vom Hoch
SPREAD_PE_RATIO_MULTIPLIER=1.5     # 150% über Branche
SPREAD_MIN_IV_RANK=70              # Hohe Volatilität
SPREAD_MIN_DTE=30                  # Mindestens 30 Tage
SPREAD_MAX_DTE=45                  # Maximal 45 Tage
SPREAD_SHORT_DELTA_MIN=0.25        # Short Delta Min
SPREAD_SHORT_DELTA_MAX=0.35        # Short Delta Max
SPREAD_STRIKE_WIDTH=5.0            # $5 Spread Width
```

## Funktionsweise

### Scan-Ablauf (alle 60 Minuten)
1. **Historische Daten laden** (252 Tage = 52 Wochen)
2. **Fundamentaldaten abrufen** (P/E, FCF, Market Cap)
3. **Options-Chain laden** (Strikes, Expirations)
4. **52W-Hoch/Tief berechnen**
5. **Filter anwenden** (Proximity, Fundamental, IV Rank)
6. **Passende Option auswählen** (Strike + DTE)
7. **Signal generieren** + Pushover-Benachrichtigung

### Benachrichtigungen

Bei Signal erhältst du:
```
[LONG PUT] AAPL
52W-Hoch Setup @ $195.50
Strike: 195 DTE: 75
P/E: 32.5 | IV Rank: 72.3
```

```
[LONG CALL] MSFT
52W-Tief Setup @ $350.20
Strike: 365 DTE: 105
FCF Yield: 0.0325 | IV Rank: 28.1
```

```
[BEAR CALL SPREAD] NVDA
52W-Hoch Setup @ $520.30
Spread: 540/545 DTE: 38
P/E: 85.3 | IV Rank: 73.5
Net Premium: $125.00 | Max Risk: $500.00
```

## Datenbank

### Neue Tabellen

- **`options_signals`**: Alle generierten Signale mit Details
- **`options_positions`**: Tracking offener Positionen (für zukünftiges Position Management)
- **`fundamental_data`**: Cache für Fundamentaldaten (7 Tage)
- **`iv_history`**: IV-Historie für IV Rank Berechnung

### Abfragen

```python
from database import DatabaseManager

db = DatabaseManager()

# Alle Options-Signale der letzten 30 Tage
signals = pd.read_sql_query("""
    SELECT * FROM options_signals 
    WHERE timestamp >= date('now', '-30 days')
    ORDER BY timestamp DESC
""", db.conn)

# Long Put Signale mit hohem IV Rank
puts = pd.read_sql_query("""
    SELECT * FROM options_signals 
    WHERE signal_type = 'LONG_PUT' 
    AND iv_rank > 75
    ORDER BY iv_rank DESC
""", db.conn)
```

## Handelszeiten

Scanner ist nur aktiv während NYSE-Handelszeiten:
- **Start**: 9:30 AM EST
- **Ende**: 4:00 PM EST

Scans außerhalb dieser Zeit werden übersprungen.

## TWS API Anforderungen

### Benötigte Daten:
1. **Historische Daten**: 252 Tage (52 Wochen) OHLC
2. **Fundamentaldaten**: P/E Ratio, FCF, Market Cap, Sektor
3. **Options-Chain**: Strikes, Expirations
4. **Options Greeks**: IV, Delta (für Strike-Auswahl)

### Rate Limits:
- 60 Historische Daten-Requests pro 10 Minuten
- Deshalb: 2 Sekunden Pause zwischen Symbolen
- Bei 100 Symbolen: ~3.5 Minuten pro Scan

## Position Management (TODO)

Aktuell werden nur **Signale generiert**. Für echtes Position-Management:

1. **Position öffnen**: Nach Signal manuell oder automatisch
2. **Tägliches Monitoring**:
   - Underlying Preis vs. Stop Loss
   - Option Premium vs. Take Profit
   - DTE-Check für Auto-Close
3. **Position schließen**: Bei Exit-Bedingung

Implementierung in `options_position_manager.py` (geplant).

## Logs

- **Console**: INFO-Level
- **File**: `logs/options_scanner.log` (alle Levels)

Debug-Mode:
```bash
# In .env setzen:
LOG_LEVEL=DEBUG
```

## Troubleshooting

### "Keine Fundamentaldaten"
- Nicht alle Aktien haben vollständige Daten in TWS
- Check: Ist Symbol korrekt? Hat Aktie P/E Ratio?
- Lösung: Symbol aus Watchlist entfernen oder manuell Daten pflegen

### "Keine Options-Chain"
- Aktie hat möglicherweise keine gelisteten Options
- Lösung: Nur Options-fähige Aktien in Watchlist

### "IV Rank nicht verfügbar"
- Zu wenig historische IV-Daten
- Fallback: Nutzt historische Volatilität als Proxy
- Nach einigen Scans: Echte IV-Historie aufgebaut

### TWS Verbindungsfehler
- TWS muss laufen BEVOR Scanner startet
- Client-ID 2 (unterscheidet sich vom Aktien-Scanner)
- Ports: 7497 (Paper) / 7496 (Live)

## Nächste Schritte

1. **Test mit 2-3 Symbolen** (z.B. AAPL, MSFT, TSLA)
2. **Erste Signale abwarten** (kann Tage dauern bis Setup erscheint)
3. **Branchen-PE-Daten verbessern** (externe API)
4. **Position-Management implementieren**
5. **Backtesting** mit historischen Daten

## Wichtige Hinweise

⚠️ **Disclaimer**: Dies ist ein Signalgenerator, kein automatisches Trading-System. Alle Trades müssen manuell ausgeführt werden.

⚠️ **Paper Trading**: Teste IMMER zuerst im Paper-Account!

⚠️ **Options-Risiko**: Options können wertlos verfallen. Nie mehr riskieren als du verlieren kannst.

⚠️ **IV Crush**: Nach Earnings kann IV stark fallen - vermeide Options kurz vor Earnings!
