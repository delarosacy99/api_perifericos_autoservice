@echo off
setlocal EnableExtensions

set "PROJECT_DIR=%~dp0"
set "BIND_PORT=%~1"
if not defined BIND_PORT set "BIND_PORT=8765"

cd /d "%PROJECT_DIR%" || exit /b 2
if not exist "logs" mkdir "logs"

set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"
set "LOG_FILE=%PROJECT_DIR%logs\daphne.log"
set PYTHONUNBUFFERED=1

if not exist "%PYTHON_EXE%" (
    echo [%date% %time%] ERROR: No existe %PYTHON_EXE%.>> "%LOG_FILE%"
    exit /b 3
)

:restart_daphne
echo [%date% %time%] Iniciando Daphne en 127.0.0.1:%BIND_PORT%.>> "%LOG_FILE%"
"%PYTHON_EXE%" -m daphne -b 127.0.0.1 -p %BIND_PORT% config.asgi:application >> "%LOG_FILE%" 2>&1
set "DAPHNE_EXIT_CODE=%ERRORLEVEL%"
echo [%date% %time%] Daphne finalizo con codigo %DAPHNE_EXIT_CODE%. Reinicio en 10 segundos.>> "%LOG_FILE%"
timeout /t 10 /nobreak >nul
goto restart_daphne
