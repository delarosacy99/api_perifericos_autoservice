# Inicio automático y recuperación en Windows 11

## Objetivo

La estación ejecuta una sola instancia de Daphne en `127.0.0.1`. Una tarea programada inicia el
agente cuando entra el usuario de Windows que opera el kiosco. El iniciador mantiene un supervisor
simple: si Daphne termina, espera 10 segundos y lo inicia otra vez.

La tarea usa un inicio de sesión interactivo deliberadamente. Así conserva el mismo contexto de
usuario con el que fueron validados la cola `CUSTOM K80`, los puertos COM y las bibliotecas del
fabricante. Si la estación debe operar sin intervención, Windows debe iniciar sesión
automáticamente con ese usuario.

## Requisitos

- Windows 11 y Python 3.12 x64.
- PowerShell ejecutado como administrador.
- `.env` creado y revisado.
- `CARD_ALLOW_TEST_PROVISIONING=false`.
- Ninguna recolección de efectivo activa durante la instalación.
- El puerto local configurado libre si se usa `-StartNow`.

Un entorno `.venv` no debe copiarse desde otra PC o ruta. El instalador crea uno nuevo cuando no
existe. Si encuentra uno inválido, se detiene; `-RecreateVenv` lo mueve a un respaldo con fecha y
crea otro sin borrar el anterior.

## Instalación en un comando

Desde la raíz del proyecto, en PowerShell como administrador:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\install_autostart.ps1 -StartNow
```

El instalador:

1. valida `.env` y bloquea el aprovisionamiento de tarjetas de laboratorio;
2. crea o valida `.venv` con Python 3.12;
3. instala `requirements.txt`;
4. aplica migraciones, ejecuta `check`, comprueba migraciones pendientes y corre las pruebas;
5. registra `DEMP Autoservice API` para el usuario interactivo;
6. configura ejecución con privilegios altos, sin restricciones de batería y sin límite de tiempo;
7. inicia la tarea y espera hasta 30 segundos por `127.0.0.1:8765` cuando se usa `-StartNow`.

Opciones útiles:

```powershell
# Reparar un .venv copiado o inválido, conservándolo como respaldo.
.\scripts\windows\install_autostart.ps1 -RecreateVenv -StartNow

# Registrar para un usuario concreto.
.\scripts\windows\install_autostart.ps1 `
    -RunAsUser "EQUIPO\Usuario" `
    -StartNow

# Omitir reinstalación de dependencias o pruebas durante una actualización controlada.
.\scripts\windows\install_autostart.ps1 `
    -SkipDependencyInstall `
    -SkipTests `
    -StartNow
```

El instalador nunca termina automáticamente el proceso que ocupa el puerto. Si detecta otro
servidor en `127.0.0.1:8765`, deja la tarea instalada y solicita liberar el puerto manualmente.

## Actualización de una estación existente

No reemplaces `.env`, `.venv`, `var` ni `logs`. Antes de extraer una nueva versión sobre la carpeta
del proyecto, detén la tarea desde PowerShell como administrador:

```powershell
Stop-ScheduledTask -TaskName "DEMP Autoservice API"
```

Después de extraer los archivos de la nueva versión, ejecuta:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\install_autostart.ps1 `
    -SkipDependencyInstall `
    -StartNow
```

Omite `-SkipDependencyInstall` cuando cambie `requirements.txt`. El instalador conserva un `.venv`
válido, aplica migraciones, valida el proyecto, ejecuta las pruebas y vuelve a registrar la tarea.

## Estado y registros

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\status_autostart.ps1
```

El registro se conserva en:

```text
logs\daphne.log
```

Una recuperación correcta contiene una secuencia similar a:

```text
Daphne finalizo con codigo 1. Reinicio en 10 segundos.
Iniciando Daphne en 127.0.0.1:8765.
Listening on TCP address 127.0.0.1:8765
```

## Desinstalación

La desinstalación solo detiene y elimina la tarea. No borra configuración, entorno virtual,
registros ni la base SQLite:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\uninstall_autostart.ps1
```

PowerShell solicita confirmación antes de eliminarla. Para automatización administrada puede
agregarse `-Confirm:$false`.

## Validación física realizada

La estación de laboratorio superó:

- inicio de Daphne al entrar el usuario después de reiniciar Windows;
- recuperación con un PID nuevo después de finalizar forzosamente el proceso de Daphne;
- reinicio del monitor continuo XL128 junto con el proceso;
- respuesta posterior de salud y acceso a NV200, CRT-603, XL128 y CUSTOM K80;
- ejecución de 118 pruebas desde un `.venv` recreado en la ruta real de la estación.
