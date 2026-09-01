$ErrorActionPreference = "Stop"

# Start the Wanderly model backend from Windows Terminal / PowerShell.
# Run from anywhere; this script finds the project root automatically.

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$VenvDir = Join-Path $ScriptDir ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"

Write-Host "[Windows] Project root : $ProjectRoot"
Write-Host "[Windows] Backend dir  : $ScriptDir"

if (-not (Test-Path $VenvDir)) {
    Write-Host "[Windows] Creating Windows venv at $VenvDir ..."
    python -m venv $VenvDir

    Write-Host "[Windows] Installing dependencies..."
    & $PythonExe -m pip install --upgrade pip --quiet
    & $PythonExe -m pip install -r (Join-Path $ScriptDir "requirements.txt") --quiet

}

$EnvFile = Join-Path $ScriptDir ".env"
if (Test-Path $EnvFile) {
    Write-Host "[Windows] Loading env from $EnvFile"
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            return
        }

        $parts = $line.Split("=", 2)
        $key = $parts[0].Trim()
        $value = $parts[1].Split("#", 2)[0].Trim()
        if ($key) {
            [Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
} else {
    Write-Host "[Windows] WARNING: $EnvFile not found. Using defaults."
}

if (-not $env:BACKEND_PRELOAD_MODEL) { $env:BACKEND_PRELOAD_MODEL = "fine_tuned" }
if (-not $env:MODEL_MAX_NEW_TOKENS) { $env:MODEL_MAX_NEW_TOKENS = "1200" }
if (-not $env:MODEL_MAX_INPUT_TOKENS) { $env:MODEL_MAX_INPUT_TOKENS = "2000" }
if (-not $env:MODEL_TEMPERATURE) { $env:MODEL_TEMPERATURE = "0.0" }

$HostValue = if ($env:BACKEND_HOST) { $env:BACKEND_HOST } else { "127.0.0.1" }
$PortValue = if ($env:BACKEND_PORT) { $env:BACKEND_PORT } else { "9000" }

Write-Host "[Windows] BACKEND_PRELOAD_MODEL : $env:BACKEND_PRELOAD_MODEL"
Write-Host "[Windows] MODEL_MAX_NEW_TOKENS  : $env:MODEL_MAX_NEW_TOKENS"
Write-Host "[Windows] Binding               : ${HostValue}:${PortValue}"
Write-Host ""

Set-Location $ProjectRoot
& $PythonExe -m uvicorn backend.app.main:app `
    --host $HostValue `
    --port $PortValue `
    --workers 1 `
    --log-level info
