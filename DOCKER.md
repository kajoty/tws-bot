# Docker Setup für IB Trading Bot

## Überblick

Der Trading Bot läuft jetzt in Docker mit IB Gateway statt TWS. Dies bietet folgende Vorteile:

- **Headless Betrieb**: IB Gateway läuft ohne GUI (VNC optional verfügbar)
- **Isolation**: Saubere Container-Umgebung
- **Portabilität**: Läuft überall wo Docker verfügbar ist
- **Automatische Restarts**: Container starten automatisch nach Problemen neu
- **Einfache Konfiguration**: Alle Einstellungen über `.env` Datei

## Voraussetzungen

- Docker und Docker Compose installiert
- Interactive Brokers Account (Paper oder Live)
- IB Gateway Credentials

## Schnellstart

### 1. Umgebungsvariablen einrichten

```bash
# .env Datei aus Beispiel erstellen
cp .env.example .env

# .env mit eigenen Credentials bearbeiten
nano .env
```

**Wichtig**: Trage deine IB Credentials ein:
```env
TWS_USERID=dein_ib_username
TWS_PASSWORD=dein_ib_passwort
TRADING_MODE=paper  # oder "live"
```

### 2. Container starten

```bash
# Alle Container im Hintergrund starten
docker-compose up -d

# Logs verfolgen
docker-compose logs -f

# Nur Trading Bot Logs
docker-compose logs -f trading-bot

# Nur IB Gateway Logs
docker-compose logs -f ib-gateway
```

### 3. Status prüfen

```bash
# Container Status
docker-compose ps

# Gateway Health Check
docker exec ib-gateway nc -z localhost 4002

# Bot Datenbankzugriff prüfen
docker exec trading-bot ls -la /app/data/
```

## Services

### IB Gateway (`ib-gateway`)

- **Image**: `ghcr.io/gnzsnz/ib-gateway:latest`
- **Ports**:
  - `4002`: Paper Trading API
  - `4001`: Live Trading API  
  - `5900`: VNC Server (optional)
- **Volumes**:
  - `ib-gateway-config`: Gateway Konfiguration
  - `ib-gateway-logs`: Gateway Logs

### Trading Bot (`trading-bot`)

- **Build**: Lokales Dockerfile
- **Verbindung**: Zu `ib-gateway` Container
- **Volumes**:
  - `./data`: SQLite Datenbank
  - `./logs`: Bot Logs
  - `./plots`: Performance Charts

## Konfiguration

### Ports

IB Gateway nutzt andere Ports als TWS:

| Modus          | TWS Port | Gateway Port |
|----------------|----------|--------------|
| Paper Trading  | 7497     | **4002**     |
| Live Trading   | 7496     | **4001**     |

Der Bot erkennt automatisch die richtigen Ports basierend auf `IS_PAPER_TRADING`.

### Umgebungsvariablen

Alle wichtigen Parameter können über `.env` gesteuert werden:

#### IB Gateway
```env
TWS_USERID=username         # IB Login
TWS_PASSWORD=password       # IB Passwort
TRADING_MODE=paper         # paper oder live
READ_ONLY_API=no           # API Read-Only?
VNC_PASSWORD=vncpass       # VNC Zugriff
```

#### Trading Bot
```env
IB_HOST=ib-gateway         # Gateway Container Name
IB_PORT=4002              # 4002=Paper, 4001=Live
IS_PAPER_TRADING=True     # Paper Modus?
DRY_RUN=False             # Simulation ohne Orders?
ACCOUNT_SIZE=100000.0     # Startkapital
MAX_RISK_PER_TRADE_PCT=0.01  # 1% Risiko/Trade
MAX_CONCURRENT_POSITIONS=5   # Max. Positionen
WATCHLIST_STOCKS=AAPL,MSFT,GOOGL  # Symbols
LOG_LEVEL=INFO            # Log-Level
```

## Befehle

### Container Management

```bash
# Container starten
docker-compose up -d

# Container stoppen
docker-compose down

# Container neu starten
docker-compose restart

# Container neu bauen
docker-compose build

# Alles neu starten (mit neuem Build)
docker-compose up -d --build

# Container löschen (inkl. Volumes!)
docker-compose down -v
```

### Logs & Debugging

```bash
# Alle Logs live
docker-compose logs -f

# Letzte 100 Zeilen
docker-compose logs --tail=100

# Container Shell
docker exec -it trading-bot /bin/bash
docker exec -it ib-gateway /bin/bash

# Python im Bot Container
docker exec -it trading-bot python
```

