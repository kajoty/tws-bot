#!/bin/bash
# Installation des IB Trading Bot als systemd Service
# Erfordert sudo-Rechte

set -e

SERVICE_NAME="ib-trading-bot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER=$(whoami)
PYTHON_PATH=$(which python3)
VENV_PATH="${SCRIPT_DIR}/venv"

# Farben für Output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN} IB TRADING BOT - SYSTEMD SERVICE INSTALLATION${NC}"
echo -e "${CYAN}============================================================${NC}"

# Prüfe sudo-Rechte
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}FEHLER: Dieses Script benötigt sudo-Rechte!${NC}"
   echo -e "${YELLOW}Führe aus: sudo ./install_service.sh${NC}"
   exit 1
fi

# Prüfe ob Service bereits existiert
if systemctl list-units --full -all | grep -Fq "${SERVICE_NAME}.service"; then
    echo -e "${YELLOW}Service existiert bereits!${NC}"
    echo -e "Zum Deinstallieren: ${YELLOW}sudo ./uninstall_service.sh${NC}"
    exit 1
fi

echo -e "\n${GREEN}Python gefunden: ${PYTHON_PATH}${NC}"
echo -e "${GREEN}Script-Verzeichnis: ${SCRIPT_DIR}${NC}"
echo -e "${GREEN}User: ${USER}${NC}"

# Erstelle/Aktiviere Virtual Environment (empfohlen)
echo -e "\n${YELLOW}Prüfe Virtual Environment...${NC}"
if [ ! -d "$VENV_PATH" ]; then
    echo -e "${YELLOW}Erstelle Virtual Environment...${NC}"
    sudo -u $USER python3 -m venv "$VENV_PATH"
    sudo -u $USER "$VENV_PATH/bin/pip" install --upgrade pip
fi

# Installiere Dependencies
echo -e "${YELLOW}Installiere Python-Pakete...${NC}"
sudo -u $USER "$VENV_PATH/bin/pip" install -q -r "${SCRIPT_DIR}/requirements.txt"

# Erstelle systemd Service-Datei
echo -e "\n${YELLOW}Erstelle systemd Service...${NC}"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=IB Trading Bot Service
After=network.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${SCRIPT_DIR}
Environment="PATH=${VENV_PATH}/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=${VENV_PATH}/bin/python ${SCRIPT_DIR}/service_wrapper_linux.py
Restart=always
RestartSec=10
StandardOutput=append:${SCRIPT_DIR}/logs/service.log
StandardError=append:${SCRIPT_DIR}/logs/service.log

# Resource Limits (optional)
MemoryLimit=2G
CPUQuota=100%

[Install]
WantedBy=multi-user.target
EOF

echo -e "${GREEN}✓ Service-Datei erstellt: ${SERVICE_FILE}${NC}"

# Erstelle Log-Verzeichnis
mkdir -p "${SCRIPT_DIR}/logs"
chown -R $USER:$USER "${SCRIPT_DIR}/logs"

# Reload systemd
echo -e "\n${YELLOW}Lade systemd-Konfiguration neu...${NC}"
systemctl daemon-reload

# Enable Service (Autostart)
echo -e "${YELLOW}Aktiviere Autostart...${NC}"
systemctl enable ${SERVICE_NAME}

echo -e "\n${GREEN}============================================================${NC}"
echo -e "${GREEN} INSTALLATION ERFOLGREICH!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo -e "\n${CYAN}Service-Befehle:${NC}"
echo -e "  Start:   ${YELLOW}sudo systemctl start ${SERVICE_NAME}${NC}"
echo -e "  Stop:    ${YELLOW}sudo systemctl stop ${SERVICE_NAME}${NC}"
echo -e "  Status:  ${YELLOW}sudo systemctl status ${SERVICE_NAME}${NC}"
echo -e "  Logs:    ${YELLOW}sudo journalctl -u ${SERVICE_NAME} -f${NC}"
echo -e "  oder:    ${YELLOW}tail -f logs/service.log${NC}"
echo -e "\n${CYAN}Skripte:${NC}"
echo -e "  ${YELLOW}./start_service.sh${NC}"
echo -e "  ${YELLOW}./stop_service.sh${NC}"
echo -e "  ${YELLOW}./status_service.sh${NC}"
echo -e "\n${GREEN}Autostart beim Boot: AKTIVIERT${NC}"
echo -e "${GREEN}============================================================${NC}"
