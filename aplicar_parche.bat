@echo off
chcp 65001 >nul
title AduanaDcc - Aplicar Parche de Actualización
cd /d "%~dp0"

echo ==================================================================
echo  ɥ AduanaDcc - Actualizador de Parche en 1 Clic
echo ==================================================================
echo.

if not exist "actualizacion_parche.zip" (
    echo [ERROR] Incluye el archivo actualizacion_parche.zip junto a este instalador .bat
    pause
    exit /b 1
)

python aplicar_parche.py

if %errorlevel% neq 0 (
    echo.
    echo [AVISO] Extrayendo archivos con PowerShell...
    powershell -NoProfile -Command "Expand-Archive -Path 'actualizacion_parche.zip' -DestinationPath '.' -Force"
    echo [OK] Parche aplicado con éxito.
)

echo.
echo Presiona cualquier tecla para finalizar...
pause >nul
