@echo off
echo ========================================
echo TWS Signal Service - Paralleler Start
echo ========================================
cd /d "c:\Users\Karsten Jochens\Documents\VSCode_Projekte\tws-bot"

echo Aktiviere virtuelle Umgebung...
call venv\Scripts\activate.bat

echo Starte Web-App im Hintergrund...
start "TWS-WebApp" /b python web_app.py

echo Starte Signal-Service im Hintergrund...
start "TWS-SignalService" /b python signal_service.py

echo.
echo System gestartet!
echo - Web-App: http://localhost:5000
echo - Logs: logs\signal_service.log
echo.
echo Zum Stoppen: stop_system.bat ausfuehren
echo.
pause