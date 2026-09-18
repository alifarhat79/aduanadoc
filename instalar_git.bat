@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

echo ================================================================
echo           INSTALADOR AUTOMATICO DE GIT PARA WINDOWS
echo                      (Para AduanaDoc)
echo ================================================================
echo.

where git >nul 2>&1
if not errorlevel 1 (
    echo [OK] Git ya se encuentra instalado en esta computadora:
    git --version
    echo.
    echo No es necesario volver a instalarlo.
    pause
    exit /b 0
)

echo [*] Git no esta instalado en este equipo.
echo [*] Iniciando instalacion automatica de Git para Windows...
echo.

REM 1. Intentar instalar via winget (Windows Package Manager)
where winget >nul 2>&1
if not errorlevel 1 (
    echo [*] Descargando e instalando Git via Windows Package Manager (winget)...
    winget install --id Git.Git -e --source winget --accept-source-agreements --accept-package-agreements --silent
    if not errorlevel 1 goto VERIFICAR_INSTALACION
)

REM 2. Fallback: Descargar instalador oficial via PowerShell
echo [*] Descargando instalador oficial de Git para Windows (64-bit)...
set "INSTALLER=%TEMP%\git_installer_%RANDOM%.exe"

powershell -ExecutionPolicy Bypass -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
    "$url = 'https://github.com/git-for-windows/git/releases/latest/download/Git-64-bit.exe'; " ^
    "Write-Host '[*] Conectando con servidor de Git...'; " ^
    "Invoke-WebRequest -Uri $url -OutFile '%INSTALLER%'"

if not exist "%INSTALLER%" (
    echo.
    echo [ERROR] No se pudo descargar el instalador de Git automaticamente.
    echo Por favor, descargalo e instalalo manualmente desde:
    echo   https://git-scm.com/download/win
    echo.
    pause
    exit /b 1
)

echo [*] Ejecutando instalacion silenciosa de Git...
"%INSTALLER%" /VERYSILENT /NORESTART /NOCANCEL /SP- /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS

del "%INSTALLER%" >nul 2>&1

:VERIFICAR_INSTALACION
REM Agregar rutas comunes de Git al PATH de la sesion actual
set "PATH=%PATH%;C:\Program Files\Git\cmd;C:\Program Files\Git\bin;C:\Program Files (x86)\Git\cmd;%LOCALAPPDATA%\Programs\Git\cmd"

where git >nul 2>&1
if errorlevel 1 (
    echo.
    echo [AVISO] Git se instalo, pero es posible que requieras reiniciar la consola
    echo o reiniciar tu computadora para que Windows reconozca el comando 'git'.
) else (
    echo.
    echo ================================================================
    echo   [EXITO] Git se ha instalado correctamente:
    git --version
    echo ================================================================
)

echo.
pause
exit /b 0
