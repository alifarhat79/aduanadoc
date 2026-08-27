import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from app.database import SessionLocal
from app.config import settings
from app.services.gdrive_service import GoogleDriveService

logger = logging.getLogger(__name__)

class GDriveWatcher:
    _instance: Optional["GDriveWatcher"] = None

    def __init__(self):
        self.is_running: bool = False
        self.is_enabled: bool = True  # Activado por defecto
        self.interval_seconds: int = 60  # Cada 60 segundos
        self.last_checked_at: Optional[datetime] = None
        self.last_result: Dict[str, Any] = {}
        self.task: Optional[asyncio.Task] = None

    @classmethod
    def get_instance(cls) -> "GDriveWatcher":
        if cls._instance is None:
            cls._instance = GDriveWatcher()
        return cls._instance

    async def start(self):
        """Inicia el bucle de vigilancia en segundo plano."""
        if self.is_running:
            return
        self.is_running = True
        self.task = asyncio.create_task(self._watch_loop())
        logger.info("[GDriveWatcher] Vigilante automático de Google Drive iniciado.")

    async def stop(self):
        """Detiene el bucle de vigilancia."""
        self.is_running = False
        if self.task and not self.task.done():
            self.task.cancel()
        logger.info("[GDriveWatcher] Vigilante automático detenido.")

    async def _watch_loop(self):
        """Bucle que examina Google Drive periódicamente."""
        # Esperar 5 segundos al inicio para que el servidor esté listo
        await asyncio.sleep(5)

        while self.is_running:
            if self.is_enabled:
                try:
                    await self._perform_scan()
                except Exception as e:
                    logger.error(f"[GDriveWatcher] Error durante el escaneo automático: {e}")

            # Esperar el intervalo configurado
            await asyncio.sleep(self.interval_seconds)

    async def _perform_scan(self):
        """Ejecuta un escaneo seguro en un hilo para no bloquear el bucle de eventos."""
        gdrive = GoogleDriveService()
        
        # Verificar si existe el archivo de credenciales
        import os
        from pathlib import Path
        cred_path = Path(gdrive.credentials_file)
        if not cred_path.is_absolute():
            cred_path = Path(__file__).resolve().parent.parent.parent / gdrive.credentials_file

        if not cred_path.exists():
            return

        def run_sync_scan():
            db = SessionLocal()
            try:
                return gdrive.scan_and_process_folder(db=db, propietario_default="Google Drive Auto")
            finally:
                db.close()

        # Ejecutar en thread pool para no bloquear el servidor FastAPI
        result = await asyncio.to_thread(run_sync_scan)
        self.last_checked_at = datetime.now()
        self.last_result = result

        if result.get("nuevos_procesados", 0) > 0:
            logger.info(f"[GDriveWatcher] ¡Nuevos despachos detectados y procesados automáticamente!: {result['nuevos_procesados']}")

    def set_enabled(self, enabled: bool):
        """Activa o desactiva el vigilante en segundo plano."""
        self.is_enabled = bool(enabled)
        settings.GDRIVE_WATCHER_ENABLED = bool(enabled)
        logger.info(f"[GDriveWatcher] Vigilante de Google Drive {'ACTIVADO' if self.is_enabled else 'DESACTIVADO'}.")

    def set_interval(self, interval_seconds: int):
        """Modifica el intervalo de chequeo."""
        self.interval_seconds = max(10, int(interval_seconds))
        settings.GDRIVE_WATCHER_INTERVAL = self.interval_seconds
        logger.info(f"[GDriveWatcher] Intervalo de vigilancia actualizado a {self.interval_seconds}s.")

    def get_status(self) -> Dict[str, Any]:
        """Retorna el estado actual del vigilante en segundo plano."""
        return {
            "is_running": self.is_running,
            "is_enabled": self.is_enabled,
            "interval_seconds": self.interval_seconds,
            "last_checked_at": self.last_checked_at.strftime("%H:%M:%S (%d/%m/%Y)") if self.last_checked_at else "Aún no ejecutado",
            "last_result": self.last_result
        }
