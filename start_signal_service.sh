#!/bin/bash
# TWS Signal Service - Startup Script

echo "==================================="
echo "TWS Signal Service"
echo "==================================="
echo ""

# Check .env
if [ ! -f .env ]; then
    echo "❌ Fehler: .env Datei nicht gefunden!"
    echo "   Erstelle .env aus .env.example:"
    echo "   cp .env.example .env"
    exit 1
fi

# Aktiviere virtuelle Umgebung
if [ ! -d "venv" ]; then
    echo "📦 Erstelle virtuelle Umgebung..."
    python3 -m venv venv
    source venv/bin/activate
    echo "📥 Installiere Dependencies..."
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

# Check TWS connection (optional warning)
echo "⚠️  Stelle sicher dass TWS läuft und API aktiviert ist!"
echo ""

# Start service
echo "🚀 Starte Signal Service..."
python signal_service.py
