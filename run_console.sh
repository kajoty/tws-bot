#!/bin/bash
# Führt den Bot im Konsolen-Modus aus (für Testing, ohne Service)

CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN} IB TRADING BOT - CONSOLE MODE${NC}"
echo -e "${CYAN}============================================================${NC}"
echo -e "${YELLOW} Drücke Ctrl+C zum Beenden${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""

# Prüfe ob venv existiert
if [ -d "venv" ]; then
    source venv/bin/activate
    python service_wrapper_linux.py
else
    python3 service_wrapper_linux.py
fi
