@echo off
echo ========================================
echo TWS Signal Service - System Stoppen
echo ========================================

echo Stoppe alle Python-Prozesse...
taskkill /f /im python.exe >nul 2>&1

echo Pruefe auf laufende Prozesse...
tasklist /fi "imagename eq python.exe" /nh | findstr /c:"python.exe" >nul
if %errorlevel% equ 0 (
    echo Einige Python-Prozesse laufen noch...
    timeout /t 2 /nobreak > nul
    taskkill /f /im python.exe >nul 2>&1
) else (
    echo Alle Python-Prozesse gestoppt.
)

echo System gestoppt.
pause