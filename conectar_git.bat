@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ================================================================
echo      VINCULAR ADUANADOC CON REPOSITORIO GITHUB
echo ================================================================
echo.

where git >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Git no esta instalado en esta computadora.
    echo Por favor descarga e instala Git para Windows: https://git-scm.com/download/win
    echo.
    pause
    exit /b 1
)

echo [*] Inicializando repositorio Git...
git init -q

echo [*] Configurando origen remoto a GitHub...
git remote remove origin >nul 2>&1
git remote add origin https://github.com/alifarhat79/aduanadoc.git

echo [*] Descargando ultimas actualizaciones desde GitHub...
git fetch origin main

echo [*] Vinculando rama principal...
git branch -M main
git reset --mixed origin/main

echo.
echo ================================================================
echo   EXITO: Esta PC ya quedo vinculada con GitHub.
echo   Cada vez que inicies iniciar_app.bat se actualizara solo.
echo ================================================================
echo.
pause
