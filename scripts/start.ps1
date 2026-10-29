param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$pythonPath = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Error 'Set up the environment first: uv sync --python 3.12 --extra dev --extra local'
}
& $pythonPath -m uvicorn app.main:app --host 127.0.0.1 --port $Port --workers 1 --log-level info
