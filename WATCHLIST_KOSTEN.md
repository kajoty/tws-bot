# Watchlist & Kosten-Management

## Watchlist-System (CSV-basiert)

Die Watchlist wurde von hardcodierten Werten auf ein flexibles CSV-System umgestellt.

### Struktur der `watchlist.csv`

```csv
symbol,market_cap,avg_volume,sector,pe_ratio,fcf,enabled,notes
AAPL,2800000000000,50000000,Technology,28.5,99900000000,true,High liquidity
```

**Spalten:**
- `symbol`: Ticker Symbol
- `market_cap`: Marktkapitalisierung in USD
- `avg_volume`: Durchschnittliches tägliches Handelsvolumen
- `sector`: Sektor (z.B. Technology, Healthcare)
- `pe_ratio`: P/E Ratio (für konträre Strategie)
- `fcf`: Free Cash Flow in USD (für konträre Strategie)
- `enabled`: true/false - aktiv/inaktiv
- `notes`: Freitext-Notizen

### S&P 500 Watchlist generieren

```powershell
# Generiert watchlist.csv mit allen S&P 500 Symbolen
python generate_sp500_watchlist.py

# Optionen im Skript:
# 1. Alle ~500 Symbole (dauert 15-20 Min)
# 2. Top 50 (schneller Test)
# 3. Nur aktuelle aus config.py
```

Das Skript:
- Lädt S&P 500 Liste von Wikipedia
- Reichert Daten mit Yahoo Finance an (Marktkapitalisierung, Volumen, P/E, FCF)
- Speichert in `watchlist.csv`

### Watchlist verwalten (CLI)

```powershell
# Liste alle Symbole
python watchlist_cli.py list

# Symbol hinzufügen
python watchlist_cli.py add TSLA --market-cap 700000000000 --sector Automotive --pe 65.8

# Symbol deaktivieren (bleibt in Liste, wird nicht gehandelt)
python watchlist_cli.py disable TSLA

# Symbol aktivieren
python watchlist_cli.py enable TSLA

# Metadaten aktualisieren
python watchlist_cli.py update AAPL --pe 28.5 --fcf 99900000000

# Details zu Symbol
python watchlist_cli.py info AAPL

# Nach Kriterien filtern
python watchlist_cli.py filter --min-market-cap 1000000000000
python watchlist_cli.py filter --min-volume 5000000

# Symbol entfernen
python watchlist_cli.py remove TSLA
```

### Programmatische Verwendung

```python
from watchlist_manager import WatchlistManager

wl = WatchlistManager()

# Aktive Symbole abrufen
symbols = wl.get_active_symbols()  # ['AAPL', 'MSFT', ...]

# Metadaten abrufen
meta = wl.get_symbol_metadata('AAPL')
print(meta['market_cap'])  # 2800000000000
print(meta['pe_ratio'])    # 28.5

# Symbol hinzufügen
wl.add_symbol('NVDA', {
    'market_cap': 1200000000000,
    'sector': 'Technology',
    'enabled': True
})

# Nach Sektor filtern
tech_stocks = wl.get_symbols_by_sector('Technology')

# Nach Größe filtern
large_caps = wl.get_symbols_by_filter(
    min_market_cap=500_000_000_000,  # $500B+
    min_volume=10_000_000            # 10M+ avg volume
)
```

---

## Trading-Kosten-System

Detaillierte Kostenberechnung für realistische Performance-Analyse.

### Konfiguration (`config.py`)

```python
# AKTIEN-KOSTEN
STOCK_COMMISSION_PER_ORDER = 1.00  # USD pro Order
STOCK_MIN_COMMISSION = 1.00

# OPTIONEN-KOSTEN
OPTION_COMMISSION_PER_CONTRACT = 2.50  # EUR 2,50 pro Contract
OPTION_MIN_COMMISSION = 2.50

# REGULATORY FEES (USA)
SEC_FEE_PER_MILLION = 27.80       # $27.80 per $1M (nur Sells)
FINRA_TAF_PER_SHARE = 0.000166    # $0.000166 per share (nur Sells)

# SLIPPAGE
SLIPPAGE_PCT = 0.001  # 0.1%

# WÄHRUNG
EUR_TO_USD_RATE = 1.10
CURRENCY_CONVERSION_SPREAD = 0.0002  # 0.02%
```

### Trading-Kosten berechnen

