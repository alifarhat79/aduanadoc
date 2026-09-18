@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

echo ================================================================
echo       ACTUALIZADOR DE ADUANADOC (Multi-PC / GitHub)
echo ================================================================
echo.

REM 1. Buscar ejecutable de Python disponible
set PY_CMD=python

where python >nul 2>&1
if errorlevel 1 (
    if exist "C:\Python314\python.exe" set PY_CMD=C:\Python314\python.exe
    if exist "C:\Python312\python.exe" set PY_CMD=C:\Python312\python.exe
    if exist "C:\Python311\python.exe" set PY_CMD=C:\Python311\python.exe
    if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set PY_CMD=%LOCALAPPDATA%\Programs\Python\Python314\python.exe
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY_CMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
)

REM 2. Verificar si Git esta instalado en PATH o en rutas estandar
where git >nul 2>&1
if errorlevel 1 (
    if exist "C:\Program Files\Git\cmd\git.exe" set "PATH=%PATH%;C:\Program Files\Git\cmd"
    if exist "C:\Program Files (x86)\Git\cmd\git.exe" set "PATH=%PATH%;C:\Program Files (x86)\Git\cmd"
    if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Git\cmd"
)

where git >nul 2>&1
if not errorlevel 1 (
    echo [*] Git detectado en el sistema.
    echo [*] Descargando archivos y actualizaciones desde GitHub...
    echo.
    git pull origin main
    if errorlevel 1 (
        echo.
        echo [AVISO] Intentando sincronizacion limpia con GitHub...
        git fetch origin main
        git reset --mixed origin/main
    )
    echo.
    echo ================================================================
    echo   [OK] Sistema actualizado exitosamente con Git.
    echo ================================================================
    echo.
    pause
    exit /b 0
)

REM 3. Si Git NO esta instalado: Ejecutar actualizador directo via Python/HTTP
echo [AVISO] Git no esta instalado en esta computadora.
echo [*] No te preocupes: AduanaDoc se actualizara directamente
echo     descargando los archivos mas recientes desde GitHub.
echo [*] Tu base de datos y configuraciones (.env) estan protegidas.
echo.

"%PY_CMD%" actualizar_desde_github.py

echo.
echo ----------------------------------------------------------------
echo NOTA: Puedes instalar Git con 1 solo clic ejecutando:
echo       instalar_git.bat
echo ----------------------------------------------------------------
echo.
set /p DESEA_GIT="Deseas instalar Git en esta computadora ahora mismo? (S/N): "
if /i "%DESEA_GIT%"=="S" (
    call "%~dp0instalar_git.bat"
)

pause
exit /b 0
