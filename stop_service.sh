#!/bin/bash
# Stoppt den IB Trading Bot Service

SERVICE_NAME="ib-trading-bot"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}Stoppe Service '${SERVICE_NAME}'...${NC}"

if ! systemctl list-units --full -all | grep -Fq "${SERVICE_NAME}.service"; then
    echo -e "${RED}FEHLER: Service nicht installiert!${NC}"
    exit 1
fi

if ! systemctl is-active --quiet ${SERVICE_NAME}; then
    echo -e "${YELLOW}Service ist bereits gestoppt!${NC}"
    exit 0
fi

sudo systemctl stop ${SERVICE_NAME}

# Warte kurz
sleep 2

if ! systemctl is-active --quiet ${SERVICE_NAME}; then
    echo -e "${GREEN}✓ Service erfolgreich gestoppt!${NC}"
else
    echo -e "${YELLOW}WARNUNG: Service läuft noch...${NC}"
fi
