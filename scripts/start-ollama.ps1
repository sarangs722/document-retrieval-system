param([string]$Model = 'qwen3:1.7b', [switch]$PullModel)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$projectRoot = (Get-Location).Path
$ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaCommand) { throw 'Install Ollama first: https://ollama.com/download' }
$running = $false
try {
    $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2
    $running = $true
} catch { }
if (-not $running) {
    $env:OLLAMA_HOST = '127.0.0.1:11434'
    $env:OLLAMA_MODELS = Join-Path $projectRoot 'data\ollama'
    $env:OLLAMA_NO_CLOUD = '1'
    $env:OLLAMA_MAX_LOADED_MODELS = '1'
    $env:OLLAMA_NUM_PARALLEL = '1'
    New-Item -ItemType Directory -Path $env:OLLAMA_MODELS -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $projectRoot '.cache') -Force | Out-Null
    $process = Start-Process -FilePath $ollamaCommand.Source -ArgumentList @('serve') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot '.cache\ollama-server.log') -RedirectStandardError (Join-Path $projectRoot '.cache\ollama-server-error.log') -PassThru
    $process.Id | Set-Content -LiteralPath .cache/ollama-server.pid
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        try {
            $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2
            $running = $true
            break
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $running) { throw 'Ollama did not start. Inspect .cache/ollama-server-error.log.' }
}
Write-Output 'Ollama is available on http://127.0.0.1:11434'
if ($PullModel) {
    $models = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5
    if ($models.models.name -notcontains $Model) {
        & $ollamaCommand.Source pull $Model
        if ($LASTEXITCODE -ne 0) { throw 'Model download failed' }
    } else { Write-Output "Model is already available: $Model" }
}
