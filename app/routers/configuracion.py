from fastapi import APIRouter, Depends, Request, Form, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
import os
from pathlib import Path
from pydantic import BaseModel
from typing import Optional
from dotenv import set_key

from app.database import get_db
from app.models import Despacho, DespachoItem
from app.services.turso_service import TursoService
from app.services.gdrive_service import GoogleDriveService
from app.templates_config import templates
from app.config import settings

router = APIRouter(prefix="/configuracion", tags=["Configuración"])

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"

class SaveTursoPayload(BaseModel):
    database_url: str
    auth_token: str

class SaveNotificationsPayload(BaseModel):
    telegram_bot_token: Optional[str] = ""
    telegram_chat_id: Optional[str] = ""
    webhook_url: Optional[str] = ""
    notifications_enabled: Optional[bool] = True

class GDriveScanPayload(BaseModel):
    folder_id: Optional[str] = None
    propietario: Optional[str] = "Google Drive"

class GDriveLocalScanPayload(BaseModel):
    local_path: str
    propietario: Optional[str] = "Carpeta Local"

@router.get("", response_class=HTMLResponse)
async def configuracion_view(request: Request, db: Session = Depends(get_db)):
    turso_url = os.getenv("TURSO_DATABASE_URL", "libsql://despachos-alifarhat.aws-us-east-1.turso.io")
    turso_token = os.getenv("TURSO_AUTH_TOKEN", "")
    gdrive_folder_id = os.getenv("GDRIVE_FOLDER_ID", "1NP6zJHL9w_bV0W1BysIDRIZ5FXZzc5Kv")
    gdrive_credentials_file = os.getenv("GDRIVE_CREDENTIALS_FILE", "./service_account.json")

    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", getattr(settings, "TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", getattr(settings, "TELEGRAM_CHAT_ID", ""))
    webhook_url = os.getenv("WEBHOOK_URL", getattr(settings, "WEBHOOK_URL", ""))
    notifications_enabled = os.getenv("NOTIFICATIONS_ENABLED", "true").lower() in ("true", "1", "yes")

    has_credentials_file = (Path(__file__).resolve().parent.parent.parent / gdrive_credentials_file).exists()

    total_despachos = db.query(func.count(Despacho.id)).scalar() or 0
    total_items = db.query(func.count(DespachoItem.id)).scalar() or 0

    is_configured = bool(turso_url and turso_token)

    return templates.TemplateResponse(
        request=request,
        name="configuracion.html",
        context={
            "turso_url": turso_url,
            "turso_token": turso_token,
            "gdrive_folder_id": gdrive_folder_id,
            "has_credentials_file": has_credentials_file,
            "is_configured": is_configured,
            "telegram_bot_token": telegram_bot_token,
            "telegram_chat_id": telegram_chat_id,
            "webhook_url": webhook_url,
            "notifications_enabled": notifications_enabled,
            "total_despachos": total_despachos,
            "total_items": total_items,
            "env_path": str(ENV_PATH),
            "app_version": settings.APP_VERSION
        }
    )

