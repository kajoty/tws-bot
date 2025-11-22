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

# Check TWS connection (optional warning)
echo "⚠️  Stelle sicher dass TWS läuft und API aktiviert ist!"
echo ""

# Start service
echo "🚀 Starte Signal Service..."
python3 signal_service.py
