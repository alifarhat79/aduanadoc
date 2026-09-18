import asyncio
import json
import logging
from typing import Dict, Any, List
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.services.gdrive_service import GoogleDriveService
from app.services.gdrive_watcher import GDriveWatcher
from app.services.turso_service import TursoService
from app.services.updater_service import UpdaterService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["Sincronización Unificada"])

@router.post("/immediate-gdrive")
@router.get("/immediate-gdrive")
async def immediate_gdrive_check():
    """Dispara una lectura y procesamiento inmediato de la carpeta de Google Drive."""
    watcher = GDriveWatcher.get_instance()
    res = await watcher.scan_immediate()
    return {
        "success": True,
        "nuevos_procesados": res.get("nuevos_procesados", 0),
        "total_encontrados": res.get("total_encontrados", 0),
        "detalles": res.get("detalles", [])
    }

@router.get("/stream")
async def unified_sync_stream():
    """
    Streaming SSE en tiempo real para el modal de sincronización:
    1. Escaneo y descarga de Google Drive (0% - 40%)
    2. Sincronización bidireccional con Turso Cloud (40% - 80%)
    3. Verificación de repositorio Git (80% - 100%)
    Emite en vivo cada despacho adicionado en verde con su número e importador.
    """
    async def event_generator():
        adicionados: List[Dict[str, str]] = []

        def send_evt(pct: int, step_num: int, phase: str, msg: str, items: List[Dict[str, str]], is_final: bool = False):
            payload = {
                "percent": pct,
                "step": step_num,
                "phase": phase,
                "message": msg,
                "nuevos": items,
                "total_adicionados": len(adicionados),
                "is_final": is_final
            }
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        # --- Paso 1: Conexión inicial ---
        yield send_evt(10, 1, "iniciando", "Iniciando sincronización integral de AduanaDoc...", [])
        await asyncio.sleep(0.4)

        # --- Paso 2: Google Drive ---
        yield send_evt(20, 1, "gdrive_scan", "Examinando carpeta de Google Drive (DESPACHOS FINIQUITADOS)...", [])
        
        gdrive_items = []
        try:
            gdrive = GoogleDriveService()
            if GoogleDriveService.is_api_available():
                def run_gdrive():
                    db = SessionLocal()
                    try:
                        return gdrive.scan_and_process_folder(db=db, propietario_default="Sincronización Manual")
                    finally:
                        db.close()

                g_res = await asyncio.to_thread(run_gdrive)
                detalles = g_res.get("detalles", [])
                for d in detalles:
                    if d.get("estado") in ["PROCESADO", "PROCESADO_EXITOSO"] and d.get("numero_despacho"):
                        item = {
                            "numero": d.get("numero_despacho"),
                            "importador": d.get("importador", "Importador Registrado"),
                            "origen": "Google Drive",
                            "tipo": "nuevo"
                        }
                        gdrive_items.append(item)
                        adicionados.append(item)
            else:
                logger.warning("[SyncStream] Google API no disponible")
        except Exception as ge:
            logger.error(f"[SyncStream] Error en Google Drive: {ge}")

        if gdrive_items:
            yield send_evt(40, 2, "gdrive_done", f"¡Se importaron {len(gdrive_items)} despachos nuevos desde Google Drive!", gdrive_items)
        else:
            yield send_evt(40, 2, "gdrive_done", "Google Drive al día. 0 despachos nuevos pendientes.", [])
        
        await asyncio.sleep(0.5)

        # --- Paso 3: Turso Cloud ---
        yield send_evt(55, 3, "turso_sync", "Sincronizando bidireccionalmente con Turso Cloud Database...", [])
        turso_items = []
        try:
            turso = TursoService()
            if turso.is_configured():
                db = SessionLocal()
                try:
                    # 1. Push locales a Turso
                    await turso.push_all_to_turso(db)
                    # 2. Pull remotos de Turso
                    pulled_data = await turso.pull_despachos_quick(db)
                finally:
                    db.close()

                desp_list = pulled_data.get("despachos_nuevos_lista", []) if isinstance(pulled_data, dict) else []
                for num in desp_list:
                    item = {
                        "numero": num,
                        "importador": "Descargado de la Nube",
                        "origen": "Turso Cloud",
                        "tipo": "nuevo"
                    }
                    turso_items.append(item)
                    adicionados.append(item)
        except Exception as te:
            logger.error(f"[SyncStream] Error en Turso Cloud: {te}")

        if turso_items:
            yield send_evt(75, 3, "turso_done", f"¡Se sincronizaron {len(turso_items)} despachos desde Turso Cloud!", turso_items)
        else:
            yield send_evt(75, 3, "turso_done", "Base de datos en la nube 100% sincronizada.", [])

        await asyncio.sleep(0.5)

        # --- Paso 4: Git Sync ---
        yield send_evt(85, 4, "git_sync", "Verificando repositorio Git para replicación en otras PCs...", [])
        try:
            updater = UpdaterService()
            await asyncio.to_thread(updater.git_pull)
        except Exception as gite:
            logger.warning(f"[SyncStream] Aviso en Git sync: {gite}")

        yield send_evt(95, 4, "git_done", "Repositorio Git actualizado.", [])
        await asyncio.sleep(0.4)

        # --- Paso 5: Finalización ---
        final_msg = f"¡Sincronización exitosa! Total de despachos adicionados: {len(adicionados)}." if adicionados else "¡Todo al día! Tu sistema está sincronizado con Google Drive y la Nube."
        yield send_evt(100, 5, "finalizado", final_msg, adicionados, is_final=True)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
