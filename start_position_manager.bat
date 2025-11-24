@echo off
echo ========================================
echo   POSITION MANAGER - Interactive CLI
echo ========================================
echo.

cd /d %~dp0
call venv\Scripts\activate.bat

python position_manager.py

pause
