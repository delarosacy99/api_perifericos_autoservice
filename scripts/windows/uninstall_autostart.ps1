[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [string]$TaskName = "DEMP Autoservice API"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Ejecuta PowerShell como administrador."
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "La tarea $TaskName no esta instalada."
    exit 0
}

if (-not $PSCmdlet.ShouldProcess($TaskName, "detener y eliminar la tarea programada")) {
    exit 0
}

if ($task.State -eq "Running") {
    Stop-ScheduledTask -TaskName $TaskName
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        Start-Sleep -Seconds 1
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($null -eq $task -or $task.State -ne "Running") {
            break
        }
    }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -ne $task -and $task.State -eq "Running") {
        throw "La tarea $TaskName no se detuvo dentro de 15 segundos y no fue eliminada."
    }
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Tarea eliminada. No se borraron .env, .venv, logs ni datos locales." -ForegroundColor Green
