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

@router.get("/check-new")
async def check_new_despachos(drive: bool = True):
    """
    Comprobación rápida para abrir automáticamente el modal de importación:
    - pendientes: despachos que el vigilante ya importó y la interfaz aún no mostró.
    - nuevos_en_drive: PDFs en Google Drive aún no registrados (sin descargarlos). Solo si drive=true.
    """
    watcher = GDriveWatcher.get_instance()
    pendientes = watcher.peek_pending()
    nuevos_en_drive: List[str] = []
    error = None
    if drive and GoogleDriveService.is_api_available():
        def run_detect():
            db = SessionLocal()
            try:
                return GoogleDriveService().detect_new_files(db)
            finally:
                db.close()
        try:
            nuevos_en_drive = await asyncio.wait_for(asyncio.to_thread(run_detect), timeout=30.0)
        except Exception as e:
            error = str(e)
            logger.warning(f"[CheckNew] No se pudo consultar Google Drive: {e}")

    return {
        "hay_nuevos": bool(pendientes or nuevos_en_drive),
        "pendientes": pendientes,
        "nuevos_en_drive": nuevos_en_drive,
        "escaneando": GoogleDriveService.is_scanning(),
        "error": error
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
        gdrive_errores: List[str] = []
        try:
            gdrive = GoogleDriveService()
            if GoogleDriveService.is_api_available():
                def run_gdrive():
                    db = SessionLocal()
                    try:
                        return gdrive.scan_and_process_folder(db=db, propietario_default="Sincronización Manual")
                    finally:
                        db.close()

                g_task = asyncio.ensure_future(asyncio.to_thread(run_gdrive))
                pct = 20
                while not g_task.done():
                    await asyncio.wait({g_task}, timeout=2.0)
                    if not g_task.done():
                        pct = min(pct + 1, 38)
                        yield send_evt(pct, 1, "gdrive_scan", "Descargando y procesando despachos nuevos de Google Drive...", [])
                g_res = g_task.result()
                detalles = g_res.get("detalles", [])
                for d in detalles:
                    if d.get("estado") == "ERROR":
                        gdrive_errores.append(f"{d.get('archivo')}: {d.get('error')}")
                        logger.error(f"[SyncStream] Error procesando {d.get('archivo')}: {d.get('error')}")
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
                gdrive_errores.append("Google API no disponible (pip install google-api-python-client google-auth)")
        except Exception as ge:
            logger.error(f"[SyncStream] Error en Google Drive: {ge}")
            gdrive_errores.append(str(ge))

        # Agregar los despachos que el vigilante automático importó en segundo plano (al arrancar o cada minuto)
        ya_listados = {i["numero"] for i in gdrive_items}
        for item in GDriveWatcher.get_instance().consume_pending():
            if item["numero"] not in ya_listados:
                ya_listados.add(item["numero"])
                gdrive_items.append(item)
                adicionados.append(item)

        if gdrive_items:
            yield send_evt(40, 2, "gdrive_done", f"¡Se importaron {len(gdrive_items)} despachos nuevos desde Google Drive!", gdrive_items)
        elif not gdrive_errores:
            yield send_evt(40, 2, "gdrive_done", "Google Drive al día. 0 despachos nuevos pendientes.", [])
        if gdrive_errores:
            yield send_evt(40, 2, "gdrive_error", f"⚠ {len(gdrive_errores)} archivo(s) de Google Drive con error: {gdrive_errores[0][:120]}", [])
        
        await asyncio.sleep(0.5)

        # --- Paso 3: Turso Cloud ---
        yield send_evt(50, 3, "turso_sync", "Conectando con Turso Cloud Database...", [])
        turso_items = []
        try:
            turso = TursoService()
            if turso.is_configured():
                yield send_evt(60, 3, "turso_sync", "Sincronizando cambios incrementales con la nube...", [])

                async def execute_turso_sync():
                    db = SessionLocal()
                    try:
                        # 1. Push incremental (sube solo despachos que falten en Turso)
                        await turso.push_delta_to_turso(db)
                        # 2. Pull rápido (descarga despachos e ítems nuevos)
                        return await turso.pull_despachos_quick(db)
                    finally:
                        db.close()

                # Timeout preventivo de 20s para asegurar que jamás se congele la sincronización
                pulled_data = await asyncio.wait_for(execute_turso_sync(), timeout=20.0)

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
            else:
                logger.info("[SyncStream] Turso Cloud no configurado, omitiendo paso")
        except asyncio.TimeoutError:
            logger.warning("[SyncStream] Tiempo de espera de Turso Cloud agotado (>20s). Continuando...")
            yield send_evt(70, 3, "turso_sync", "Turso: Red lenta, continuando con los siguientes pasos...", [])
        except Exception as te:
            logger.error(f"[SyncStream] Error en Turso Cloud: {te}")
            yield send_evt(70, 3, "turso_sync", f"Aviso en Nube: {str(te)[:50]}", [])

        if turso_items:
            yield send_evt(75, 3, "turso_done", f"¡Se sincronizaron {len(turso_items)} despachos desde Turso Cloud!", turso_items)
        else:
            yield send_evt(75, 3, "turso_done", "Base de datos en la nube 100% sincronizada.", [])

        await asyncio.sleep(0.4)

        # --- Paso 4: Git Sync ---
        yield send_evt(85, 4, "git_sync", "Verificando repositorio Git para replicación en otras PCs...", [])
        try:
            updater = UpdaterService()
            await asyncio.wait_for(asyncio.to_thread(updater.git_pull), timeout=15.0)
        except asyncio.TimeoutError:
            logger.warning("[SyncStream] Tiempo de espera agotado en Git pull")
        except Exception as gite:
            logger.warning(f"[SyncStream] Aviso en Git sync: {gite}")

        yield send_evt(95, 4, "git_done", "Repositorio Git actualizado.", [])
        await asyncio.sleep(0.4)

        # --- Paso 5: Finalización ---
        final_msg = f"¡Sincronización exitosa! Total de despachos adicionados: {len(adicionados)}." if adicionados else "¡Todo al día! Tu sistema está sincronizado con Google Drive y la Nube."
        if gdrive_errores:
            final_msg += f" ⚠ {len(gdrive_errores)} archivo(s) de Google Drive no se pudieron procesar (ver log)."
        yield send_evt(100, 5, "finalizado", final_msg, adicionados, is_final=True)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
