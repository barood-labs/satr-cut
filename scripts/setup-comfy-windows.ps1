# Satr Cut on a Windows ComfyUI host.
# Installs the CLI, checks ffmpeg, and writes a start script.
# It does not download gated weights and it does not read or store API keys.
# Usage (PowerShell):
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\scripts\setup-comfy-windows.ps1 -ComfyRoot D:\ComfyUI\ComfyUI

param(
    [string]$ComfyRoot = $env:SATRCUT_COMFY_ROOT,
    [int]$Port = 8188
)

$ErrorActionPreference = "Stop"

function Need($name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Missing $name on PATH. Install it, then run this script again."
    }
}

Need python
Need pip
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning "ffmpeg is not on PATH. satrcut assemble needs it. Install ffmpeg and reopen the shell."
}

python -m pip install --upgrade satrcut

if (-not $ComfyRoot) {
    $ComfyRoot = Read-Host "Path to the ComfyUI folder that contains main.py"
}
if (-not (Test-Path (Join-Path $ComfyRoot "main.py"))) {
    throw "No main.py in $ComfyRoot. Point -ComfyRoot at the ComfyUI checkout, not a parent folder."
}

$envFile = Join-Path (Get-Location) "satrcut-comfy.cmd"
@"
@echo off
set SATRCUT_COMFY_ROOT=$ComfyRoot
set SATRCUT_COMFY_URL=http://127.0.0.1:$Port
set SATRCUT_PROJECT=%CD%
echo SATRCUT_COMFY_ROOT=%SATRCUT_COMFY_ROOT%
echo Starting ComfyUI on $Port. Leave this window open.
cd /d "$ComfyRoot"
python main.py --listen 127.0.0.1 --port $Port --disable-auto-launch
"@ | Set-Content -Encoding ASCII $envFile

Write-Host ""
Write-Host "Wrote $envFile"
Write-Host "Weights stay on this machine. satrcut models prints the public filenames. Nothing is downloaded."
Write-Host ""
Write-Host "Then, in the series folder:"
Write-Host "  satrcut plan generate -e 1"
Write-Host "  satrcut lint -e 1"
Write-Host "  satrcut render -e 1 --provider comfyui --max-tries 4"
Write-Host "  satrcut assemble -e 1"
Write-Host "  satrcut service install -e 1 --provider comfyui"
Write-Host "  schtasks /Create /TN ""SatrCut Render"" /XML ""%CD%\deploy\satrcut-render.xml"" /F"
