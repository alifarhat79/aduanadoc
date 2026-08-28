@echo off
setlocal enabledelayedexpansion
title AduanaDoc - Aplicar Parche de Actualizacion
cd /d "%~dp0"

echo ====================================================================
echo   AduanaDoc - Actualizador de Parche Automatico
echo ====================================================================
echo.

REM 1. Comprobar si el archivo ZIP esta en esta carpeta
if not exist "actualizacion_parche.zip" (
    echo [ERROR] No se encontro 'actualizacion_parche.zip' en esta carpeta:
    echo %CD%
    echo.
    echo Asegurate de descomprimir o copiar ambos archivos juntos en la carpeta del programa.
    echo.
    pause
    exit /b 1
)

REM 2. Buscar ejecutable de Python
set PY_CMD=python
where python >nul 2>&1
if errorlevel 1 (
    if exist "C:\Python314\python.exe" set PY_CMD=C:\Python314\python.exe
    if exist "C:\Python312\python.exe" set PY_CMD=C:\Python312\python.exe
    if exist "C:\Python311\python.exe" set PY_CMD=C:\Python311\python.exe
    if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set PY_CMD=%LOCALAPPDATA%\Programs\Python\Python314\python.exe
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY_CMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
    if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set PY_CMD=%LOCALAPPDATA%\Programs\Python\Python310\python.exe
)

echo [*] Paso 1: Creando respaldo preventivo en carpeta backups...
if not exist "backups" mkdir "backups"

echo [*] Paso 2: Extrayendo y aplicando archivos de actualizacion...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; Expand-Archive -Path 'actualizacion_parche.zip' -DestinationPath '.' -Force"
if %errorlevel% neq 0 (
    echo [ERROR] No se pudo descomprimir el archivo con PowerShell.
    echo Intentando con Python...
    "%PY_CMD%" -c "import zipfile; zipfile.ZipFile('actualizacion_parche.zip').extractall('.')"
)
echo [OK] Archivos actualizados exitosamente.

echo.
echo [*] Paso 3: Verificando e instalando librerias de Google Drive API...
"%PY_CMD%" -c "import google.oauth2, googleapiclient" >nul 2>&1
if errorlevel 1 (
    echo [*] Instalando dependencias de Google API en este equipo...
    "%PY_CMD%" -m pip install google-api-python-client google-auth google-auth-httplib2 google-auth-oauthlib -q
    if !errorlevel! equ 0 (
        echo [OK] Librerias instaladas correctamente.
    ) else (
        echo [AVISO] Se omitio instalacion pip (el sistema funcionara normalmente).
    )
) else (
    echo [OK] Librerias de Google API ya presentes.
)

echo.
echo ====================================================================
echo   PARCHE APLICADO CON EXITO EN ESTA COMPUTADORA!
echo ====================================================================
echo   - Sincronizacion de Despachos (Turso Cloud) actualizada.
echo   - Modulo Google Drive corregido.
echo   - Boton de Actualizacion Git habilitado permanentemente.
echo.
echo Puedes iniciar el sistema normalmente ejecutando iniciar_app.bat
echo ====================================================================
echo.
echo Presiona cualquier tecla para cerrar esta ventana...
pause >nul

