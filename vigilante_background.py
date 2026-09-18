"""
Vigilante Autónomo en Segundo Plano para AduanaDoc (SIN SERVIDOR WEB)
--------------------------------------------------------------------
Este script vigila la carpeta de Google Drive periódicamente,
descarga y procesa cualquier despacho nuevo, lo guarda en la base
de datos local, lo sincroniza con Turso Cloud y emite notificaciones
nativas en el escritorio de Windows.

NO requiere iniciar el servidor FastAPI/Uvicorn ni abrir ningún navegador.
"""

import os
import sys
import time
import signal
import logging
from datetime import datetime

# Forzar codificación UTF-8 en consola de Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("VigilanteBackground")

from app.database import init_db, SessionLocal
from app.config import settings
from app.services.gdrive_service import GoogleDriveService
from app.services.windows_notification_service import WindowsNotificationService
from app.services.turso_service import TursoService

INTERVALO_SEGUNDOS = int(os.getenv("GDRIVE_WATCHER_INTERVAL", "60"))
EJECUTANDO = True

def handle_sigterm(signum, frame):
    global EJECUTANDO
    logger.info("[Vigilante] Solicitud de detención recibida. Finalizando bucle...")
    EJECUTANDO = False

signal.signal(signal.SIGINT, handle_sigterm)
if hasattr(signal, "SIGTERM"):
    signal.signal(signal.SIGTERM, handle_sigterm)

def ejecutar_vigilancia():
    global EJECUTANDO
    logger.info("=" * 65)
    logger.info("  AduanaDoc - Vigilante Autónomo en Segundo Plano (Sin Servidor)")
    logger.info(f"  Intervalo de chequeo: cada {INTERVALO_SEGUNDOS} segundos")
    logger.info("=" * 65)

    # 1. Inicializar Base de Datos SQLite si hace falta
    try:
        init_db()
        logger.info("[DB] Base de datos local inicializada correctamente.")
    except Exception as db_err:
        logger.error(f"[DB Error] No se pudo inicializar la base de datos: {db_err}")
        return

    # 2. Verificar disponibilidad de Google Drive
    if not GoogleDriveService.is_api_available():
        logger.error("[Drive Error] Librerías de Google API no encontradas. Ejecuta: pip install google-api-python-client google-auth")
        return

    gdrive = GoogleDriveService()
    from pathlib import Path
    cred_path = Path(gdrive.credentials_file)
    if not cred_path.is_absolute():
        cred_path = Path(__file__).resolve().parent / gdrive.credentials_file

    if not cred_path.exists():
        logger.error(f"[Drive Error] Archivo service_account.json no encontrado en: {cred_path}")
        return

    # 3. Notificación inicial de arranque silencioso en Windows
    try:
        WindowsNotificationService.send_notification(
            title="AduanaDoc Vigilante Activo",
            message=f"Monitoreando Google Drive en segundo plano (cada {INTERVALO_SEGUNDOS}s). Sin servidor web."
        )
    except Exception:
        pass

    # 4. Bucle principal de vigilancia
    primera_vez = True
    while EJECUTANDO:
        try:
            logger.info(f"[Escaneo] Verificando carpeta Google Drive ({gdrive.folder_id})...")
            db = SessionLocal()
            try:
                result = gdrive.scan_and_process_folder(
                    db=db,
                    propietario_default="Vigilante Background",
                    allow_duplicate=False
                )
            finally:
                db.close()

            total = result.get("total_encontrados", 0)
            nuevos = result.get("nuevos_procesados", 0)
            omitidos = result.get("omitidos_duplicados", 0)
            errores = result.get("errores", 0)

            logger.info(f"[Resultado] Total: {total} | Nuevos agregados: {nuevos} | Omitidos: {omitidos} | Errores: {errores}")

            if nuevos > 0:
                detalles = result.get("detalles", [])
                despachos_ok = [
                    d.get("numero_despacho") for d in detalles
                    if d.get("estado") in ["PROCESADO_EXITOSO", "PROCESADO"] and d.get("numero_despacho")
                ]
                logger.info(f"¡{nuevos} despacho(s) nuevo(s) procesado(s) y notificado(s) en Windows!")

        except Exception as e:
            logger.error(f"[Error en escaneo] {e}", exc_info=True)

        # Esperar el intervalo antes de la siguiente verificación
        for _ in range(INTERVALO_SEGUNDOS):
            if not EJECUTANDO:
                break
            time.sleep(1)

    logger.info("[Vigilante] Proceso detenido limpiamente.")

if __name__ == "__main__":
    ejecutar_vigilancia()
