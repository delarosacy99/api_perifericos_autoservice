[CmdletBinding()]
param(
    [string]$TaskName = "DEMP Autoservice API",
    [string]$RunAsUser = "",
    [int]$Port = 0,
    [switch]$RecreateVenv,
    [switch]$SkipDependencyInstall,
    [switch]$SkipTests,
    [switch]$StartNow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-NativeCommand {
    param(
        [string]$Description,
        [string]$FilePath,
        [string[]]$ArgumentList
    )
    Write-Step $Description
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Description fallo con codigo $LASTEXITCODE."
    }
}

function Test-Python312 {
    param([string]$PythonPath)
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return $false
    }
    & $PythonPath -c "import struct, sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8 else 1)" *> $null
    return $LASTEXITCODE -eq 0
}

function New-PythonVirtualEnvironment {
    param([string]$Destination)

    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -ne $pyLauncher) {
        & $pyLauncher.Source -3.12 -c `
            "import struct, sys; raise SystemExit(0 if struct.calcsize('P') == 8 else 1)" *> $null
        if ($LASTEXITCODE -eq 0) {
            Invoke-NativeCommand `
                -Description "Creando .venv con Python 3.12" `
                -FilePath $pyLauncher.Source `
                -ArgumentList @("-3.12", "-m", "venv", $Destination)
            return
        }
    }

    $pythonCandidates = Get-Command python.exe -All -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Source -Unique
    foreach ($candidate in $pythonCandidates) {
        if (Test-Python312 -PythonPath $candidate) {
            Invoke-NativeCommand `
                -Description "Creando .venv con Python 3.12" `
                -FilePath $candidate `
                -ArgumentList @("-m", "venv", $Destination)
            return
        }
    }

    throw "No se encontro Python 3.12 x64. Instalalo antes de continuar."
}

function Get-DotEnvValue {
    param(
        [string]$Path,
        [string]$Name
    )
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*)\s*$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Wait-TaskStopped {
    param([string]$Name)
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
        if ($null -eq $task -or $task.State -ne "Running") {
            return
        }
        Start-Sleep -Seconds 1
    }
    throw "La tarea $Name no se detuvo dentro de 15 segundos."
}

if (-not (Test-Administrator)) {
    throw "Ejecuta PowerShell como administrador."
}

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$EnvFile = Join-Path $ProjectRoot ".env"
$RequirementsFile = Join-Path $ProjectRoot "requirements.txt"
$ManagePy = Join-Path $ProjectRoot "manage.py"
$SupervisorPath = Join-Path $ProjectRoot "start_autoservice.cmd"
$VenvPath = Join-Path $ProjectRoot ".venv"
$PythonExe = Join-Path $VenvPath "Scripts\python.exe"
$LogFile = Join-Path $ProjectRoot "logs\daphne.log"

foreach ($requiredFile in @($EnvFile, $RequirementsFile, $ManagePy, $SupervisorPath)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Falta el archivo requerido: $requiredFile"
    }
}

$provisioningEnabled = Get-DotEnvValue -Path $EnvFile -Name "CARD_ALLOW_TEST_PROVISIONING"
if ($provisioningEnabled -match "^(1|true|yes|on)$") {
    throw "CARD_ALLOW_TEST_PROVISIONING debe estar en false antes de instalar el inicio automatico."
}

$debugEnabled = Get-DotEnvValue -Path $EnvFile -Name "DJANGO_DEBUG"
if ($debugEnabled -notmatch "^(0|false|no|off)$") {
    Write-Warning "DJANGO_DEBUG no esta en false. Cambialo antes de usar la estacion en produccion."
}

if ($Port -eq 0) {
    $configuredPort = Get-DotEnvValue -Path $EnvFile -Name "LOCAL_PORT"
    if ([string]::IsNullOrWhiteSpace($configuredPort)) {
        $Port = 8765
    }
    elseif (-not [int]::TryParse($configuredPort, [ref]$Port)) {
        throw "LOCAL_PORT debe ser un numero entero."
    }
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw "El puerto debe estar entre 1 y 65535."
}

if ([string]::IsNullOrWhiteSpace($RunAsUser)) {
    $RunAsUser = (Get-CimInstance Win32_ComputerSystem).UserName
    if ([string]::IsNullOrWhiteSpace($RunAsUser)) {
        throw "No hay un usuario interactivo. Especifica -RunAsUser EQUIPO\Usuario."
    }
}

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$existingTaskWasRunning = $null -ne $existingTask -and $existingTask.State -eq "Running"
if ($existingTaskWasRunning) {
    Write-Step "Deteniendo la tarea existente"
    Stop-ScheduledTask -TaskName $TaskName
    Wait-TaskStopped -Name $TaskName
}

try {
if (Test-Path -LiteralPath $VenvPath -PathType Container) {
    if ($RecreateVenv) {
        $backupPath = Join-Path $ProjectRoot (".venv_backup_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
        Write-Step "Conservando el entorno anterior en $backupPath"
        Move-Item -LiteralPath $VenvPath -Destination $backupPath
    }
    elseif (-not (Test-Python312 -PythonPath $PythonExe)) {
        throw (
            ".venv no es valido para esta PC. Los entornos virtuales no se copian entre equipos. " +
            "Vuelve a ejecutar con -RecreateVenv para conservarlo como respaldo y crear uno nuevo."
        )
    }
}

if (-not (Test-Path -LiteralPath $VenvPath -PathType Container)) {
    New-PythonVirtualEnvironment -Destination $VenvPath
}
if (-not (Test-Python312 -PythonPath $PythonExe)) {
    throw "El nuevo .venv no contiene un Python 3.12 valido."
}

if (-not $SkipDependencyInstall) {
    Invoke-NativeCommand `
        -Description "Actualizando pip" `
        -FilePath $PythonExe `
        -ArgumentList @("-m", "pip", "install", "--upgrade", "pip")
    Invoke-NativeCommand `
        -Description "Instalando dependencias" `
        -FilePath $PythonExe `
        -ArgumentList @("-m", "pip", "install", "-r", $RequirementsFile)
}

Invoke-NativeCommand `
    -Description "Aplicando migraciones" `
    -FilePath $PythonExe `
    -ArgumentList @($ManagePy, "migrate", "--noinput")
Invoke-NativeCommand `
    -Description "Validando configuracion Django" `
    -FilePath $PythonExe `
    -ArgumentList @($ManagePy, "check")
Invoke-NativeCommand `
    -Description "Comprobando migraciones pendientes" `
    -FilePath $PythonExe `
    -ArgumentList @($ManagePy, "makemigrations", "--check", "--dry-run")

if (-not $SkipTests) {
    Invoke-NativeCommand `
        -Description "Ejecutando pruebas" `
        -FilePath $PythonExe `
        -ArgumentList @($ManagePy, "test")
}

$actionArguments = '/d /c ""{0}" {1}"' -f $SupervisorPath, $Port
$action = New-ScheduledTaskAction `
    -Execute $env:ComSpec `
    -Argument $actionArguments `
    -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $RunAsUser
$principal = New-ScheduledTaskPrincipal `
    -UserId $RunAsUser `
    -LogonType Interactive `
    -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Write-Step "Registrando la tarea $TaskName para $RunAsUser"
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "API local de perifericos DEMP" `
    -Force | Out-Null

if ($StartNow -or $existingTaskWasRunning) {
    $listener = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port `
        -State Listen -ErrorAction SilentlyContinue
    if ($null -ne $listener) {
        throw (
            "La tarea fue instalada, pero el puerto 127.0.0.1:$Port ya esta ocupado. " +
            "No se termino ningun proceso. Libera el puerto y usa Start-ScheduledTask."
        )
    }

    Write-Step "Iniciando la tarea"
    Start-ScheduledTask -TaskName $TaskName
    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Seconds 1
        $listener = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port `
            -State Listen -ErrorAction SilentlyContinue
    } while ($null -eq $listener -and (Get-Date) -lt $deadline)

    if ($null -eq $listener) {
        throw "Daphne no abrio el puerto en 30 segundos. Revisa $LogFile"
    }
}

$task = Get-ScheduledTask -TaskName $TaskName
Write-Host "`nInstalacion completada." -ForegroundColor Green
[pscustomobject]@{
    TaskName = $task.TaskName
    State = $task.State
    RunAsUser = $RunAsUser
    ProjectRoot = $ProjectRoot
    BindAddress = "127.0.0.1"
    Port = $Port
    LogFile = $LogFile
}
}
catch {
    $installationError = $_
    if ($existingTaskWasRunning) {
        try {
            $taskToRecover = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            if ($null -ne $taskToRecover -and $taskToRecover.State -ne "Running") {
                Write-Warning "La instalacion fallo; intentando recuperar la tarea anterior."
                Start-ScheduledTask -TaskName $TaskName
            }
        }
        catch {
            Write-Warning "No se pudo reiniciar automaticamente la tarea anterior."
        }
    }
    throw $installationError
}
