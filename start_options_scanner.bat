@echo off
REM Startet den Options-Scanner für TWS

echo.
echo =====================================================================
echo   TWS OPTIONS-SCANNER
echo   Konträre 52-Wochen-Extrem-Strategie
echo =====================================================================
echo.

REM Aktiviere virtuelle Umgebung
call venv\Scripts\activate.bat

REM Starte Options-Scanner
python options_scanner.py

pause
