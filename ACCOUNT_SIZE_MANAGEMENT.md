# Account Size Management

## Übersicht

Der Position Manager kann die Account Size auf **zwei Arten** verwalten:

### ✅ Option 1: Manuelle Konfiguration (`.env` Datei)

**Standard-Methode** - Du trägst deinen Account-Wert einmalig ein:

```bash
# In .env Datei:
ACCOUNT_SIZE=100000.0
```

**Vorteile:**
- ✅ Einfach
- ✅ Keine TWS-Verbindung nötig
- ✅ Für Paper Trading mit festem Startkapital

**Nachteile:**
- ⚠️ Muss manuell aktualisiert werden wenn Account wächst/schrumpft
- ⚠️ Cushion-Berechnung basiert auf statischem Wert

### ✅ Option 2: Automatischer Abruf von TWS (Empfohlen für Live Trading)

**Dynamische Methode** - System holt aktuellen Wert von TWS:

**Beim Start des Position Managers:**
```
Account Size Quelle:
1. Aus .env Datei (manuell konfiguriert)
2. Automatisch von TWS abrufen  ← Wähle diese Option

Wahl (1 oder 2): 2
```

**Was wird von TWS geholt:**
- **Net Liquidation Value**: Gesamtwert deines Accounts (Cash + Positionen)
- **Buying Power**: Verfügbare Kaufkraft
- **TWS Cushion**: Margin-Puffer vom Broker

**Vorteile:**
- ✅ Immer aktuell
- ✅ Berücksichtigt tägliche P&L
- ✅ Echte Buying Power statt geschätzte
- ✅ Für Live Trading ideal

**Nachteile:**
- ⚠️ TWS muss laufen
- ⚠️ Benötigt API-Zugriff

---

## Setup

### Schritt 1: Fallback-Wert in `.env` setzen

Trage einen **Fallback-Wert** ein (wird genutzt wenn TWS-Abruf fehlschlägt):

```bash
# .env
ACCOUNT_SIZE=100000.0  # Fallback wenn TWS nicht verfügbar
```

### Schritt 2: Position Manager starten

```bash
start_position_manager.bat
```

**Wähle beim Start:**
- **Option 1**: Nutzt `ACCOUNT_SIZE` aus `.env`
- **Option 2**: Holt Account Size von TWS

---

## Verwendung

### Manueller Abruf (via CLI)

```bash
start_position_manager.bat
# Option 2 wählen beim Start

# Im Menü:
# Option 6: Account Size aktualisieren
```

### Programmatische Nutzung

```python
from position_manager import PositionManager

# Mit .env Account Size
manager = PositionManager(use_tws_account_size=False)

# Mit TWS Account Size
manager = PositionManager(use_tws_account_size=True)

# Account Size manuell aktualisieren
manager._update_account_size_from_tws()

# Portfolio Summary mit aktueller Account Size
summary = manager.get_portfolio_summary(refresh_account_size=True)
print(f"Account Size: ${summary['account_size']:,.2f}")
```

### Account Data testen

```bash
python account_data_manager.py
```

**Output:**
```
======================================================================
  ACCOUNT SUMMARY
======================================================================
Net Liquidation:   $1,012,449.76
Buying Power:      $6,749,665.07
Total Cash:        $1,011,640.05
Available Funds:   $1,012,449.76
Excess Liquidity:  $1,012,449.76
TWS Cushion:       100.0%
======================================================================
```

---

## TWS Account Data erklärt

| Field | Beschreibung | Verwendung |
|-------|--------------|------------|
| **Net Liquidation** | Gesamtwert: Cash + Positionen + Optionen | → **Account Size** für Portfolio-Berechnung |
| **Buying Power** | Verfügbare Kaufkraft (inkl. Margin) | → Für Options-Margin-Berechnung |
| **Total Cash** | Reine Cash-Position | → Liquide Mittel |
| **Available Funds** | Verfügbar für neue Trades | → Pre-Trade-Check |
| **Excess Liquidity** | Puffer über Margin-Minimum | → Risk Management |
| **TWS Cushion** | Broker-Cushion (%) | → Margin-Call-Abstand |

### Net Liquidation vs. Buying Power

**Net Liquidation** = Dein tatsächlicher Account-Wert
```
Net Liq = $100,000
```

**Buying Power** = Was du theoretisch kaufen kannst (mit Margin)
```
Buying Power = $400,000 (bei 4x Portfolio Margin)
```

**Für Position Manager:**
- Wir nutzen **Net Liquidation** als `account_size`
- Risk-Berechnung basiert auf echtem Kapital, nicht Buying Power
- Verhindert Über-Leveraging

