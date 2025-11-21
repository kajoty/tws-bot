#!/bin/bash
# Deinstallation des systemd Service

set -e

SERVICE_NAME="ib-trading-bot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}Deinstalliere Service '${SERVICE_NAME}'...${NC}"

# Prüfe sudo-Rechte
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}FEHLER: Benötigt sudo-Rechte!${NC}"
   exit 1
fi

# Prüfe ob Service existiert
if ! systemctl list-units --full -all | grep -Fq "${SERVICE_NAME}.service"; then
    echo -e "${YELLOW}Service ist nicht installiert.${NC}"
    exit 0
fi

# Stoppe Service falls läuft
if systemctl is-active --quiet ${SERVICE_NAME}; then
    echo -e "${YELLOW}Stoppe Service...${NC}"
    systemctl stop ${SERVICE_NAME}
fi

# Disable Autostart
echo -e "${YELLOW}Deaktiviere Autostart...${NC}"
systemctl disable ${SERVICE_NAME}

# Entferne Service-Datei
if [ -f "$SERVICE_FILE" ]; then
    echo -e "${YELLOW}Entferne Service-Datei...${NC}"
    rm "$SERVICE_FILE"
fi

# Reload systemd
systemctl daemon-reload
systemctl reset-failed

echo -e "${GREEN}✓ Service erfolgreich deinstalliert!${NC}"
