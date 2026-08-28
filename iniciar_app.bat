@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

title AduanaDoc - Sistema de Despachos Aduaneros
cd /d "%~dp0"

echo ================================================================
echo      SISTEMA DE GESTION DE DESPACHOS ADUANEROS (AduanaDoc)
echo ================================================================
echo.

REM 1. Buscar ejecutable de Python
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

echo [*] Utilizando Python: %PY_CMD%

REM 2. Sincronizacion con Git (silenciosa y segura)
where git >nul 2>&1
if not errorlevel 1 (
    if not exist ".git" (
        echo [*] Configurando vinculo con repositorio GitHub...
        git init -q >nul 2>&1
        git remote add origin https://github.com/alifarhat79/aduanadoc.git >nul 2>&1
        git fetch origin main -q >nul 2>&1
        git branch -M main >nul 2>&1
        git reset --mixed origin/main >nul 2>&1
    )
    echo [*] Sincronizando cambios de GitHub...
    git pull origin main --autostash -q >nul 2>&1 || git pull origin main -q >nul 2>&1
)

REM 3. Dependencias basicas
echo [*] Verificando dependencias...
"%PY_CMD%" -c "import fastapi, uvicorn, sqlalchemy, openpyxl, reportlab, httpx, dotenv" >nul 2>&1
if errorlevel 1 (
    echo [*] Instalando dependencias necesarias (esto puede tardar unos segundos)...
    "%PY_CMD%" -m pip install fastapi uvicorn sqlalchemy pydantic jinja2 python-multipart pymupdf pdfplumber openpyxl reportlab python-dotenv httpx pandas
)

REM 4. Liberar puerto 8000 si un proceso anterior quedo colgado
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

REM 5. Abrir navegador
echo [*] Abriendo navegador web en http://127.0.0.1:8000
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8000"

REM 6. Iniciar Servidor FastAPI
echo [*] Servidor iniciado correctamente en http://127.0.0.1:8000
echo ================================================================
echo   Para detener el servidor, cierra esta ventana.
echo ================================================================
echo.
"%PY_CMD%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

if errorlevel 1 (
    echo.
    echo ================================================================
    echo [ERROR] El servidor se detuvo con codigo de error %errorlevel%.
    echo ================================================================
    echo.
    pause
)

