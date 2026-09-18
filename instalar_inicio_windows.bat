@echo off
chcp 65001 >nul
echo ================================================================
echo   INSTALAR ADUANADOC EN EL INICIO AUTOMATICO DE WINDOWS
echo ================================================================
echo.

set "SCRIPT_DIR=%~dp0"
set "TARGET_VBS=%SCRIPT_DIR%iniciar_segundo_plano.vbs"
set "SHORTCUT_PATH=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AduanaDoc_Background.lnk"

powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = '%TARGET_VBS%'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'AduanaDoc Vigilante en Segundo Plano'; $s.Save()"

if exist "%SHORTCUT_PATH%" (
    echo [OK] AduanaDoc configurado exitosamente para iniciar con Windows.
    echo      Cada vez que enciendas tu PC, vigilara Google Drive en segundo plano
    echo      y te notificara con avisos de Windows.
) else (
    echo [ERROR] No se pudo crear el acceso directo de inicio automatico.
)
echo.
pause
