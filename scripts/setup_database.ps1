$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    & (Join-Path $root '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'setup_database.py')
    if ($LASTEXITCODE) { throw 'PostgreSQL cluster setup failed' }
} finally { Pop-Location }
