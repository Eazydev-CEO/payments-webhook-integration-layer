<#
.SYNOPSIS
    Pick a random free high host port, store it in .env as APP_PORT, and
    launch the stack with Docker Compose. Never stops or kills any process.

.DESCRIPTION
    - Chooses a random port in [10000, 60000].
    - Confirms the port is free (Get-NetTCPConnection + a real TcpListener bind).
    - Writes/updates APP_PORT in .env.
    - Runs `docker compose up --build -d`.
    - Prints the final URL.

.NOTES
    This script is read-only with respect to OTHER processes: it only ever
    *tests* ports by attempting a listener bind that it immediately releases.
    It will never call Stop-Process, taskkill, or free a busy port.
#>

[CmdletBinding()]
param(
    [int]$MinPort = 10000,
    [int]$MaxPort = 60000,
    [switch]$Detached
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot ".env"

function Test-PortFree {
    param([int]$Port)
    # 1) Is anything already connected/listening on it?
    $conn = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    if ($conn) { return $false }
    # 2) Can we actually bind a listener? (definitive free check)
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        $listener.Stop()
        return $true
    } catch {
        return $false
    }
}

Write-Host "==> Selecting a random free high port between $MinPort and $MaxPort..." -ForegroundColor Cyan

$rand = New-Object System.Random
$selectedPort = $null
for ($i = 0; $i -lt 100; $i++) {
    $candidate = $rand.Next($MinPort, $MaxPort)
    if (Test-PortFree -Port $candidate) {
        $selectedPort = $candidate
        break
    }
}

if (-not $selectedPort) {
    Write-Error "Could not find a free port after 100 attempts. Try again."
    exit 1
}

Write-Host "==> Confirmed free port: $selectedPort" -ForegroundColor Green

# ---- Update APP_PORT in .env (create from example if missing) -----------
if (-not (Test-Path $EnvFile)) {
    $example = Join-Path $ProjectRoot ".env.example"
    if (Test-Path $example) {
        Copy-Item $example $EnvFile
        Write-Host "==> Created .env from .env.example" -ForegroundColor Yellow
    } else {
        New-Item -ItemType File -Path $EnvFile | Out-Null
    }
}

$lines = Get-Content $EnvFile
if ($lines -match '^APP_PORT=') {
    $lines = $lines -replace '^APP_PORT=.*', "APP_PORT=$selectedPort"
} else {
    $lines += "APP_PORT=$selectedPort"
}
# Keep CSRF trusted origins aligned with the chosen port for convenience.
if ($lines -match '^CSRF_TRUSTED_ORIGINS=') {
    $lines = $lines -replace '^CSRF_TRUSTED_ORIGINS=.*', "CSRF_TRUSTED_ORIGINS=http://localhost:$selectedPort,http://127.0.0.1:$selectedPort"
}
Set-Content -Path $EnvFile -Value $lines -Encoding utf8
Write-Host "==> Wrote APP_PORT=$selectedPort to .env" -ForegroundColor Green

# ---- Launch Docker Compose ----------------------------------------------
Write-Host "==> Building and starting Docker Compose..." -ForegroundColor Cyan
Push-Location $ProjectRoot
try {
    if ($Detached) {
        docker compose up --build -d
    } else {
        docker compose up --build -d
    }
} finally {
    Pop-Location
}

$url = "http://localhost:$selectedPort/"
Write-Host ""
Write-Host "=========================================================" -ForegroundColor Green
Write-Host "  Payments & Webhook Integration Layer is starting up." -ForegroundColor Green
Write-Host "  URL:        $url" -ForegroundColor Green
Write-Host "  Dashboard:  ${url}dashboard/" -ForegroundColor Green
Write-Host "  API docs:   ${url}api/docs/" -ForegroundColor Green
Write-Host "  Login with the ADMIN_USERNAME / ADMIN_PASSWORD from .env" -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Green
