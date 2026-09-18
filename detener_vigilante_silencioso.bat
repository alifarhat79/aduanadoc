@echo off
chcp 65001 >nul
echo [*] Buscando proceso del Vigilante Aut?nomo en segundo plano...
for /f "tokens=2" %%p in ('wmic process where "commandline like '%%vigilante_background.py%%' and name='python.exe'" get processid ^| findstr /r "[0-9]"') do (
    echo [*] Deteniendo Vigilante PID: %%p
    taskkill /F /PID %%p >nul 2>&1
)
echo [OK] Vigilante en segundo plano detenido correctamente.
timeout /t 2 >nul
