# Installation des IB Trading Bot als Windows-Dienst
# Erfordert Administrator-Rechte

param(
    [switch]$Uninstall
)

$serviceName = "IBTradingBot"
$displayName = "IB Trading Bot Service"
$description = "Automated trading bot for Interactive Brokers TWS"
$scriptPath = $PSScriptRoot
$pythonPath = (Get-Command python).Source
$mainScript = Join-Path $scriptPath "service_wrapper.py"
$logPath = Join-Path $scriptPath "logs"

# Prüfe Administrator-Rechte
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "FEHLER: Dieses Script benötigt Administrator-Rechte!" -ForegroundColor Red
    Write-Host "Bitte als Administrator ausführen." -ForegroundColor Yellow
    exit 1
}

if ($Uninstall) {
    Write-Host "Deinstalliere Dienst '$serviceName'..." -ForegroundColor Yellow
    
    # Stoppe Dienst falls läuft
    $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($service) {
        if ($service.Status -eq 'Running') {
            Write-Host "Stoppe Dienst..." -ForegroundColor Yellow
            Stop-Service -Name $serviceName -Force
            Start-Sleep -Seconds 2
        }
        
        # Entferne Dienst
        Write-Host "Entferne Dienst..." -ForegroundColor Yellow
        sc.exe delete $serviceName
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "✓ Dienst erfolgreich deinstalliert!" -ForegroundColor Green
        } else {
            Write-Host "FEHLER beim Deinstallieren!" -ForegroundColor Red
        }
    } else {
        Write-Host "Dienst ist nicht installiert." -ForegroundColor Yellow
    }
    
    exit 0
}

# Installation
Write-Host "="*60 -ForegroundColor Cyan
Write-Host " IB TRADING BOT - DIENST INSTALLATION" -ForegroundColor Cyan
Write-Host "="*60 -ForegroundColor Cyan

# Prüfe ob Python gefunden wurde
if (-not $pythonPath) {
    Write-Host "FEHLER: Python nicht gefunden!" -ForegroundColor Red
    Write-Host "Bitte Python installieren und zur PATH hinzufügen." -ForegroundColor Yellow
    exit 1
}

Write-Host "Python gefunden: $pythonPath" -ForegroundColor Green

# Prüfe ob service_wrapper.py existiert
if (-not (Test-Path $mainScript)) {
    Write-Host "FEHLER: service_wrapper.py nicht gefunden!" -ForegroundColor Red
    exit 1
}

# Installiere benötigte Python-Pakete
Write-Host "`nInstalliere Python-Pakete..." -ForegroundColor Yellow
& python -m pip install --quiet pywin32

# Prüfe ob Dienst bereits existiert
$existingService = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existingService) {
    Write-Host "`nDienst existiert bereits!" -ForegroundColor Yellow
    Write-Host "Zum Deinstallieren: .\install_service.ps1 -Uninstall" -ForegroundColor Yellow
    exit 1
}

# Erstelle Log-Verzeichnis
if (-not (Test-Path $logPath)) {
    New-Item -ItemType Directory -Path $logPath | Out-Null
}

# Installiere Dienst mit NSSM (empfohlen) oder Python Service
Write-Host "`nInstalliere Dienst..." -ForegroundColor Yellow

# Methode 1: Python Win32 Service
$installCmd = "python `"$mainScript`" install"
Write-Host "Führe aus: $installCmd" -ForegroundColor Gray
Invoke-Expression $installCmd

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ Dienst installiert!" -ForegroundColor Green
    
    # Konfiguriere Dienst
    Write-Host "`nKonfiguriere Dienst..." -ForegroundColor Yellow
    sc.exe config $serviceName start= auto
    sc.exe description $serviceName "$description"
    
    Write-Host "`n" + "="*60 -ForegroundColor Green
    Write-Host " INSTALLATION ERFOLGREICH!" -ForegroundColor Green
    Write-Host "="*60 -ForegroundColor Green
    Write-Host "`nDienst-Befehle:" -ForegroundColor Cyan
    Write-Host "  Start:   .\start_service.ps1" -ForegroundColor White
    Write-Host "  Stop:    .\stop_service.ps1" -ForegroundColor White
    Write-Host "  Status:  .\status_service.ps1" -ForegroundColor White
    Write-Host "  Logs:    Get-Content logs\service.log -Tail 50 -Wait" -ForegroundColor White
    Write-Host "`nOder über Services-Manager: services.msc" -ForegroundColor Gray
} else {
    Write-Host "FEHLER bei der Installation!" -ForegroundColor Red
    Write-Host "Versuche Alternative: NSSM (Non-Sucking Service Manager)" -ForegroundColor Yellow
    Write-Host "Download: https://nssm.cc/download" -ForegroundColor Gray
}
