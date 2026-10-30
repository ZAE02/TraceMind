param([string]$RunId = '')
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$processFile = Join-Path $projectRoot '.local/api/continuous-process.json'
if (Test-Path -LiteralPath $processFile) {
    $saved = Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json
    $existing = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$saved.pid)
    if ($existing -and $existing.Name -eq 'python.exe' -and $existing.CommandLine -match '(gemini_batch|run_project)\.py') {
        Write-Output ('Synthesis already running, PID ' + $saved.pid)
        exit 0
    }
    if (-not $RunId) { $RunId = $saved.batch_id }
}
if (-not $RunId) { $RunId = 'gemini-continuous-' + (Get-Date -Format 'yyyyMMdd-HHmmss') }
if ($RunId -notmatch '^[a-zA-Z0-9_-]+$') { throw 'Invalid run ID' }
$stopFile = Join-Path $projectRoot '.local/api/STOP'
if (Test-Path -LiteralPath $stopFile) { throw 'STOP marker present; remove it only when resumption is authorized' }
$runnerExe = Join-Path $projectRoot '.venv/Scripts/python.exe'
$runnerScript = Join-Path $projectRoot 'scripts/run_project.py'
$stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
$outFile = Join-Path $projectRoot ('.local/api/' + $RunId + '-' + $stamp + '.stdout.log')
$errFile = Join-Path $projectRoot ('.local/api/' + $RunId + '-' + $stamp + '.stderr.log')
$worker = Start-Process -FilePath $runnerExe -ArgumentList @('-X','utf8','-u',('"' + $runnerScript + '"'),'--batch-id',$RunId) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $outFile -RedirectStandardError $errFile -PassThru
@{pid=$worker.Id;batch_id=$RunId;started_at=[DateTime]::UtcNow.ToString('o');limit=$null;interval_seconds=30;stdout=$outFile;stderr=$errFile;scope='full_project_20basegroups_models_reports'} | ConvertTo-Json | Set-Content -LiteralPath $processFile -Encoding utf8
Write-Output ('Started synthesis process PID ' + $worker.Id)
