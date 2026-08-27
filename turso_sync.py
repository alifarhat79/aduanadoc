"""
Script CLI para Sincronizar Base de Datos con Turso Cloud.
Uso:
    python turso_sync.py push   -> Sube los datos locales de SQLite a Turso Cloud
    python turso_sync.py pull   -> Descarga los datos de Turso Cloud a la base local de esta PC
    python turso_sync.py test   -> Prueba la conexión con Turso Cloud
"""

import sys
import os
import asyncio
from dotenv import load_dotenv

# Cargar variables de entorno de .env
load_dotenv()

from app.database import SessionLocal, init_db
from app.services.turso_service import TursoService

async def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ["push", "pull", "test"]:
        print("\n=======================================================")
        print("   Sincronizador Turso Cloud - AduanaDoc")
        print("=======================================================")
        print("Uso:")
        print("  python turso_sync.py test   -> Probar conexión con Turso")
        print("  python turso_sync.py push   -> Subir base local a Turso Cloud")
        print("  python turso_sync.py pull   -> Descargar desde Turso Cloud a esta PC")
        print("\nVariables necesarias en .env o entorno:")
        print("  TURSO_DATABASE_URL=libsql://tu-base.turso.io")
        print("  TURSO_AUTH_TOKEN=tu_token_aqui")
        print("=======================================================\n")
        return

    command = sys.argv[1]
    db_url = os.getenv("TURSO_DATABASE_URL", "")
    token = os.getenv("TURSO_AUTH_TOKEN", "")

    if not db_url or not token:
        print("\n[!] Error: Debes configurar TURSO_DATABASE_URL y TURSO_AUTH_TOKEN en el archivo .env o pasar como variables de entorno.")
        print("Ejemplo de .env:")
        print("TURSO_DATABASE_URL=libsql://despachos-miusuario.turso.io")
        print("TURSO_AUTH_TOKEN=eyJhbGciOi...")
        return

    turso = TursoService(db_url=db_url, auth_token=token)
    init_db()
    db = SessionLocal()

    try:
        if command == "test":
            print("[*] Probando conexión con Turso Cloud...")
            res = await turso.test_connection()
            print(f"[✓] {res['message']}")

        elif command == "push":
            print("[*] Iniciando subida de datos a Turso Cloud...")
            res = await turso.push_all_to_turso(db)
            print(f"[✓] Éxito: {res['despachos_subidos']} despachos y {res['items_subidos']} mercancías subidas a Turso Cloud.")

        elif command == "pull":
            print("[*] Iniciando descarga de datos desde Turso Cloud a esta PC...")
            res = await turso.pull_all_from_turso(db)
            print(f"[✓] Éxito: {res['despachos_descargados']} despachos y {res['items_descargados']} mercancías sincronizadas en la base local de esta PC.")

    except Exception as e:
        print(f"[✗] Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(main())
