@echo off
setlocal
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
)

echo [*] Utilizando Python: %PY_CMD%

REM 2. Sincronizacion con Git
where git >nul 2>&1
if not errorlevel 1 (
    if not exist ".git" (
        echo [*] Configurando vinculo automatico con GitHub...
        git init -q >nul 2>&1
        git remote add origin https://github.com/alifarhat79/aduanadoc.git >nul 2>&1
        git fetch origin main -q >nul 2>&1
        git branch -M main >nul 2>&1
        git reset --mixed origin/main >nul 2>&1
    )
    echo [*] Sincronizando con repositorio Git...
    git pull origin main --ff-only >nul 2>&1 || git pull origin main -q >nul 2>&1
)

REM 3. Dependencias
echo [*] Verificando dependencias...
"%PY_CMD%" -c "import fastapi, uvicorn, sqlalchemy, openpyxl, reportlab, httpx, dotenv" >nul 2>&1
if errorlevel 1 (
    echo [*] Instalando dependencias necesarias...
    "%PY_CMD%" -m pip install -r requirements.txt
)

REM 4. Abrir navegador
echo [*] Abriendo navegador web en http://127.0.0.1:8000
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8000"

REM 5. Servidor FastAPI
echo [*] Iniciando servidor AduanaDoc...
echo ================================================================
"%PY_CMD%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

pause



