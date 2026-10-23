[CmdletBinding()]
param(
    [string]$TaskName = "DEMP Autoservice API",
    [int]$Port = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$EnvFile = Join-Path $ProjectRoot ".env"
$LogFile = Join-Path $ProjectRoot "logs\daphne.log"

if ($Port -eq 0 -and (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    foreach ($line in Get-Content -LiteralPath $EnvFile) {
        if ($line -match "^\s*LOCAL_PORT\s*=\s*(.*)\s*$") {
            $configuredPort = $Matches[1].Trim().Trim('"').Trim("'")
            if (-not [int]::TryParse($configuredPort, [ref]$Port)) {
                throw "LOCAL_PORT debe ser un numero entero."
            }
            break
        }
    }
}
if ($Port -eq 0) {
    $Port = 8765
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw "El puerto debe estar entre 1 y 65535."
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($null -eq $task) {
    Write-Host "La tarea $TaskName no esta instalada." -ForegroundColor Yellow
    exit 1
}

$info = Get-ScheduledTaskInfo -TaskName $TaskName
$listeners = @(Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port `
    -State Listen -ErrorAction SilentlyContinue)

[pscustomobject]@{
    TaskName = $task.TaskName
    State = $task.State
    LastRunTime = $info.LastRunTime
    LastTaskResult = $info.LastTaskResult
    Listening = $listeners.Count -gt 0
    ListenerPids = ($listeners | Select-Object -ExpandProperty OwningProcess) -join ","
    Address = "127.0.0.1"
    Port = $Port
    LogFile = $LogFile
}

if (Test-Path -LiteralPath $LogFile -PathType Leaf) {
    Write-Host "`nUltimas lineas del log:" -ForegroundColor Cyan
    Get-Content -LiteralPath $LogFile -Tail 15
}
