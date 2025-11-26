@echo off
echo ========================================
echo   POSITION MONITOR SERVICE (Automatic)
echo ========================================
echo.

cd /d %~dp0
call venv\Scripts\activate.bat

python position_monitor_service.py

pause