```python
from trading_costs import TradingCostCalculator

calc = TradingCostCalculator()

# Aktien-Trade
stock_costs = calc.calculate_stock_commission(
    quantity=100,
    price=150.00,
    is_sell=False
)
print(f"Kosten: ${stock_costs['total_cost']:.2f}")
# Output: Kosten: $16.50 (Kommission + Slippage)

# Options-Trade
option_costs = calc.calculate_option_commission(
    contracts=10,
    premium=5.00,
    is_sell=False
)
print(f"Kosten: ${option_costs['total_cost']:.2f}")
# Output: Kosten: $35.00 (10 × €2.50 + Slippage)

# Round-Trip (Buy + Sell)
rt_costs = calc.calculate_round_trip_cost(
    instrument_type="option",
    quantity=10,
    entry_price=3.50,
    exit_price=5.25
)

gross_profit = (5.25 - 3.50) * 10 * 100  # $1,750
net_profit = calc.adjust_profit_for_costs(gross_profit, rt_costs)
print(f"Brutto: ${gross_profit:.2f}")
print(f"Netto:  ${net_profit:.2f}")
# Output: Brutto: $1750.00, Netto: $1690.00
```

### Kosten-Demo

```powershell
python trading_costs.py
```

Zeigt:
- Aktien-Trade Beispiel (100 AAPL @ $150)
- Options-Trade Beispiel (5 Contracts @ $5 Prämie)
- Round-Trip mit Net Profit Berechnung

### Kosten-Aufschlüsselung

**Aktien (100 AAPL @ $150, Verkauf):**
```
Kommission:     $1.00
SEC Fee:        $0.42   (($15,000 / $1M) × $27.80)
FINRA TAF:      $0.02   (100 × $0.000166)
Slippage:       $15.00  ($15,000 × 0.1%)
─────────────────────
Total:          $16.44
Als % vom Trade: 0.110%
```

**Optionen (10 Contracts @ $5 Prämie):**
```
Kommission:     $25.00  (10 × €2.50)
Slippage:       $10.00  ($5,000 × 0.2%)
─────────────────────
Total:          $35.00
Als % vom Trade: 0.700%
```

### Integration in Bot

Der `TradingCostCalculator` wird automatisch in:
- `RiskManager` für Position Sizing
- `PerformanceAnalyzer` für Net P&L
- `DatabaseManager` für Trade-Recording

eingebunden.

---

## Migration Guide

### Von altem zu neuem System

**Vorher:**
```python
# config.py
WATCHLIST_STOCKS = ["AAPL", "MSFT", "GOOGL"]
COMMISSION_PER_ORDER = 1.0
```

**Nachher:**
```python
# watchlist.csv wird geladen
from watchlist_manager import WatchlistManager
wl = WatchlistManager()
symbols = wl.get_active_symbols()

# Detaillierte Kosten
from trading_costs import TradingCostCalculator
calc = TradingCostCalculator()
costs = calc.calculate_option_commission(10, 5.00)
```

### Datenbank-Migration

Neue Tabellen in `database.py`:
- `fundamental_data` - P/E, FCF, Marktkapitalisierung
- `iv_history` - IV für IV Rank Berechnung
- `sector_benchmarks` - Branchen-P/E Medians

**Automatisch erstellt** beim ersten Bot-Start.

---

## Best Practices

### Watchlist

1. **Aktiviere nur handelbare Symbole**: `enabled=true`
2. **Halte Metadaten aktuell**: Nutze `watchlist_cli.py update`
3. **Backup**: `watchlist.csv` ist unter Git-Versionskontrolle
4. **Fundamentaldaten**: Werden automatisch durch Bot aktualisiert

### Kosten

1. **Aktualisiere Wechselkurs**: `EUR_TO_USD_RATE` in `config.py`
2. **Prüfe Broker-Gebühren**: IB ändert Preise regelmäßig
3. **Berücksichtige bei Backtests**: Verwende `adjust_profit_for_costs()`
4. **Track Kosten**: Alle Trades speichern Kommission in DB

---

## Troubleshooting

### Watchlist wird nicht geladen
```
ERROR: Watchlist-CSV nicht gefunden
```
**Lösung**: `python generate_sp500_watchlist.py` ausführen

### Yahoo Finance Timeout
```
ERROR: Timeout beim Abrufen von Symbol XYZ
```
**Lösung**: Nutze Option "2" (Top 50) für schnelleren Test

### Kosten erscheinen zu hoch
```
Warnung: Kosten sind 5% des Trade-Werts
```
**Lösung**: 
- Prüfe `OPTION_COMMISSION_PER_CONTRACT` in `config.py`
- Kleine Trades haben proportional höhere Kosten
- Erhöhe Position Size oder reduziere Frequenz
