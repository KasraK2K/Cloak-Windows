param(
    [string]$Version = "0.1.0-alpha",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$AppDir = Join-Path $Root "windows-app"
$BinDir = Join-Path $AppDir "bin"
$VenvDir = Join-Path $Root ".venv"
$Python = Join-Path $VenvDir "Scripts\python.exe"
$BuildDir = Join-Path $Root "build"
$PyInstallerBuildDir = Join-Path $BuildDir "pyinstaller"
$DistDir = Join-Path $Root "dist"
$PackageName = "Cloak-Windows-v$Version"
$PackageDir = Join-Path $DistDir $PackageName
$ReleaseZip = Join-Path $DistDir "$PackageName.zip"

if (-not (Test-Path $Python)) {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw "Python was not found on PATH. Install Python 3.11+ first."
    }
    python -m venv $VenvDir
}

if (-not $SkipInstall) {
    & $Python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
    & $Python -m pip install -r (Join-Path $AppDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Installing application requirements failed." }
    & $Python -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "Installing PyInstaller failed." }
}

& $Python -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not available in .venv. Install it with: .\.venv\Scripts\python.exe -m pip install pyinstaller"
}

$RequiredRuntime = @(
    "xray.exe",
    "geoip.dat",
    "geosite.dat"
)

foreach ($name in $RequiredRuntime) {
    $path = Join-Path $BinDir $name
    if (-not (Test-Path $path)) {
        throw "$name is missing from windows-app\bin. Run scripts\setup_windows.ps1 -DownloadXray first."
    }
}

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
if (Test-Path $PackageDir) {
    Remove-Item -LiteralPath $PackageDir -Recurse -Force
}
if (Test-Path $ReleaseZip) {
    Remove-Item -LiteralPath $ReleaseZip -Force
}

$ListenerDist = Join-Path $PyInstallerBuildDir "listener-dist"
$GuiDist = Join-Path $PyInstallerBuildDir "gui-dist"
$ListenerWork = Join-Path $PyInstallerBuildDir "listener-work"
$GuiWork = Join-Path $PyInstallerBuildDir "gui-work"
$SpecDir = Join-Path $PyInstallerBuildDir "spec"

New-Item -ItemType Directory -Force -Path $ListenerDist, $GuiDist, $ListenerWork, $GuiWork, $SpecDir | Out-Null

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --name cloak-listener `
    --paths (Join-Path $AppDir "core") `
    --hidden-import pydivert `
    --collect-all pydivert `
    --distpath $ListenerDist `
    --workpath $ListenerWork `
    --specpath $SpecDir `
    (Join-Path $AppDir "core\main.py")
if ($LASTEXITCODE -ne 0) { throw "Building cloak-listener.exe failed." }

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name Cloak-Windows `
    --distpath $GuiDist `
    --workpath $GuiWork `
    --specpath $SpecDir `
    --add-data "$(Join-Path $AppDir 'assets\Cloak.png');assets" `
    --collect-data sv_ttk `
    (Join-Path $AppDir "cloak_windows.py")
if ($LASTEXITCODE -ne 0) { throw "Building Cloak-Windows.exe failed." }

New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $PackageDir "bin") | Out-Null

Copy-Item (Join-Path $GuiDist "Cloak-Windows\*") $PackageDir -Recurse -Force
Copy-Item (Join-Path $AppDir "assets") (Join-Path $PackageDir "assets") -Recurse -Force
Copy-Item (Join-Path $ListenerDist "cloak-listener.exe") (Join-Path $PackageDir "bin\cloak-listener.exe") -Force
Copy-Item (Join-Path $BinDir "xray.exe") (Join-Path $PackageDir "bin\xray.exe") -Force
Copy-Item (Join-Path $BinDir "geoip.dat") (Join-Path $PackageDir "bin\geoip.dat") -Force
Copy-Item (Join-Path $BinDir "geosite.dat") (Join-Path $PackageDir "bin\geosite.dat") -Force
Copy-Item (Join-Path $Root "README.md") (Join-Path $PackageDir "README.md") -Force
Copy-Item (Join-Path $Root "LICENSE") (Join-Path $PackageDir "LICENSE") -Force
Copy-Item (Join-Path $Root "NOTICE.md") (Join-Path $PackageDir "NOTICE.md") -Force

@"
@echo off
setlocal
pushd "%~dp0"
start "" "%~dp0Start-Cloak-Windows.vbs"
popd
"@ | Set-Content -Path (Join-Path $PackageDir "Start-Cloak-Windows.cmd") -Encoding ASCII

@"
Option Explicit
Dim fso
Dim shellApp
Dim root
Dim exePath
Set fso = CreateObject("Scripting.FileSystemObject")
Set shellApp = CreateObject("Shell.Application")
root = fso.GetParentFolderName(WScript.ScriptFullName)
exePath = fso.BuildPath(root, "Cloak-Windows.exe")
shellApp.ShellExecute exePath, "", root, "runas", 1
"@ | Set-Content -Path (Join-Path $PackageDir "Start-Cloak-Windows.vbs") -Encoding ASCII

Compress-Archive -Path (Join-Path $PackageDir "*") -DestinationPath $ReleaseZip -Force

Write-Host ""
Write-Host "Package created:"
Write-Host "  $ReleaseZip"
Write-Host ""
Write-Host "Test it by extracting the zip and double-clicking Start-Cloak-Windows.vbs."
