#!/bin/bash
# Zeigt Status des IB Trading Bot Service

SERVICE_NAME="ib-trading-bot"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
GRAY='\033[0;90m'
NC='\033[0m'

echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN} IB TRADING BOT - SERVICE STATUS${NC}"
echo -e "${CYAN}============================================================${NC}"

if ! systemctl list-units --full -all | grep -Fq "${SERVICE_NAME}.service"; then
    echo -e "\n${RED}Service ist NICHT installiert!${NC}"
    echo -e "\n${YELLOW}Installiere mit: sudo ./install_service.sh${NC}"
    exit 1
fi

# Status anzeigen
echo ""
sudo systemctl status ${SERVICE_NAME} --no-pager

# Zeige letzte Log-Einträge
echo -e "\n${GRAY}------------------------------------------------------------${NC}"
echo -e "${CYAN} LETZTE LOG-EINTRÄGE (service.log)${NC}"
echo -e "${GRAY}------------------------------------------------------------${NC}"

if [ -f "logs/service.log" ]; then
    tail -n 20 logs/service.log | while IFS= read -r line; do
        echo -e "${GRAY}${line}${NC}"
    done
    
    echo -e "\n${GRAY}------------------------------------------------------------${NC}"
    echo -e "${GRAY}Live-Logs: tail -f logs/service.log${NC}"
    echo -e "${GRAY}oder: sudo journalctl -u ${SERVICE_NAME} -f${NC}"
else
    echo -e "${YELLOW}Keine Logs gefunden.${NC}"
fi

echo -e "\n${CYAN}============================================================${NC}"
