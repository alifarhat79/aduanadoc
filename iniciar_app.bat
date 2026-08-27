@echo off
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

title AduanaDoc - Sistema de Despachos Aduaneros
cd /d "%~dp0"

echo ================================================================
echo      SISTEMA DE GESTION DE DESPACHOS ADUANEROS (AduanaDoc)
echo ================================================================
echo.

:: Verificar si Python esta en PATH o buscar en rutas comunes
set "PY_CMD=python"
where python >nul 2>&1
if %errorlevel% neq 0 (
    if exist "C:\Python314\python.exe" (
        set "PY_CMD=C:\Python314\python.exe"
    ) else if exist "C:\Python312\python.exe" (
        set "PY_CMD=C:\Python312\python.exe"
    ) else if exist "C:\Python311\python.exe" (
        set "PY_CMD=C:\Python311\python.exe"
    ) else if exist "C:\Python310\python.exe" (
        set "PY_CMD=C:\Python310\python.exe"
    ) else if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (
        set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    ) else if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    ) else (
        echo [!] ERROR: Python no esta instalado o no esta en el PATH.
        echo [*] Por favor descarga Python desde https://www.python.org/
        echo.
        pause
        exit /b 1
    )
)

:: Sincronizar con repositorio Git si esta configurado
if exist ".git" (
    where git >nul 2>&1
    if %errorlevel% equ 0 (
        echo [*] Repositorio Git detectado. Verificando actualizaciones remotas...
        git pull --ff-only 2>nul
        if %errorlevel% equ 0 (
            echo [*] Codigo sincronizado con exito desde Git.
        ) else (
            echo [!] No se pudo sincronizar con Git (posiblemente sin conexion o cambios locales pendientes).
        )
    )
)

:: Verificar dependencias
echo [*] Verificando dependencias del sistema...
"%PY_CMD%" -c "import fastapi, uvicorn, sqlalchemy, openpyxl, reportlab, httpx, dotenv, fitz, pdfplumber, googleapiclient" >nul 2>&1
if %errorlevel% neq 0 (
    echo [*] Instalando dependencias necesarias desde requirements.txt...
    "%PY_CMD%" -m pip install -r requirements.txt
)

:: Abrir navegador en 2 segundos
echo [*] Abriendo navegador web en: http://127.0.0.1:8000
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8000"

:: Iniciar servidor FastAPI
echo [*] Iniciando servidor AduanaDoc...
echo ================================================================
"%PY_CMD%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

pause
