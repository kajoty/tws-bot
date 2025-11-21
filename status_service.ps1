# Zeigt Status des IB Trading Bot Service

$serviceName = "IBTradingBot"

Write-Host "="*60 -ForegroundColor Cyan
Write-Host " IB TRADING BOT - SERVICE STATUS" -ForegroundColor Cyan
Write-Host "="*60 -ForegroundColor Cyan

$service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue

if (-not $service) {
    Write-Host "`nService ist NICHT installiert!" -ForegroundColor Red
    Write-Host "`nInstalliere mit: .\install_service.ps1" -ForegroundColor Yellow
    exit 1
}

Write-Host "`nService Name:    $($service.Name)" -ForegroundColor White
Write-Host "Display Name:    $($service.DisplayName)" -ForegroundColor White
Write-Host "Status:          " -NoNewline

switch ($service.Status) {
    'Running' { Write-Host "RUNNING" -ForegroundColor Green }
    'Stopped' { Write-Host "STOPPED" -ForegroundColor Red }
    default { Write-Host $service.Status -ForegroundColor Yellow }
}

Write-Host "Start Type:      $($service.StartType)" -ForegroundColor White

# Zeige letzte Log-Einträge
Write-Host "`n" + "-"*60 -ForegroundColor Gray
Write-Host " LETZTE LOG-EINTRÄGE (service.log)" -ForegroundColor Cyan
Write-Host "-"*60 -ForegroundColor Gray

$logFile = "logs\service.log"
if (Test-Path $logFile) {
    Get-Content $logFile -Tail 20 | ForEach-Object { Write-Host $_ -ForegroundColor Gray }
    
    Write-Host "`n" + "-"*60 -ForegroundColor Gray
    Write-Host "Live-Logs: Get-Content logs\service.log -Tail 50 -Wait" -ForegroundColor Gray
} else {
    Write-Host "Keine Logs gefunden." -ForegroundColor Yellow
}

Write-Host "`n" + "="*60 -ForegroundColor Cyan
