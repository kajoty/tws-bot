# Startet den IB Trading Bot Service

$serviceName = "IBTradingBot"

Write-Host "Starte Service '$serviceName'..." -ForegroundColor Yellow

$service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue

if (-not $service) {
    Write-Host "FEHLER: Service nicht installiert!" -ForegroundColor Red
    Write-Host "Installiere zuerst mit: .\install_service.ps1" -ForegroundColor Yellow
    exit 1
}

if ($service.Status -eq 'Running') {
    Write-Host "Service läuft bereits!" -ForegroundColor Green
    exit 0
}

Start-Service -Name $serviceName

# Warte kurz
Start-Sleep -Seconds 2

$service = Get-Service -Name $serviceName
if ($service.Status -eq 'Running') {
    Write-Host "✓ Service erfolgreich gestartet!" -ForegroundColor Green
    Write-Host "`nLogs anzeigen: Get-Content logs\service.log -Tail 50 -Wait" -ForegroundColor Gray
} else {
    Write-Host "FEHLER: Service konnte nicht gestartet werden!" -ForegroundColor Red
    Write-Host "Status: $($service.Status)" -ForegroundColor Yellow
    Write-Host "`nPrüfe Logs: Get-Content logs\service.log -Tail 50" -ForegroundColor Gray
}
