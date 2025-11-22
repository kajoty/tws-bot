#!/bin/bash
# Quick-Start Script für Docker Setup

set -e

echo "=========================================="
echo "IB Trading Bot - Docker Setup"
echo "=========================================="
echo ""

# Prüfe ob Docker läuft
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker läuft nicht. Bitte starte Docker Desktop."
    exit 1
fi

echo "✓ Docker läuft"

# Prüfe ob .env existiert
if [ ! -f .env ]; then
    echo ""
    echo "⚠️  .env Datei nicht gefunden!"
    echo ""
    read -p "Soll .env aus .env.example erstellt werden? (j/n): " create_env
    
    if [ "$create_env" = "j" ] || [ "$create_env" = "J" ]; then
        cp .env.example .env
        echo "✓ .env erstellt"
        echo ""
        echo "⚠️  WICHTIG: Bearbeite .env und trage deine IB Credentials ein!"
        echo ""
        read -p "Möchtest du .env jetzt bearbeiten? (j/n): " edit_env
        
        if [ "$edit_env" = "j" ] || [ "$edit_env" = "J" ]; then
            ${EDITOR:-nano} .env
        fi
    else
        echo "Abgebrochen. Erstelle .env manuell und starte dieses Script erneut."
        exit 1
    fi
fi

echo ""
echo "Konfiguration aus .env:"
echo "----------------------------------------"
grep -E "^(TWS_USERID|TRADING_MODE|IS_PAPER_TRADING|IB_PORT|WATCHLIST_STOCKS)=" .env | sed 's/^/  /'
echo "----------------------------------------"
echo ""

# Warne bei Default-Credentials
if grep -q "your_ib_username" .env; then
    echo "⚠️  WARNUNG: Standard-Credentials in .env erkannt!"
    echo "   Bitte trage deine echten IB Credentials ein."
    echo ""
    read -p "Trotzdem fortfahren? (j/n): " continue
    if [ "$continue" != "j" ] && [ "$continue" != "J" ]; then
        exit 1
    fi
fi

# Erstelle Verzeichnisse
echo "Erstelle Verzeichnisse..."
mkdir -p data logs plots
echo "✓ Verzeichnisse erstellt"

# Baue Container
echo ""
echo "Baue Trading Bot Container..."
docker-compose build --no-cache

echo ""
echo "✓ Container gebaut"
echo ""

# Starte Services
echo "Starte Services..."
docker-compose up -d

echo ""
echo "✓ Services gestartet"
echo ""

# Warte auf Gateway
echo "Warte auf IB Gateway (max 60s)..."
for i in {1..60}; do
    if docker exec ib-gateway nc -z localhost 4002 2>/dev/null; then
        echo "✓ Gateway bereit!"
        break
    fi
    
    if [ $i -eq 60 ]; then
        echo "⚠️  Gateway Timeout. Prüfe Credentials in .env"
        echo ""
        echo "Gateway Logs:"
        docker-compose logs ib-gateway | tail -20
        exit 1
    fi
    
    sleep 1
    echo -n "."
done

echo ""
echo ""
echo "=========================================="
echo "✓ Setup abgeschlossen!"
echo "=========================================="
echo ""
echo "Services:"
echo "  - IB Gateway:   läuft auf Port 4002 (Paper) / 4001 (Live)"
echo "  - Trading Bot:  verbunden mit Gateway"
echo "  - VNC:          localhost:5900 (optional)"
echo ""
echo "Nützliche Befehle:"
echo "  docker-compose logs -f           # Alle Logs live"
echo "  docker-compose logs -f trading-bot  # Nur Bot Logs"
echo "  docker-compose ps                # Status"
echo "  docker-compose down              # Stoppen"
echo "  docker-compose restart           # Neu starten"
echo ""
echo "Dateien:"
echo "  ./data/trading_data.db          # Datenbank"
echo "  ./logs/                         # Log-Dateien"
echo "  ./plots/                        # Performance Charts"
echo ""
echo "Weitere Infos: siehe DOCKER.md"
echo ""
