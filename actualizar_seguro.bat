@echo off
REM ================================================================
REM  Actualizacion segura de AduanaDoc
REM  Se usa solo cuando "git pull" no pudo avanzar directo.
REM  Antes de igualar esta PC con GitHub guarda cualquier cambio local
REM  para no perder trabajo.
REM ================================================================
git fetch origin main -q >nul 2>&1
if errorlevel 1 (
    echo [!] Sin conexion con GitHub: se usa la version instalada en esta PC.
    exit /b 0
)
echo [!] No se pudo actualizar directo: guardando cambios locales antes de actualizar...
git branch -f respaldo-antes-de-actualizar HEAD >nul 2>&1
git stash push -u -m "respaldo automatico antes de actualizar" >nul 2>&1
git reset --hard origin/main >nul 2>&1
echo [*] Listo. Cambios locales guardados en la rama respaldo-antes-de-actualizar y en git stash.
exit /b 0
