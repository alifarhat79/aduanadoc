@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

title AduanaDoc - Sistema de Despachos Aduaneros
cd /d "%~dp0"

echo ================================================================
echo      SISTEMA DE GESTION DE DESPACHOS ADUANEROS (AduanaDoc)
echo ================================================================
echo.

REM --- 1. LOCALIZADOR INTELIGENTE DE PYTHON ---
set "PY_CMD="

REM Probar si python funciona directamente en PATH
python --version >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=python"
    goto :python_encontrado
)

REM Probar py -3 launcher de Windows
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=py -3"
    goto :python_encontrado
)

REM Probar py launcher
py --version >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=py"
    goto :python_encontrado
)

REM Buscar en carpetas estandar de instalacion de Python en Windows
for %%V in (314 313 312 311 310 39 38) do (
    if exist "C:\Python%%V\python.exe" (
        set "PY_CMD=C:\Python%%V\python.exe"
        goto :python_encontrado
    )
    if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
        set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
        goto :python_encontrado
    )
    if exist "%ProgramFiles%\Python%%V\python.exe" (
        set "PY_CMD=%ProgramFiles%\Python%%V\python.exe"
        goto :python_encontrado
    )
    if exist "%ProgramFiles(x86)%\Python%%V\python.exe" (
        set "PY_CMD=%ProgramFiles(x86)%\Python%%V\python.exe"
        goto :python_encontrado
    )
)

REM Buscar dinamicamente en todo el directorio Programs\Python de AppData
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
    if exist "%%D\python.exe" (
        set "PY_CMD=%%D\python.exe"
        goto :python_encontrado
    )
)

:python_no_encontrado
echo [ERROR CRITICO] No se encontro Python instalado en esta computadora.
echo.
echo Para solucionarlo:
echo 1. Descarga Python desde https://www.python.org/downloads/
echo 2. AL INSTALAR, MARCA LA CASILLA: "Add python.exe to PATH"
echo.
echo Presiona cualquier tecla para salir...
pause >nul
exit /b 1

:python_encontrado
echo [*] Python detectado: %PY_CMD%
%PY_CMD% --version
echo.

REM --- 2. SINCRONIZACION CON GIT (OPCIONAL Y SILENCIOSA) ---
where git >nul 2>&1
if not errorlevel 1 (
    if exist ".git" (
        echo [*] Sincronizando cambios de GitHub...
        git pull origin main --autostash -q >nul 2>&1 || git pull origin main -q >nul 2>&1
    )
)

REM --- 3. VERIFICACION DE DEPENDENCIAS ---
echo [*] Verificando dependencias del sistema...
%PY_CMD% -c "import fastapi, uvicorn, sqlalchemy, openpyxl, reportlab, httpx, dotenv" >nul 2>&1
if errorlevel 1 (
    echo [*] Instalando dependencias necesarias (por favor espera unos segundos)...
    %PY_CMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [*] Instalando modulos principales directamente...
        %PY_CMD% -m pip install fastapi uvicorn sqlalchemy pydantic jinja2 python-multipart pymupdf pdfplumber openpyxl reportlab python-dotenv httpx pandas
    )
)

REM --- 4. LIBERAR PUERTO 8000 SI ESTA OCUPADO ---
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

REM --- 5. ABRIR NAVEGADOR E INICIAR SERVIDOR ---
echo [*] Abriendo navegador en http://127.0.0.1:8000...
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8000"

echo [*] Servidor AduanaDoc listo y en ejecucion.
echo ================================================================
echo   Presiona CTRL + C o cierra esta ventana para detener.
echo ================================================================
echo.

%PY_CMD% -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo ================================================================
echo [AVISO] El servidor se ha detenido con codigo: %errorlevel%.
echo ================================================================
echo.
pause


