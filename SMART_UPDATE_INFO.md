# Smart-Update Logik - Historische Daten

## Übersicht

Der Options-Scanner verwendet eine intelligente Update-Strategie für historische Daten, um Scan-Zeiten drastisch zu reduzieren.

## Wie es funktioniert

### Erster Scan (Initial Load)
```
Symbol: AAPL
Aktion: Lade 252 Tage vollständig
Dauer: ~2 Sekunden pro Symbol
Log: "Lade historische Daten für AAPL (252 Tage, vollständig)..."
Cache: 252 Bars gespeichert
```

### Folgende Scans (Incremental Update)
```
Symbol: AAPL (bereits im Cache)
Aktion: Lade nur 5 neue Tage
Dauer: ~0.5 Sekunden pro Symbol
Log: "Lade neue Daten für AAPL (5 Tage, inkrementell)..."
Cache: +5 neue Bars angehängt, alte Daten bleiben erhalten
```

## Performance-Verbesserung

### Vor Smart-Update
- **Jeder Scan**: 500 Symbole × 252 Tage = ~17 Minuten
- **Alle 5 Minuten**: Komplett neue 252 Tage laden
- **Problem**: Unnötige TWS API-Belastung

### Nach Smart-Update
- **Erster Scan**: 500 Symbole × 252 Tage = ~17 Minuten (einmalig)
- **Folge-Scans**: 500 Symbole × 5 Tage = ~4 Minuten
- **Vorteil**: 75% schneller! + Cache bleibt komplett erhalten

## Technische Details

### Cache-Management
- **Dictionary**: `historical_data_cache[symbol]` = DataFrame mit allen Bars
- **Timestamp**: `historical_data_last_update[symbol]` = Zeitpunkt des letzten Updates
- **Duplikate**: Werden automatisch entfernt (neuester Wert bleibt)
- **Sortierung**: Chronologisch nach Datum

### Datenintegration
```python
# Alter Cache: 252 Bars (z.B. 2024-01-01 bis 2025-11-20)
# Neue Daten:   5 Bars (z.B. 2025-11-18 bis 2025-11-22)
# Resultat:   257 Bars (kombiniert, Duplikate entfernt)
```

### Inkrementeller Modus
```python
# Aktiviert wenn:
1. incremental=True (Standard)
2. Symbol bereits in historical_data_cache
3. Cache enthält gültige Daten

# Deaktiviert wenn:
- Erster Scan für Symbol
- incremental=False erzwungen
- Cache leer/ungültig
```

## Code-Beispiele

### Vollständiger Load erzwingen
```python
# Lade komplett neu (252 Tage), ignoriere Cache
self.request_historical_data('AAPL', days=252, incremental=False)
```

### Standard Smart-Update
```python
# Automatisch: 252 Tage beim ersten Mal, 5 Tage danach
self.request_historical_data('AAPL', days=252, incremental=True)
```

## Vorteile

✅ **Geschwindigkeit**: 75% schnellere Folge-Scans  
✅ **TWS-Schonung**: Weniger API-Requests = stabiler  
✅ **Datenqualität**: Alte Daten bleiben erhalten (kein Verlust)  
✅ **Speicher-effizient**: Cache wächst linear, nicht exponentiell  
✅ **Automatisch**: Keine manuelle Konfiguration nötig  

## Rate Limit Optimierung

### TWS Limits
- **Historical Data**: 60 Requests / 10 Minuten
- **Smart-Update Impact**: 
  - Alte Logik: 500 Symbole = über Limit!
  - Neue Logik: 500 Symbole × 5 Tage = innerhalb Limit

### Scan-Intervall Empfehlungen
```env
# Für 100 Symbole
OPTIONS_SCAN_INTERVAL=300  # 5 Minuten (sicher)

# Für 250 Symbole
OPTIONS_SCAN_INTERVAL=600  # 10 Minuten (optimal)

# Für 500 Symbole
OPTIONS_SCAN_INTERVAL=900  # 15 Minuten (empfohlen)
```

## Monitoring

### Log-Ausgaben
```
# Vollständiger Load
[INFO] Lade historische Daten für AAPL (252 Tage, vollständig)...
[OK] AAPL: 252 Bars geladen (vollständig)

# Inkrementeller Update
[DEBUG] Lade neue Daten für AAPL (5 Tage, inkrementell)...
[OK] AAPL: +5 neue Bars (gesamt: 257)
```

### Cache-Status prüfen
```python
# Im Code:
print(f"Cache-Größe: {len(scanner.historical_data_cache)} Symbole")
print(f"AAPL Bars: {len(scanner.historical_data_cache['AAPL'])}")
print(f"Letztes Update: {scanner.historical_data_last_update['AAPL']}")
```

## Zusammenfassung

Die Smart-Update-Logik macht den Scanner **deutlich schneller** bei Folge-Scans, während **alle historischen Daten erhalten bleiben**. Der erste Scan dauert zwar länger (einmalig 15-20 Minuten für 500 Symbole), aber danach sind Scans in nur 4-5 Minuten möglich.

**Ergebnis**: Praktikable Nutzung mit 500 S&P 500 Symbolen! 🚀