---

## Cushion-Berechnung

### Position Manager Cushion (unser System):

```python
account_size = Net Liquidation von TWS (oder .env)
total_max_risk = Summe aller Max Risks (offene Positionen)
available_capital = account_size - total_max_risk
cushion_pct = (available_capital / account_size) * 100

# Beispiel:
# Account: $100,000
# Max Risk: $2,500 (2.5% in Positionen)
# Cushion: 97.5%
```

### TWS Cushion (vom Broker):

TWS berechnet Cushion basierend auf **Margin Requirements**:

```
TWS Cushion = (Excess Liquidity / Net Liquidation) * 100
```

**TWS Cushion Schwellenwerte:**
- >30%: ✅ Sicher
- <30%: ⚠️ Warnung
- <25%: 🚨 Margin Call Risiko

---

## Best Practices

### Für Paper Trading:
```bash
# .env
ACCOUNT_SIZE=100000.0

# Im Position Manager:
Wahl: 1 (aus .env)
```
→ Fester Startkapital-Wert, kein TWS-Overhead

### Für Live Trading:
```bash
# .env
ACCOUNT_SIZE=100000.0  # Nur Fallback

# Im Position Manager:
Wahl: 2 (von TWS)
```
→ Immer aktuelle Account Size, berücksichtigt P&L

### Für automatische Services:

**Position Monitor Service** sollte Account Size täglich aktualisieren:

```python
# In position_monitor_service.py anpassen:
manager = PositionManager(use_tws_account_size=True)

# Bei jedem Monitor-Run:
summary = manager.get_portfolio_summary(refresh_account_size=True)
```

---

## Troubleshooting

### "Keine valide Account Size von TWS erhalten"

**Ursachen:**
- TWS nicht gestartet
- API-Zugriff nicht aktiviert
- Falscher Port (7497 Paper / 7496 Live)

**Lösung:**
→ System nutzt automatisch Fallback aus `.env`

### "TWS Cushion 100%"

Das ist **korrekt** wenn:
- Du keine Margin nutzt (nur Cash Account)
- Alle Positionen mit eigenem Kapital gedeckt
- Paper Trading Account

### Account Size weicht von TWS ab

**Normal wenn:**
- Offene Positionen mit unrealisierten P&L
- Pending Orders
- Währungsschwankungen (bei Multi-Currency)

**Check:**
```python
# Detaillierte Account Daten holen:
from account_data_manager import AccountDataManager

manager = AccountDataManager()
manager.connect_to_tws()
data = manager.get_account_data()

print(f"Net Liq: ${data['NetLiquidation']:,.2f}")
print(f"Cash: ${data['TotalCashValue']:,.2f}")
# Differenz = Unrealisierte P&L
```

---

## API Referenz

### `AccountDataManager`

```python
from account_data_manager import AccountDataManager

manager = AccountDataManager()

# Verbinden
if manager.connect_to_tws():
    
    # Alle Account-Daten holen
    data = manager.get_account_data()
    # Returns: {'NetLiquidation': ..., 'BuyingPower': ..., ...}
    
    # Nur Net Liquidation
    net_liq = manager.get_net_liquidation()
    
    # Nur Buying Power
    buying_power = manager.get_buying_power()
    
    # TWS Cushion
    cushion = manager.get_cushion()
    
    manager.disconnect_from_tws()
```

### Helper-Funktion

```python
from account_data_manager import get_account_size_from_tws

# Schneller One-Liner
account_size = get_account_size_from_tws()
# Returns: Float oder None bei Fehler
```

---

## Zusammenfassung

```
┌─────────────────────────────────────────────────────────────┐
│              ACCOUNT SIZE MANAGEMENT OPTIONS                 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  📋 MANUELL (.env)                                          │
│  ────────────────                                           │
│  + Einfach, kein TWS nötig                                  │
│  - Muss manuell aktualisiert werden                         │
│  → Ideal für Paper Trading mit festem Startkapital          │
│                                                             │
│  🔄 AUTOMATISCH (TWS API)                                   │
│  ──────────────────────                                     │
│  + Immer aktuell (Net Liquidation Value)                    │
│  + Berücksichtigt tägliche P&L                              │
│  - Benötigt laufende TWS                                    │
│  → Ideal für Live Trading                                   │
│                                                             │
│  💡 EMPFEHLUNG                                              │
│  ──────────────                                             │
│  Paper: .env (Option 1)                                     │
│  Live:  TWS (Option 2)                                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Dein aktueller Account (von TWS abgerufen):**
```
Net Liquidation: $1,012,449.76
Buying Power:    $6,749,665.07
TWS Cushion:     100.0%
```
