@echo off
chcp 65001 >nul
echo ================================================================
echo   INSTALAR VIGILANTE SILENCIOSO (SIN SERVIDOR) EN WINDOWS STARTUP
echo ================================================================
echo.

set "SCRIPT_DIR=%~dp0"
set "TARGET_VBS=%SCRIPT_DIR%iniciar_vigilante_silencioso.vbs"
set "SHORTCUT_PATH=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AduanaDoc_Vigilante.lnk"

powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = '%TARGET_VBS%'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'AduanaDoc Vigilante Silencioso sin servidor'; $s.Save()"

if exist "%SHORTCUT_PATH%" (
    echo [OK] Vigilante instalado exitosamente en el inicio automatico.
    echo      Cada vez que enciendas tu PC, vigilara Google Drive en segundo plano
    echo      SIN necesidad de abrir el servidor web ni el navegador, y te avisara
    echo      con notificaciones de Windows cuando haya despachos nuevos.
) else (
    echo [ERROR] No se pudo crear el acceso directo de inicio automatico.
)
echo.
pause
