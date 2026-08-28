@echo off
chcp 65001 >nul
title AduanaDoc - Diagnostico del Sistema
cd /d "%~dp0"

echo ================================================================
echo   DIAGNOSTICO COMPLETO DEL SISTEMA (AduanaDoc)
echo ================================================================
echo.

echo [1] Verificando comandos de Python:
where python
where py

echo.
echo [2] Version de Python activa:
python --version
py -3 --version

echo.
echo [3] Modulos clave instalados:
python -c "import fastapi, uvicorn, sqlalchemy; print('FastAPI/Uvicorn/SQLAlchemy: OK')"
python -c "import openpyxl, reportlab, fitz, pdfplumber; print('PDF/Excel: OK')"
python -c "import httpx, dotenv, pandas; print('HTTP/Dotenv/Pandas: OK')"

echo.
echo [4] Verificando Puerto 8000:
netstat -aon | findstr ":8000"

echo.
echo [5] Archivos principales en esta carpeta:
dir /b app iniciar_app.bat requirements.txt

echo.
echo ================================================================
echo Diagnostico finalizado. Toma una captura o copia el texto si hay error.
echo ================================================================
pause
