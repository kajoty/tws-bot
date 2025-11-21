#!/bin/bash
# Startet den IB Trading Bot Service

SERVICE_NAME="ib-trading-bot"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}Starte Service '${SERVICE_NAME}'...${NC}"

if ! systemctl list-units --full -all | grep -Fq "${SERVICE_NAME}.service"; then
    echo -e "${RED}FEHLER: Service nicht installiert!${NC}"
    echo -e "${YELLOW}Installiere zuerst mit: sudo ./install_service.sh${NC}"
    exit 1
fi

if systemctl is-active --quiet ${SERVICE_NAME}; then
    echo -e "${GREEN}Service läuft bereits!${NC}"
    exit 0
fi

sudo systemctl start ${SERVICE_NAME}

# Warte kurz
sleep 2

if systemctl is-active --quiet ${SERVICE_NAME}; then
    echo -e "${GREEN}✓ Service erfolgreich gestartet!${NC}"
    echo -e "\n${YELLOW}Logs anzeigen: tail -f logs/service.log${NC}"
    echo -e "${YELLOW}oder: sudo journalctl -u ${SERVICE_NAME} -f${NC}"
else
    echo -e "${RED}FEHLER: Service konnte nicht gestartet werden!${NC}"
    echo -e "${YELLOW}Prüfe Logs: sudo journalctl -u ${SERVICE_NAME} -n 50${NC}"
fi
