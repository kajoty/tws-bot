# IB Trading Bot - Windows Service Setup

## 🚀 Schnellstart

### 1. Dependencies installieren
```powershell
pip install -r requirements.txt
```

### 2. Als Windows-Dienst installieren (Administrator erforderlich)
```powershell
# PowerShell als Administrator öffnen
.\install_service.ps1
```

### 3. Dienst steuern
```powershell
# Starten
.\start_service.ps1

# Stoppen
.\stop_service.ps1

# Status anzeigen
.\status_service.ps1
```

### 4. Deinstallieren
```powershell
.\install_service.ps1 -Uninstall
```

## 📊 Überwachung

### Live-Logs anzeigen
```powershell
Get-Content logs\service.log -Tail 50 -Wait
```

### Web-Interface (parallel zum Service)
```powershell
python web_interface.py
# Dann Browser: http://localhost:5000
```

## 🧪 Testing ohne Service

Für Entwicklung und Debugging:
```powershell
.\run_console.ps1
# oder
python service_wrapper.py
```

## 📝 Notizen

- **Autostart**: Service startet automatisch beim Windows-Boot
- **Logs**: Alle Logs in `logs/service.log`
- **TWS Verbindung**: Stelle sicher dass TWS läuft bevor der Service startet
- **Konfiguration**: Alle Einstellungen in `config.py`

## 🔧 Troubleshooting

### Service startet nicht
1. Prüfe Logs: `Get-Content logs\service.log -Tail 50`
2. Stelle sicher TWS läuft und API aktiviert ist
3. Prüfe Port in `config.py` (7497 = Paper, 7496 = Live)

### "Service not found"
- Installiere erst mit `.\install_service.ps1`
- Benötigt Administrator-Rechte

### Firewall-Warnung
- Erlaube Python-Verbindung zu TWS (localhost:7497)

## ⚙️ Windows Services Manager

Service kann auch über `services.msc` verwaltet werden:
1. `Win+R` → `services.msc`
2. Suche "IB Trading Bot Service"
3. Rechtsklick → Start/Stop/Properties
