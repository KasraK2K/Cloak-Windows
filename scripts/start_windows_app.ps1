param(
    [switch]$AsAdmin
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$AppDir = Join-Path $Root "windows-app"
$App = Join-Path $AppDir "cloak_windows.py"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$SystemPython = (Get-Command python -ErrorAction Stop).Source
$Python = $SystemPython
if (Test-Path $VenvPython) {
    $env:CLOAK_LISTENER_PYTHON = $VenvPython
}

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if ($AsAdmin -and -not (Test-IsAdmin)) {
    Start-Process -FilePath "powershell" -ArgumentList @(
        "-NoProfile",
        "-WindowStyle",
        "Hidden",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "`"$PSCommandPath`"",
        "-AsAdmin"
    ) -WorkingDirectory $Root -Verb RunAs -WindowStyle Hidden
    exit
}

Push-Location $AppDir
try {
    & $Python $App
}
finally {
    Pop-Location
}