@router.post("/api/gdrive/scan")
async def scan_gdrive_api(payload: Optional[GDriveScanPayload] = None, db: Session = Depends(get_db)):
    folder_id = payload.folder_id if payload and payload.folder_id else os.getenv("GDRIVE_FOLDER_ID", "1NP6zJHL9w_bV0W1BysIDRIZ5FXZzc5Kv")
    propietario = payload.propietario if payload and payload.propietario else "Google Drive"

    service = GoogleDriveService(folder_id=folder_id)
    try:
        results = service.scan_and_process_folder(db=db, propietario_default=propietario)
        return {
            "success": True,
            "message": f"Escaneo completado. {results['nuevos_procesados']} despachos nuevos procesados, {results['omitidos_duplicados']} omitidos por ya existir.",
            "data": results
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/api/gdrive/scan-local")
async def scan_gdrive_local_api(payload: GDriveLocalScanPayload, db: Session = Depends(get_db)):
    service = GoogleDriveService()
    try:
        results = service.scan_local_folder(db=db, local_path=payload.local_path, propietario_default=payload.propietario or "Carpeta Local")
        return {
            "success": True,
            "message": f"Escaneo local completado. {results['nuevos_procesados']} despachos nuevos procesados, {results['omitidos_duplicados']} omitidos por ya existir.",
            "data": results
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/api/gdrive/watcher/status")
async def get_gdrive_watcher_status():
    from app.services.gdrive_watcher import GDriveWatcher
    watcher = GDriveWatcher.get_instance()
    return watcher.get_status()

@router.post("/api/gdrive/watcher/toggle")
async def toggle_gdrive_watcher(enabled: bool = Body(..., embed=True)):
    from app.services.gdrive_watcher import GDriveWatcher
    watcher = GDriveWatcher.get_instance()
    watcher.is_enabled = enabled
    return {
        "success": True,
        "is_enabled": watcher.is_enabled,
        "message": f"Auto-Vigilante {'activado' if enabled else 'pausado'}."
    }

@router.post("/api/gdrive/watcher/config")
async def config_gdrive_watcher(interval_seconds: int = Body(..., embed=True)):
    from app.services.gdrive_watcher import GDriveWatcher
    watcher = GDriveWatcher.get_instance()
    if interval_seconds < 10:
        interval_seconds = 10
    watcher.interval_seconds = interval_seconds
    return {
        "success": True,
        "interval_seconds": watcher.interval_seconds,
        "message": f"Intervalo actualizado a {watcher.interval_seconds} segundos."
    }

@router.post("/api/guardar")
async def guardar_configuracion_api(payload: SaveTursoPayload):
    url = payload.database_url.strip()
    token = payload.auth_token.strip()

    if not url:
        raise HTTPException(status_code=400, detail="La URL de la base de datos no puede estar vacía.")

    # Actualizar variables de entorno en memoria
    os.environ["TURSO_DATABASE_URL"] = url
    os.environ["TURSO_AUTH_TOKEN"] = token

    # Persistir en archivo .env
    try:
        if not ENV_PATH.exists():
            ENV_PATH.touch()
        set_key(str(ENV_PATH), "TURSO_DATABASE_URL", url)
        set_key(str(ENV_PATH), "TURSO_AUTH_TOKEN", token)
    except Exception as e:
        pass

    return {
        "success": True,
        "message": "Configuración guardada correctamente en el sistema y en el archivo .env."
    }

@router.post("/api/test")
async def test_configuracion_api(payload: Optional[SaveTursoPayload] = None):
    url = payload.database_url if payload else os.getenv("TURSO_DATABASE_URL", "")
    token = payload.auth_token if payload else os.getenv("TURSO_AUTH_TOKEN", "")

    if not url or not token:
        raise HTTPException(status_code=400, detail="Debes proporcionar tanto la URL como el Token de Turso.")

    turso = TursoService(db_url=url, auth_token=token)
    try:
        res = await turso.test_connection()
        return {
            "success": True,
            "message": "¡Conexión exitosa con la base de datos de Turso Cloud!"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Fallo de conexión: {str(e)}")

@router.post("/api/push")
async def push_configuracion_api(
    payload: Optional[SaveTursoPayload] = None,
    db: Session = Depends(get_db)
):
    url = (payload.database_url if payload else None) or os.getenv("TURSO_DATABASE_URL", "")
    token = (payload.auth_token if payload else None) or os.getenv("TURSO_AUTH_TOKEN", "")

    if not url or not token:
        raise HTTPException(status_code=400, detail="Debes configurar y guardar la URL y el Token de Turso primero.")

    turso = TursoService(db_url=url, auth_token=token)
    try:
        res = await turso.push_all_to_turso(db)
        return {
            "success": True,
            "message": f"Subida completada con éxito. Se sincronizaron {res['despachos_subidos']} despachos y {res['items_subidos']} mercancías en Turso Cloud.",
            "data": res
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al subir a Turso: {str(e)}")

@router.post("/api/pull")
async def pull_configuracion_api(
    payload: Optional[SaveTursoPayload] = None,
    db: Session = Depends(get_db)
):
    url = (payload.database_url if payload else None) or os.getenv("TURSO_DATABASE_URL", "")
    token = (payload.auth_token if payload else None) or os.getenv("TURSO_AUTH_TOKEN", "")

    if not url or not token:
        raise HTTPException(status_code=400, detail="Debes configurar y guardar la URL y el Token de Turso primero.")

    turso = TursoService(db_url=url, auth_token=token)
    try:
        res = await turso.pull_all_from_turso(db)
        return {
            "success": True,
            "message": f"Descarga completada con éxito. Se importaron {res['despachos_descargados']} despachos y {res['items_descargados']} mercancías a esta PC.",
            "data": res
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al descargar desde Turso: {str(e)}")

@router.post("/api/notificaciones/guardar")
async def guardar_notificaciones_api(payload: SaveNotificationsPayload):
    token = (payload.telegram_bot_token or "").strip()
    chat_id = (payload.telegram_chat_id or "").strip()
    webhook = (payload.webhook_url or "").strip()
    enabled = payload.notifications_enabled if payload.notifications_enabled is not None else True

    # Actualizar en memoria
    os.environ["TELEGRAM_BOT_TOKEN"] = token
    os.environ["TELEGRAM_CHAT_ID"] = chat_id
    os.environ["WEBHOOK_URL"] = webhook
    os.environ["NOTIFICATIONS_ENABLED"] = "true" if enabled else "false"

    settings.TELEGRAM_BOT_TOKEN = token
    settings.TELEGRAM_CHAT_ID = chat_id
    settings.WEBHOOK_URL = webhook
    settings.NOTIFICATIONS_ENABLED = enabled

    # Persistir en .env
    try:
        if not ENV_PATH.exists():
            ENV_PATH.touch()
        set_key(str(ENV_PATH), "TELEGRAM_BOT_TOKEN", token)
        set_key(str(ENV_PATH), "TELEGRAM_CHAT_ID", chat_id)
        set_key(str(ENV_PATH), "WEBHOOK_URL", webhook)
        set_key(str(ENV_PATH), "NOTIFICATIONS_ENABLED", "true" if enabled else "false")
    except Exception as e:
        pass

    return {
        "success": True,
        "message": "Configuración de notificaciones guardada correctamente."
    }

@router.post("/api/notificaciones/test")
async def test_notificaciones_api(payload: Optional[SaveNotificationsPayload] = None):
    from app.services.notification_service import NotificationService
    token = payload.telegram_bot_token if payload and payload.telegram_bot_token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = payload.telegram_chat_id if payload and payload.telegram_chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")
    webhook = payload.webhook_url if payload and payload.webhook_url is not None else os.getenv("WEBHOOK_URL", "")

    service = NotificationService(telegram_token=token, telegram_chat_id=chat_id, webhook_url=webhook)
    result = service.send_test_notification()
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Error al enviar la notificación de prueba."))
    return result
