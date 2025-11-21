# Führt den Bot im Konsolen-Modus aus (für Testing, ohne Service)
# Praktisch für Entwicklung und Debugging

Write-Host "="*60 -ForegroundColor Cyan
Write-Host " IB TRADING BOT - CONSOLE MODE" -ForegroundColor Cyan
Write-Host "="*60 -ForegroundColor Cyan
Write-Host " Drücke Ctrl+C zum Beenden" -ForegroundColor Yellow
Write-Host "="*60 -ForegroundColor Cyan
Write-Host ""

python service_wrapper.py
