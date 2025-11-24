# Position Management Guide

## Workflow: Von Signal bis Exit

### 1️⃣ Signal erhalten (Options Scanner)

Der `options_scanner.py` generiert Signale wenn Setups erkannt werden:

```
[LONG PUT] AAPL
52W-Hoch Setup @ $195.50
Strike: 195 DTE: 75
P/E: 32.5 | IV Rank: 72.3
```

**Signal wird gespeichert in DB:** `options_signals` Tabelle

### 2️⃣ Trade manuell ausführen (bei deinem Broker)

- Gehe zu TWS/Broker-Plattform
- Öffne Position gemäß Signal
- Notiere: **Entry Premium**, **Quantity**, **Actual Strike**, **Expiry**

### 3️⃣ Position eintragen (Position Manager)

Starte interaktives CLI:

```bash
start_position_manager.bat
```

**Oder via Python:**

```python
from position_manager import PositionManager

manager = PositionManager()

# Long Put Beispiel
position_id = manager.enter_position(
    symbol="AAPL",
    position_type="LONG_PUT",
    entry_premium=5.20,  # USD pro Kontrakt
    entry_underlying_price=195.50,
    strike=195.0,
    expiry="20250228",  # YYYYMMDD
    right="P",
    quantity=2  # 2 Kontrakte
)

# Bear Call Spread Beispiel
position_id = manager.enter_position(
    symbol="NVDA",
    position_type="BEAR_CALL_SPREAD",
    entry_premium=125.0,  # Credit received (Net Premium)
    entry_underlying_price=520.30,
    strike=540.0,  # Short Strike
    expiry="20250115",
    right="C",
    quantity=1,
    short_strike=540.0,
    long_strike=545.0
)
```

**Was passiert beim Entry:**
- Position wird in `options_positions` Tabelle gespeichert
- Stop-Loss/Take-Profit automatisch berechnet
- Max Risk berechnet (für Cushion-Tracking)
- Pushover-Benachrichtigung gesendet
- Status = `OPEN`

### 4️⃣ Automatisches Monitoring (Position Monitor Service)

Starte Monitor-Service:

```bash
start_position_monitor.bat
```

**Service läuft stündlich (konfigurierbar) und:**
- Holt aktuelle Option Prices via TWS API
- Holt aktuelle Underlying Prices
- Berechnet P&L (Gewinn/Verlust)
- Berechnet DTE (Days to Expiration)
- Prüft Exit-Bedingungen:
  - ✅ **Stop Loss**: Underlying erreicht Stop-Level
  - ✅ **Take Profit**: Premium erreicht Ziel
  - ✅ **Auto Close**: DTE <= Schwelle UND Position im Verlust
  - ✅ **Expiration**: DTE <= 0
- Sendet **Pushover-Alert** bei Exit-Bedingung
- Updated Portfolio-Cushion

### 5️⃣ Exit-Alert erhalten

```
[EXIT ALERT] AAPL
LONG_PUT - TAKE_PROFIT
P&L: $320.00 (+61.5%)
Underlying: $188.30
Premium: $8.40 | DTE: 52
```

**Jetzt:**
- Schließe Position bei deinem Broker
- Markiere Position als geschlossen im Manager

### 6️⃣ Position schließen (Position Manager)

**Via CLI:**

```bash
start_position_manager.bat
# Wähle Option 4: Position schließen
```

**Via Python:**

```python
manager.close_position(position_id=1, exit_reason='TAKE_PROFIT')
```

**Was passiert beim Close:**
- `status` = `CLOSED`
- `exit_timestamp` = jetzt
- `exit_reason` = gespeichert
- Finaler P&L in DB
- Max Risk wird aus Cushion-Berechnung entfernt

---

## Portfolio-Übersicht (Cushion Tracking)

**Via CLI:**

```bash
start_position_manager.bat
# Wähle Option 5: Portfolio-Übersicht
```

**Ausgabe:**

```
======================================================================
  PORTFOLIO ÜBERSICHT
======================================================================
Account Size:        $100,000.00
Offene Positionen:   3
Total Max Risk:      $2,840.00 (2.8%)
Verfügbar:           $97,160.00
Cushion:             97.2%
Total P&L:           $450.00 (+0.45%)
======================================================================

OFFENE POSITIONEN:
----------------------------------------------------------------------

[1] AAPL - LONG_PUT
  Strike: 195.0 | Expiry: 20250228 | DTE: 52
  Entry Premium: $5.20 | Current: $8.40
  P&L: $320.00 (+61.5%)
  Max Risk: $520.00

[2] MSFT - LONG_CALL
  Strike: 365.0 | Expiry: 20250320 | DTE: 68
  Entry Premium: $6.80 | Current: $7.20
  P&L: $40.00 (+5.9%)
  Max Risk: $680.00

[3] NVDA - BEAR_CALL_SPREAD
  Strike: 540.0 | Expiry: 20250115 | DTE: 38
  Entry Premium: $125.00 | Current: $85.00
  P&L: $40.00 (+8.0%)
  Max Risk: $500.00
======================================================================
```

**Cushion Berechnung:**

```python
# Max Risk pro Position:
# - Long Put/Call: Entry Premium * 100 * Quantity
# - Bear Call Spread: (Long Strike - Short Strike) * 100 - Net Premium

total_max_risk = sum(alle offenen Positionen)
available_capital = account_size - total_max_risk
cushion_pct = (available_capital / account_size) * 100

# Beispiel:
# Account: $100,000
# Max Risk: $2,840 (2.8%)
# Cushion: 97.2%
```

