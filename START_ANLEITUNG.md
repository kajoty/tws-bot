# TWS Signal Service - Startanleitung

## 🚀 Schnellstart

### Option 1: Paralleler Start (Empfohlen)
```batch
start_parallel.bat
```
Startet beide Dienste parallel im Hintergrund.

### Option 2: Schritt-für-Schritt Start
```batch
start_complete_system.bat
```
Startet Web-App zuerst, dann Signal-Service (blockierend).

## 🛑 System stoppen
```batch
stop_system.bat
```
Beendet alle Python-Prozesse sauber.

## 📊 Nach dem Start

- **Web-Dashboard:** http://localhost:5000
- **Logs:** `logs/signal_service.log`
- **Konfiguration:** `.env` Datei prüfen

## ⚙️ Voraussetzungen

- TWS/Gateway muss laufen
- Virtuelle Umgebung aktiviert
- `.env` Datei konfiguriert

## 🔧 Einzelne Komponenten

- `start_service.bat` - Nur Signal-Service
- `web_app.py` - Nur Web-App (manuell)

## 📝 Konfiguration

Bearbeite `.env` für:
- TWS-Verbindung (Port, Host)
- Pushover-Benachrichtigungen
- Trading-Parameter
- Watchlist-Symbole

## 🎯 **Erweiterte Indikatoren (NEU)**

### **VIX Filter (Marktrisiko)**
- **Was?** Verhindert Entries bei hoher Marktvolatilität
- **Warum hilfreich?** Reduziert Verluste in Crash-Situationen
- **Konfiguration:**
  ```bash
  USE_VIX_FILTER=True
  VIX_MAX_LEVEL=25.0      # Keine neuen Positionen über diesem Level
  VIX_HIGH_LEVEL=30.0     # Risiko halbiert bei hohem VIX
  ```

### **ATR (Average True Range)**
- **Was?** Misst die durchschnittliche Preisvolatilität
- **Warum hilfreich?** Dynamische Stop-Loss Levels basierend auf Volatilität
- **Konfiguration:**
  ```bash
  USE_ATR=True
  ATR_PERIOD=14
  ATR_MULTIPLIER=1.5      # Stop-Loss = ATR × 1.5
  ```

### **Bollinger Bands**
- **Was?** Zeigt überkaufte/überverkaufte Zonen
- **Warum hilfreich?** Zusätzliche Mean-Reversion Signale
- **Konfiguration:**
  ```bash
  USE_BB=True
  BB_PERIOD=20
  BB_STD_DEV=2.0
  ```

## 📊 **Empfohlene Konfiguration**

Für konservatives Trading mit erweiterten Indikatoren:
```bash
# Basis-Indikatoren
USE_MA_CROSSOVER=True
USE_RSI=True
USE_MACD=False

# Erweiterte Filter (empfohlen!)
USE_VIX_FILTER=True
USE_ATR=True
USE_BB=False

# Aggressive Einstellungen
MIN_SIGNALS_FOR_ENTRY=2
VIX_MAX_LEVEL=20.0
ATR_MULTIPLIER=2.0
```