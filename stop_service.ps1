# Stoppt den IB Trading Bot Service

$serviceName = "IBTradingBot"

Write-Host "Stoppe Service '$serviceName'..." -ForegroundColor Yellow

$service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue

if (-not $service) {
    Write-Host "FEHLER: Service nicht installiert!" -ForegroundColor Red
    exit 1
}

if ($service.Status -eq 'Stopped') {
    Write-Host "Service ist bereits gestoppt!" -ForegroundColor Yellow
    exit 0
}

Stop-Service -Name $serviceName -Force

# Warte kurz
Start-Sleep -Seconds 2

$service = Get-Service -Name $serviceName
if ($service.Status -eq 'Stopped') {
    Write-Host "✓ Service erfolgreich gestoppt!" -ForegroundColor Green
} else {
    Write-Host "WARNUNG: Service Status: $($service.Status)" -ForegroundColor Yellow
}
