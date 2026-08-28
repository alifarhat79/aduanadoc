import os
import sys
import zipfile
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

def main():
    print("=" * 65)
    print(" 📦 AduanaDoc - Instalador Automático de Parche de Actualización")
    print("=" * 65)

    base_dir = Path(__file__).resolve().parent
    zip_path = base_dir / "actualizacion_parche.zip"

    if not zip_path.exists():
        print(f"\n[ERROR] No se encontró el archivo '{zip_path.name}' en esta carpeta.")
        print("Asegúrate de colocar 'actualizacion_parche.zip' en la misma carpeta que este script.\n")
        input("Presiona Enter para salir...")
        sys.exit(1)

    print(f"\n[1/4] Leyendo archivo de actualización: {zip_path.name} ({round(zip_path.stat().st_size / 1024, 1)} KB)...")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = base_dir / "backups" / f"backup_previo_parche_{timestamp}"
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            file_list = zf.namelist()
            print(f"[2/4] Creando respaldo preventivo en 'backups/' ({len(file_list)} archivos)...")
            
            for file_rel in file_list:
                local_file = base_dir / file_rel
                if local_file.exists() and local_file.is_file():
                    target_backup = backup_dir / file_rel
                    target_backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(local_file, target_backup)

            print("[3/4] Aplicando archivos actualizados...")
            for file_rel in file_list:
                target_file = base_dir / file_rel
                target_file.parent.mkdir(parents=True, exist_ok=True)
                
                source_bytes = zf.read(file_rel)
                with open(target_file, "wb") as f_out:
                    f_out.write(source_bytes)
                print(f"  [OK] Actualizado: {file_rel}")

        print("[4/4] Verificando dependencias necesarias...")
        try:
            import google.oauth2
            import googleapiclient
            print("  [OK] Librerías de Google Drive API listas.")
        except ImportError:
            print("  [*] Instalando librerías de Google Drive API (google-api-python-client, google-auth)...")
            subprocess.run([sys.executable, "-m", "pip", "install", "google-api-python-client", "google-auth", "google-auth-httplib2", "google-auth-oauthlib"], check=False)

        print("\n" + "=" * 65)
        print(" 🎉 ¡PARCHE APLICADO EXITOSAMENTE EN ESTA COMPUTADORA!")
        print("=" * 65)
        print("• La sincronización con Turso Cloud y Google Drive ya está lista.")
        print(f"• Se guardó un respaldo previo en: {backup_dir.name}")
        print("\nPuedes iniciar el sistema con: iniciar_app.bat")
        print("=" * 65 + "\n")

    except Exception as e:
        print(f"\n[ERROR] Ocurrió un fallo al aplicar el parche: {e}")
        input("\nPresiona Enter para salir...")
        sys.exit(1)

if __name__ == "__main__":
    main()