### Daten-Management

```bash
# Datenbank kopieren (Backup)
docker cp trading-bot:/app/data/trading_data.db ./backup_$(date +%Y%m%d).db

# Logs lokal ansehen
ls -la logs/
tail -f logs/trading_bot_*.log

# Performance Charts
ls -la plots/
```

## VNC Zugriff auf IB Gateway

Falls du die Gateway GUI sehen möchtest:

```bash
# VNC Client verbinden zu:
localhost:5900

# Passwort aus .env: VNC_PASSWORD
```

Empfohlene VNC Clients:
- **Linux**: Remmina, TigerVNC
- **macOS**: Screen Sharing, RealVNC
- **Windows**: TightVNC, RealVNC

## Troubleshooting

### Gateway startet nicht

```bash
# Gateway Logs prüfen
docker-compose logs ib-gateway

# Credentials in .env korrekt?
cat .env | grep TWS_

# Container neu starten
docker-compose restart ib-gateway
```

### Bot kann Gateway nicht erreichen

```bash
# Netzwerk prüfen
docker network ls
docker network inspect tws-bot_trading-network

# Gateway Port testen
docker exec trading-bot nc -zv ib-gateway 4002

# Host in .env korrekt?
# Sollte "ib-gateway" sein, nicht "localhost"!
```

### Container stoppt ständig

```bash
# Exit-Code prüfen
docker-compose ps

# Detaillierte Logs
docker-compose logs --tail=200 trading-bot

# Health Check Status
docker inspect trading-bot | grep Health -A 10
```

### Datenbank-Fehler

```bash
# Permissions prüfen
ls -la data/
sudo chown -R $USER:$USER data/

# Datenbank neu initialisieren (VORSICHT: Löscht Daten!)
rm data/trading_data.db
docker-compose restart trading-bot
```

## Performance-Optimierung

### Ressourcen limitieren

In `docker-compose.yml` hinzufügen:

```yaml
services:
  trading-bot:
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 512M
        reservations:
          cpus: '0.5'
          memory: 256M
```

### Log-Rotation

```bash
# Docker Log-Rotation aktivieren
# In /etc/docker/daemon.json:
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
```

## Lokaler Betrieb (ohne Docker)

Falls du lokal entwickeln möchtest:

```bash
# IB Gateway separat starten (oder TWS)
# ...

# .env für lokalen Betrieb anpassen
IB_HOST=localhost
IB_PORT=4002  # oder 7497 für TWS

# Bot normal starten
python main.py
```

## Backup-Strategie

```bash
# Backup-Script erstellen
cat > backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR=backups
mkdir -p $BACKUP_DIR
DATE=$(date +%Y%m%d_%H%M%S)

# Datenbank sichern
docker cp trading-bot:/app/data/trading_data.db \
  $BACKUP_DIR/trading_data_$DATE.db

# Logs sichern
tar -czf $BACKUP_DIR/logs_$DATE.tar.gz logs/

# Alte Backups löschen (älter als 30 Tage)
find $BACKUP_DIR -name "*.db" -mtime +30 -delete
find $BACKUP_DIR -name "*.tar.gz" -mtime +30 -delete

echo "Backup abgeschlossen: $DATE"
EOF

chmod +x backup.sh

# Cronjob für tägliches Backup (z.B. 2:00 Uhr)
# crontab -e
# 0 2 * * * /pfad/zu/backup.sh
```

## Sicherheitshinweise

1. **.env Datei schützen**:
   ```bash
   chmod 600 .env
   # .env ist bereits in .gitignore
   ```

2. **Niemals Credentials committen**:
   - Nutze immer `.env` für sensible Daten
   - Prüfe mit `git diff` vor jedem Commit

3. **Read-Only API** für Tests:
   ```env
   READ_ONLY_API=yes  # In .env
   ```

4. **DRY_RUN** für Strategie-Tests:
   ```env
   DRY_RUN=True  # Keine echten Orders
   ```

## Migration von TWS zu Gateway

Falls du von TWS umsteigst:

1. **Ports ändern**: TWS 7497/7496 → Gateway 4002/4001
2. **Docker Setup**: `docker-compose up -d`
3. **Credentials**: `.env` mit IB Daten füllen
4. **Testen**: Erst mit `DRY_RUN=True`
5. **Monitoring**: Logs beobachten
6. **Produktiv**: `DRY_RUN=False` setzen

Die komplette Konfiguration ist jetzt in `.env` und `docker-compose.yml` - kein manuelles Editieren von `config.py` mehr nötig!
