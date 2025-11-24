# Options-Trading Bot - Implementierungsstatus

## ✅ Implementiert

### 1. Konfiguration (`options_config.py`)
- ✅ Alle Parameter aus deinem Prompt implementiert
- ✅ Long Put Settings (52W-Hoch, P/E Ratio, IV Rank, DTE 60-90, Stop-Loss/Take-Profit)
- ✅ Long Call Settings (52W-Tief, FCF Yield, IV Rank, DTE 90-120, Stop-Loss/Take-Profit)
- ✅ Handelsuniversum-Filter (Marktkapitalisierung, Volumen)
- ✅ Risikomanagement (1% Position Size, Auto-Close bei DTE)

### 2. Scanner-Grundstruktur (`options_scanner.py`)
- ✅ TWS API Integration (EWrapper/EClient)
- ✅ 52-Wochen-Hoch/Tief Berechnung
- ✅ IV Rank Berechnung (mit historischer Volatilität als Proxy)
- ✅ Handelszeiten-Prüfung (EST 9:30-16:00)
- ✅ Signal-Erkennung Logik:
  - `check_long_put_setup()` - Prüft alle Put-Kriterien
  - `check_long_call_setup()` - Prüft alle Call-Kriterien
- ✅ Pushover-Benachrichtigungen bei Signalen

## 🚧 Noch zu implementieren (TODO)

### 3. Fundamentaldaten-Integration
**Benötigt:** TWS API Calls für:
```python
# Request Fundamental Data
self.reqFundamentalData(reqId, contract, "ReportsFinSummary", [])

# Parse aus XML:
- P/E Ratio (Trailing)
- Free Cash Flow (FCF)
- Marktkapitalisierung
- Sektor/Branche
- Branchen-Median-KGV (externe API oder manuell pflegen)
```

**Hinweis:** TWS liefert Fundamentaldaten als XML via `fundamentalData()` Callback.

### 4. Options-Chain Integration
**Benötigt:** TWS API Calls für Options:
```python
# Request Options-Chain
self.reqSecDefOptParams(reqId, underlying_symbol, "", "STK", conId)

# Dann für jede Option:
self.reqContractDetails(reqId, option_contract)

# Greeks und IV abrufen:
self.reqMktData(reqId, option_contract, "", False, False, [])
```

**Zu implementieren:**
- Strike-Auswahl (ATM für Puts, OTM mit Delta ~0.40 für Calls)
- DTE-Filter (60-90 oder 90-120 Tage)
- Implizite Volatilität aus Options-Preis
- Greeks (Delta, Theta, Vega) für Position-Management

### 5. Position-Management
**Zu implementieren:**
```python
class OptionsPositionManager:
    def track_position(self, option_contract, entry_premium, underlying_entry):
        # Speichere Position mit:
        # - Entry Premium (gezahlte Prämie)
        # - Underlying Entry Price
        # - DTE bei Entry
        # - Stop-Loss Level (Underlying)
        # - Take-Profit Level (Option Premium)
    
    def check_exit_conditions(self):
        # Prüfe täglich:
        # 1. Stop-Loss: Underlying über/unter Schwellenwert
        # 2. Take-Profit: Option Premium +50%/+75%
        # 3. Auto-Close: DTE 10/20 und verlustbehaftet
```

### 6. Marktkapitalisierung & Volumen-Filter
**Benötigt:**
```python
# Request Contract Details für Fundamentals
self.reqContractDetails(reqId, contract)

# Oder via Scanner:
self.reqScannerSubscription(reqId, scanner_subscription, [], [])
# Mit Filter: marketCapAbove=5000000000, avgVolumeAbove=500000
```

### 7. Datenbank-Erweiterung
Neue Tabellen in `database.py`:
```sql
CREATE TABLE options_positions (
    id INTEGER PRIMARY KEY,
    symbol TEXT,
    option_type TEXT,  -- 'LONG_PUT' oder 'LONG_CALL'
    strike REAL,
    expiry TEXT,
    entry_premium REAL,
    entry_underlying_price REAL,
    dte_at_entry INTEGER,
    stop_loss REAL,
    take_profit REAL,
    status TEXT,  -- 'OPEN', 'CLOSED_PROFIT', 'CLOSED_LOSS', 'CLOSED_AUTO'
    entry_timestamp DATETIME,
    exit_timestamp DATETIME
);

CREATE TABLE options_signals (
    id INTEGER PRIMARY KEY,
    signal_type TEXT,
    symbol TEXT,
    underlying_price REAL,
    high_52w REAL,
    low_52w REAL,
    iv_rank REAL,
    pe_ratio REAL,
    fcf_yield REAL,
    recommended_strike REAL,
    recommended_expiry TEXT,
    timestamp DATETIME
);
```

## 🎯 Nächste Schritte

### Minimal Viable Product (MVP):
1. **Fundamentaldaten laden** (P/E, FCF, Market Cap)
2. **Options-Chain abrufen** (für Strike-Auswahl)
3. **Tatsächliche IV** statt historische Volatilität
4. **Vollständiger Scan-Loop** mit allen Filtern

### Empfohlene Reihenfolge:
```
1. Test mit 1-2 Symbolen (z.B. AAPL, TSLA)
2. Fundamentaldaten-Integration testen
3. Options-Chain für diese Symbole laden
4. Erste Signale generieren (Paper-Modus)
5. Position-Tracking implementieren
6. Auf volle Watchlist ausweiten
```

## 📝 Konfiguration in `.env`

Füge hinzu:
```bash
# Options-Trading
MIN_MARKET_CAP=5000000000
MIN_AVG_VOLUME=500000
MAX_POSITION_SIZE_PCT=0.01

# Long Put (52W-High)
PUT_PROXIMITY_TO_HIGH_PCT=0.02
PUT_PE_RATIO_MULTIPLIER=1.5
PUT_MIN_IV_RANK=70
PUT_MIN_DTE=60
PUT_MAX_DTE=90
PUT_STOP_LOSS_PCT=0.015
PUT_TAKE_PROFIT_PCT=0.50
PUT_AUTO_CLOSE_DTE=10

# Long Call (52W-Low)
CALL_PROXIMITY_TO_LOW_PCT=0.02
CALL_MIN_FCF_YIELD=0.0
CALL_MAX_IV_RANK=30
CALL_MIN_DTE=90
CALL_MAX_DTE=120
CALL_TARGET_DELTA=0.40
CALL_STOP_LOSS_PCT=0.015
CALL_TAKE_PROFIT_PCT=0.75
CALL_AUTO_CLOSE_DTE=20

# Scanner
OPTIONS_SCAN_INTERVAL=3600  # 1 Stunde
```

## 🧪 Testing

```bash
# Test Options-Scanner
python options_scanner.py

# Integration in Hauptservice
# Später: options_scanner läuft parallel zu signal_service
```

## ⚠️ Wichtige Hinweise

1. **TWS API Limitierungen:**
   - Fundamentaldaten: Nicht für alle Aktien verfügbar
   - Options-Daten: Rate Limits beachten
   - Branchen-KGV: Muss extern beschafft werden

2. **Paper-Trading:**
   - Options in Paper-Account können sich anders verhalten
   - Greeks sind approximiert
   - Slippage nicht 1:1 mit Live

3. **Externe Daten:**
   - Branchen-Median-KGV → eventuell manuell pflegen oder von API (z.B. Alpha Vantage, Yahoo Finance)
   - IV Rank → TWS liefert nur aktuelle IV, Historie muss gespeichert werden

Soll ich mit der Implementierung der fehlenden Teile fortfahren? Wo möchtest du anfangen?
