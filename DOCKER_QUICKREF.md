# Docker Quick Reference

## Start/Stop

```bash
# Alles starten (empfohlen: nutze start-docker.sh)
docker-compose up -d

# Stoppen
docker-compose down

# Neu starten
docker-compose restart

# Neu bauen und starten
docker-compose up -d --build
```

## Logs

```bash
# Alle Logs live
docker-compose logs -f

# Nur Bot
docker-compose logs -f trading-bot

# Nur Gateway
docker-compose logs -f ib-gateway

# Letzte 100 Zeilen
docker-compose logs --tail=100
```

## Status & Debug

```bash
# Container Status
docker-compose ps

# Shell im Bot
docker exec -it trading-bot /bin/bash

# Python im Bot
docker exec -it trading-bot python

# Gateway Port testen
docker exec trading-bot nc -zv ib-gateway 4002
```

## Daten

```bash
# Datenbank Backup
docker cp trading-bot:/app/data/trading_data.db ./backup.db

# Logs exportieren
docker cp trading-bot:/app/logs ./logs_backup

# Plots ansehen
ls -la plots/
```

## Configuration

Alle Einstellungen in `.env`:

```env
# IB Credentials
TWS_USERID=username
TWS_PASSWORD=password
TRADING_MODE=paper

# Bot Settings
IB_HOST=ib-gateway
IB_PORT=4002
IS_PAPER_TRADING=True
DRY_RUN=False
WATCHLIST_STOCKS=AAPL,MSFT,GOOGL
```

Nach Änderungen an `.env`:
```bash
docker-compose down
docker-compose up -d
```

## VNC Zugriff

IB Gateway GUI per VNC:
```bash
# VNC Client verbinden zu:
localhost:5900

# Passwort: VNC_PASSWORD aus .env
```

## Troubleshooting

```bash
# Gateway startet nicht?
docker-compose logs ib-gateway
# → Prüfe Credentials in .env

# Bot kann Gateway nicht erreichen?
docker exec trading-bot nc -zv ib-gateway 4002
# → Muss "succeeded" zeigen

# Container crashed?
docker-compose ps
docker-compose logs --tail=50 trading-bot

# Alles neu starten
docker-compose down
docker-compose up -d --build
```

Mehr Details: siehe **DOCKER.md**
