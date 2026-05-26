param(
    [switch]$DownloadXray
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$AppDir = Join-Path $Root "windows-app"
$BinDir = Join-Path $AppDir "bin"
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found on PATH. Install Python 3.11+ first."
}

if (-not (Test-Path $Python)) {
    python -m venv $Venv
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $AppDir "requirements.txt")

if ($DownloadXray) {
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $release = Invoke-RestMethod "https://api.github.com/repos/XTLS/Xray-core/releases/latest"
    $asset = $release.assets | Where-Object { $_.name -match "Xray-windows-64\.zip$" } | Select-Object -First 1
    if (-not $asset) {
        throw "Could not find Xray-windows-64.zip in the latest Xray release."
    }
    $TempDir = Join-Path ([IO.Path]::GetTempPath()) ("cloak-xray-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
    $ZipPath = Join-Path $TempDir $asset.name
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $ZipPath
    Expand-Archive -Path $ZipPath -DestinationPath $TempDir -Force
    $XrayExe = Get-ChildItem -Path $TempDir -Recurse -Filter "xray.exe" | Select-Object -First 1
    if (-not $XrayExe) {
        throw "Downloaded Xray archive did not contain xray.exe."
    }
    Copy-Item $XrayExe.FullName (Join-Path $BinDir "xray.exe") -Force
    Get-ChildItem -Path $TempDir -Recurse -Include "geoip.dat", "geosite.dat" | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $BinDir $_.Name) -Force
    }
    Remove-Item $TempDir -Recurse -Force
}

Write-Host ""
Write-Host "Setup complete."
Write-Host "Start the app as Administrator:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\start_windows_app.ps1 -AsAdmin"