**⚠️ Warnung wenn Cushion zu niedrig:**
- Bei Cushion <90%: Warnung
- Bei Cushion <80%: Kritisch - keine neuen Positionen!

---

## Manuelles Position Update (optional)

Falls Monitor-Service nicht läuft, kannst du manuell updaten:

```bash
start_position_manager.bat
# Wähle Option 3: Position updaten
```

**Eingabe:**
- Position ID
- Aktueller Premium
- Aktueller Underlying Preis

**System prüft automatisch alle Exit-Bedingungen!**

---

## Integration mit Signal Scanner

### Automatischer Workflow (empfohlen):

1. **Scanner läuft kontinuierlich:**
   ```bash
   start_options_scanner.bat
   ```

2. **Monitor läuft parallel:**
   ```bash
   start_position_monitor.bat
   ```

3. **Du reagierst auf Pushover-Alerts:**
   - **[SIGNAL]** → Trade manuell ausführen → Position eintragen
   - **[EXIT ALERT]** → Trade manuell schließen → Position schließen

### Semi-Automatisch:

1. Scanner generiert Signale (gespeichert in DB)
2. Du checkst Signale via SQL oder CSV-Export
3. Trade ausführen + Position eintragen
4. Monitor prüft täglich

---

## Datenbank-Schema

### `options_positions` Tabelle

```sql
id                       INTEGER PRIMARY KEY
symbol                   TEXT
position_type            TEXT  -- LONG_PUT, LONG_CALL, BEAR_CALL_SPREAD
strike                   REAL
expiry                   TEXT  -- YYYYMMDD
right                    TEXT  -- P oder C
entry_premium            REAL  -- USD
entry_underlying_price   REAL
dte_at_entry            INTEGER
quantity                 INTEGER
stop_loss_underlying     REAL
take_profit_premium      REAL
auto_close_dte          INTEGER
current_premium          REAL  -- Updated by Monitor
current_underlying_price REAL  -- Updated by Monitor
current_dte             INTEGER
pnl                     REAL
pnl_pct                 REAL
status                  TEXT  -- OPEN, CLOSED
short_strike            REAL  -- Für Spreads
long_strike             REAL  -- Für Spreads
spread_type             TEXT
net_premium             REAL
max_risk                REAL
entry_timestamp         DATETIME
exit_timestamp          DATETIME
exit_reason             TEXT
```

---

## Tipps & Best Practices

### Position Entry:
- ✅ Trage Position **sofort nach Ausführung** ein
- ✅ Nutze **exakte Werte** (Entry Premium, nicht Limit Order!)
- ✅ Prüfe Cushion BEFORE Trade

### Position Monitoring:
- ✅ Lasse Monitor-Service **24/7 laufen** (oder täglich)
- ✅ Reagiere auf Exit-Alerts **schnell**
- ✅ Schließe Positionen **manuell** beim Broker, dann im System

### Portfolio Management:
- ✅ Max 5 offene Positionen gleichzeitig (konfigurierbar)
- ✅ Max 1% Risk pro Trade
- ✅ Cushion immer >80%
- ✅ Diversifiziere über Sektoren

### Risk Management:
- ⚠️ **Nie** alle 3 Strategien auf **dasselbe Symbol**
- ⚠️ **Stop Loss** immer respektieren
- ⚠️ **Auto Close** vor Expiration (Theta Decay!)

---

## Logs & Debugging

### Log-Dateien:
- `logs/options_scanner.log` - Scanner Activity
- `logs/position_monitor.log` - Monitor Updates
- `logs/signal_service.log` - Aktien-Scanner

### Debug-Mode:
```bash
# In .env setzen:
LOG_LEVEL=DEBUG
```

### Häufige Probleme:

**"Position nicht gefunden"**
→ Prüfe Position ID mit Option 2 (Alle Positionen anzeigen)

**"Keine Marktdaten verfügbar"**
→ TWS muss laufen + Market Data Subscription aktiv

**"Cushion-Warnung"**
→ Schließe Positionen oder erhöhe ACCOUNT_SIZE in .env

---

## API-Nutzung (für eigene Scripts)

```python
from position_manager import PositionManager

manager = PositionManager()

# Neue Position
pos_id = manager.enter_position(...)

# Update Position
result = manager.update_position(pos_id, current_premium=8.40, current_underlying_price=188.30)

if result['exit_reason']:
    print(f"Exit-Bedingung: {result['exit_reason']}")
    manager.close_position(pos_id, result['exit_reason'])

# Portfolio Summary
summary = manager.get_portfolio_summary()
print(f"Cushion: {summary['cushion_pct']:.1f}%")
print(f"Total P&L: ${summary['total_pnl']:.2f}")

# Alle offenen Positionen
positions = manager.get_all_open_positions()
for pos in positions:
    print(f"{pos['symbol']}: {pos['position_type']} - P&L: ${pos['pnl']:.2f}")
```

---

## Zusammenfassung

```
┌─────────────────────────────────────────────────────────────┐
│                   OPTIONS TRADING WORKFLOW                   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. Scanner → Signal generieren (Auto)                      │
│  2. Pushover → Benachrichtigung erhalten                    │
│  3. Broker → Trade manuell ausführen                        │
│  4. Position Manager → Position eintragen                   │
│  5. Monitor Service → Auto-Tracking (hourly)                │
│  6. Exit Alert → Pushover bei Exit-Bedingung                │
│  7. Broker → Trade schließen                                │
│  8. Position Manager → Position schließen                   │
│                                                             │
│  Portfolio-Cushion → Immer im Blick! 📊                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```
