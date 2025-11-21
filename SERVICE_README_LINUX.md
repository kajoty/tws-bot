# IB Trading Bot - Linux (Ubuntu Server) Service Setup

## 🚀 Schnellstart

### 1. Repository klonen & Dependencies installieren
```bash
cd /opt  # oder beliebiger Pfad
git clone <repository-url> tws-bot
cd tws-bot

# Virtual Environment (empfohlen)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. TWS Gateway auf Server installieren
```bash
# IB Gateway für Linux herunterladen
wget https://download2.interactivebrokers.com/installers/ibgateway/latest-standalone/ibgateway-latest-standalone-linux-x64.sh

# Installieren
chmod +x ibgateway-latest-standalone-linux-x64.sh
sudo ./ibgateway-latest-standalone-linux-x64.sh

# Konfiguriere TWS Gateway:
# - Aktiviere API: Configure → Settings → API → Enable ActiveX and Socket Clients
# - Port: 7497 (Paper) oder 7496 (Live)
# - Trusted IPs: 127.0.0.1
```

### 3. Als systemd Service installieren
```bash
# Service installieren (benötigt sudo)
sudo ./install_service.sh
```

### 4. Service steuern
```bash
# Starten
sudo systemctl start ib-trading-bot
# oder:
./start_service.sh

# Stoppen
sudo systemctl stop ib-trading-bot
# oder:
./stop_service.sh

# Status
sudo systemctl status ib-trading-bot
# oder:
./status_service.sh

# Logs
tail -f logs/service.log
# oder:
sudo journalctl -u ib-trading-bot -f
```

### 5. Deinstallieren
```bash
sudo ./uninstall_service.sh
```

## 📊 Überwachung

### Logs live anzeigen
```bash
# Eigene Logs
tail -f logs/service.log

# systemd Journal
sudo journalctl -u ib-trading-bot -f

# Letzte 100 Zeilen
sudo journalctl -u ib-trading-bot -n 100
```

### Web-Interface (parallel zum Service)
```bash
# In separater Terminal-Session
source venv/bin/activate
python web_interface.py

# Dann Browser: http://<server-ip>:5000
```

## 🧪 Testing ohne Service

Für Entwicklung und Debugging:
```bash
./run_console.sh
# oder
source venv/bin/activate
python service_wrapper_linux.py
```

## 📝 Systemd Service Details

**Service-Datei**: `/etc/systemd/system/ib-trading-bot.service`

**Features:**
- ✅ Autostart beim Boot (`systemctl enable`)
- ✅ Auto-Restart bei Crash (`Restart=always`)
- ✅ Läuft als normaler User (kein root)
- ✅ Resource Limits (2GB RAM, 100% CPU)
- ✅ Logs in `logs/service.log`

**Nützliche Befehle:**
```bash
# Service neu laden nach Config-Änderungen
sudo systemctl daemon-reload

# Autostart aktivieren/deaktivieren
sudo systemctl enable ib-trading-bot
sudo systemctl disable ib-trading-bot

# Service Status (detailliert)
systemctl status ib-trading-bot

# Fehlerhafte Services zurücksetzen
sudo systemctl reset-failed
```

## 🔧 Troubleshooting

### Service startet nicht
```bash
# Prüfe Logs
sudo journalctl -u ib-trading-bot -n 50

# Prüfe Service-Datei
sudo systemctl cat ib-trading-bot

# Teste manuell
./run_console.sh
```

### TWS Gateway Verbindung fehlschlägt
```bash
# Prüfe ob Gateway läuft
ps aux | grep gateway

# Prüfe Port
sudo netstat -tulpn | grep 7497

# Teste Verbindung
telnet localhost 7497
```

### Permission Errors
```bash
# Log-Verzeichnis Rechte
sudo chown -R $USER:$USER logs/
chmod 755 logs/

# Script Rechte
chmod +x *.sh
```

### Service läuft aber tradet nicht
1. Prüfe TWS Gateway ist angemeldet
2. Prüfe `config.py` Einstellungen
3. Prüfe Handelszeiten (US Markets)
4. Prüfe Logs auf Fehler

## 🐳 Docker Alternative

Für containerisierte Deployment siehe `Dockerfile` (optional):
```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["python", "service_wrapper_linux.py"]
```

## 📦 Systemanforderungen

- **OS**: Ubuntu 20.04+ (oder Debian-basiert)
- **Python**: 3.9+
- **RAM**: Min 512MB, empfohlen 2GB
- **CPU**: 1 Core ausreichend
- **Disk**: ~500MB für Dependencies + Logs

## 🔐 Sicherheit

### Firewall (wenn Web-Interface öffentlich)
```bash
# Nur lokal (empfohlen)
python web_interface.py --host 127.0.0.1

# Öffentlich (mit Firewall)
sudo ufw allow 5000/tcp
sudo ufw enable
```

### SSH Tunnel für Web-Interface
```bash
# Von lokalem Rechner:
ssh -L 5000:localhost:5000 user@server-ip

# Dann Browser: http://localhost:5000
```

## ⚙️ Automatische Backups

```bash
# Cron Job für tägliche DB-Backups
crontab -e

# Füge hinzu:
0 2 * * * cd /opt/tws-bot && tar -czf backups/db_$(date +\%Y\%m\%d).tar.gz data/
```

## 📞 Support

Bei Problemen:
1. Prüfe Logs: `sudo journalctl -u ib-trading-bot -n 100`
2. Teste manuell: `./run_console.sh`
3. Prüfe TWS Gateway Verbindung
4. Prüfe `config.py` Einstellungen
