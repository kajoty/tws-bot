@echo off
REM TWS Complete System - Startet Aktien-Scanner, Options-Scanner und Web-Dashboard
REM Version 2.0 - Integriertes System

echo ========================================
echo  TWS COMPLETE SYSTEM
echo  Aktien + Options + Dashboard
echo ========================================
echo.

REM Prüfe Python-Umgebung
python --version >nul 2>&1
if errorlevel 1 (
    echo FEHLER: Python ist nicht installiert oder nicht im PATH.
    echo Bitte installieren Sie Python 3.8+ und fügen Sie es zum PATH hinzu.
    pause
    exit /b 1
)

cd /d "c:\Users\Karsten Jochens\Documents\VSCode_Projekte\tws-bot"

REM Aktiviere virtuelle Umgebung
echo Aktiviere virtuelle Umgebung...
call venv\Scripts\activate.bat

REM Erstelle Logs-Verzeichnis
if not exist logs mkdir logs

REM Starte Web-Dashboard im Hintergrund
echo Starte Web-Dashboard...
start "TWS-Web-Dashboard" cmd /c "python -m tws_bot.web.app > logs\web_app.log 2>&1"

REM Warte kurz für Initialisierung
timeout /t 3 /nobreak >nul

REM Starte Aktien-Signal-Service im Hintergrund
echo Starte Aktien-Signal-Service...
start "TWS-Aktien-Scanner" cmd /c "python -m tws_bot.signal_service > logs\signal_service.log 2>&1"

REM Starte Options-Scanner im Hintergrund
echo Starte Options-Scanner...
start "TWS-Options-Scanner" cmd /c "python options_scanner.py > logs\options_scanner.log 2>&1"

echo.
echo ========================================
echo  SYSTEM GESTARTET!
echo ========================================
echo.
echo Web-Dashboard:    http://127.0.0.1:5000
echo Aktien-Scanner:   Läuft im Hintergrund
echo Options-Scanner:  Läuft im Hintergrund
echo.
echo Logs finden Sie im Verzeichnis 'logs\'
echo.
echo Drücken Sie eine Taste, um alle Services zu stoppen...
pause >nul

REM Stoppe alle Services
echo Stoppe alle Services...
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im cmd.exe /fi "WINDOWTITLE eq TWS-*" >nul 2>&1

echo.
echo Alle Services gestoppt.
echo Auf Wiedersehen!
echo.
pause