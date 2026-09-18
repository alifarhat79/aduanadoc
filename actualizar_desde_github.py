import os
import sys
import shutil
from pathlib import Path

# Agregar directorio raiz al PYTHONPATH
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

def main():
    print("=" * 66)
    print("     ACTUALIZADOR DE ADUANADOC - SINCRONIZACION CON GITHUB")
    print("=" * 66)
    print()

    has_git = shutil.which("git") is not None

    if has_git:
        print("[*] Git detectado en el sistema.")
        print("[*] Ejecutando sincronizacion con Git...")
    else:
        print("[*] Git NO esta instalado en esta computadora.")
        print("[*] Conectando directamente con GitHub via descarga segura (HTTP)...")
        print("[*] Tu base de datos y archivos .env locales estan 100% protegidos.")

    print()
    try:
        from app.services.updater_service import UpdaterService
        updater = UpdaterService()
        res = updater.git_pull()

        print()
        if res.get("success"):
            print("=" * 66)
            print("  [EXITO] AduanaDoc se ha actualizado correctamente!")
            if res.get("mode") == "http_direct":
                print("  Modo: Descarga directa desde GitHub (Sin necesidad de Git)")
            else:
                print("  Modo: Git CLI")
            
            commit = res.get("commit_hash") or (res.get("status", {}) or {}).get("commit_hash")
            if commit:
                print(f"  Version/Commit instalado: {commit[:10] if len(commit) > 10 else commit}")
            print("=" * 66)
            return 0
        else:
            print("=" * 66)
            print("  [ERROR] No se pudo completar la actualizacion:")
            print(f"  {res.get('error', 'Error desconocido')}")
            print("=" * 66)
            return 1
    except Exception as e:
        print("=" * 66)
        print(f"  [ERROR] Ocurrio un fallo durante la actualizacion: {e}")
        print("=" * 66)
        return 1

if __name__ == "__main__":
    code = main()
    sys.exit(code)
