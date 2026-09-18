@echo off
chcp 65001 >nul
echo [*] Buscando procesos de AduanaDoc en segundo plano...
for /f "tokens=2" %%p in ('wmic process where "commandline like '%%app.main:app%%' and name='python.exe'" get processid ^| findstr /r "[0-9]"') do (
    echo [*] Deteniendo proceso PID: %%p
    taskkill /F /PID %%p >nul 2>&1
)
echo [OK] Servidor en segundo plano detenido correctamente.
timeout /t 2 >nul
