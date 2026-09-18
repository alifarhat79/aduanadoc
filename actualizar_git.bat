@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ================================================================
echo       DESCARGAR ACTUALIZACIONES DESDE GITHUB (AduanaDoc)
echo ================================================================
echo.

where git >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Git no est? instalado en esta computadora.
    pause
    exit /b 1
)

echo [*] Descargando archivos nuevos desde GitHub...
git pull origin main

if errorlevel 1 (
    echo.
    echo [AVISO] Intentando sincronizaci?n completa con GitHub...
    git fetch origin main
    git reset --mixed origin/main
)

echo.
echo ================================================================
echo   [OK] Archivos actualizados exitosamente en esta computadora.
echo ================================================================
echo.
pause
