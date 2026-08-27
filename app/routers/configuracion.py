from fastapi import APIRouter, Depends, Request, Form, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
import os
import hmac
import hashlib
import time
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
AUTH_COOKIE_NAME = "aduanadoc_admin_session"

# --- UTILIDADES DE ENCRIPTACIÓN Y SESIÓN DE PROGRAMADOR ---
def create_admin_token() -> str:
    timestamp = str(int(time.time()))
    pwd = os.getenv("CONFIG_ADMIN_PASSWORD", getattr(settings, "CONFIG_ADMIN_PASSWORD", "Sohalia2012*@"))
    secret = getattr(settings, "SECRET_KEY", "aduanadoc_programmer_secret_key_2026")
    signature = hmac.new(
        secret.encode(),
        f"admin_session_{pwd}_{timestamp}".encode(),
        hashlib.sha256
    ).hexdigest()
    return f"{timestamp}.{signature}"

def verify_admin_token(token: str) -> bool:
    if not token or "." not in token:
        return False
    try:
        ts_str, signature = token.split(".", 1)
        ts = int(ts_str)
        # Token válido por 7 días
        if time.time() - ts > 7 * 86400:
            return False
        pwd = os.getenv("CONFIG_ADMIN_PASSWORD", getattr(settings, "CONFIG_ADMIN_PASSWORD", "Sohalia2012*@"))
        secret = getattr(settings, "SECRET_KEY", "aduanadoc_programmer_secret_key_2026")
        expected_sig = hmac.new(
            secret.encode(),
            f"admin_session_{pwd}_{ts_str}".encode(),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected_sig)
    except Exception:
        return False

def is_admin_authenticated(request: Request) -> bool:
    token = request.cookies.get(AUTH_COOKIE_NAME)
    return verify_admin_token(token)

def require_admin_auth(request: Request):
    if not is_admin_authenticated(request):
        raise HTTPException(status_code=401, detail="No autorizado. Acceso restringido para el programador.")


class LoginPayload(BaseModel):
    password: str

class ChangePasswordPayload(BaseModel):
    current_password: str
    new_password: str

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


# --- RUTAS DE AUTENTICACIÓN ---
@router.post("/login")
async def login_admin(payload: LoginPayload):
    current_pwd = os.getenv("CONFIG_ADMIN_PASSWORD", getattr(settings, "CONFIG_ADMIN_PASSWORD", "Sohalia2012*@"))
    if payload.password == current_pwd:
        token = create_admin_token()
        response = JSONResponse(content={"success": True, "message": "Acceso concedido"})
        response.set_cookie(
            key=AUTH_COOKIE_NAME,
            value=token,
            max_age=7 * 86400,
            httponly=True,
            samesite="lax"
        )
        return response
    raise HTTPException(status_code=401, detail="Contraseña incorrecta. Acceso denegado.")


@router.get("/logout")
@router.post("/logout")
async def logout_admin():
    response = RedirectResponse(url="/configuracion", status_code=303)
    response.delete_cookie(key=AUTH_COOKIE_NAME)
    return response


@router.post("/api/cambiar-password")
async def cambiar_password_api(payload: ChangePasswordPayload, request: Request):
    require_admin_auth(request)
    current_pwd = os.getenv("CONFIG_ADMIN_PASSWORD", getattr(settings, "CONFIG_ADMIN_PASSWORD", "Sohalia2012*@"))
    if payload.current_password != current_pwd:
        raise HTTPException(status_code=400, detail="La contraseña actual no es correcta.")
    
    if len(payload.new_password.strip()) < 6:
        raise HTTPException(status_code=400, detail="La nueva contraseña debe tener al menos 6 caracteres.")
    
    new_pwd = payload.new_password.strip()
    os.environ["CONFIG_ADMIN_PASSWORD"] = new_pwd
    settings.CONFIG_ADMIN_PASSWORD = new_pwd
    try:
        set_key(str(ENV_PATH), "CONFIG_ADMIN_PASSWORD", new_pwd)
    except Exception:
        pass
    
    token = create_admin_token()
    response = JSONResponse(content={"success": True, "message": "Contraseña de programador actualizada exitosamente."})
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=7 * 86400,
        httponly=True,
        samesite="lax"
    )
    return response


# --- PANTALLA PRINCIPAL (CON CANDADO) ---
@router.get("", response_class=HTMLResponse)
async def configuracion_view(request: Request, db: Session = Depends(get_db)):
    if not is_admin_authenticated(request):
        return templates.TemplateResponse(
            request=request,
            name="config_login.html",
            context={}
        )

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
            "es_programador_autenticado": True
        }
    )


# --- APIS DE CONFIGURACIÓN PROTEGIDAS ---
@router.post("/api/gdrive/scan")
async def scan_gdrive_api(
    payload: Optional[GDriveScanPayload] = None,
    db: Session = Depends(get_db),
    request: Request = None
):
    require_admin_auth(request)
    folder_id = payload.folder_id if payload and payload.folder_id else None
    propietario = payload.propietario if payload and payload.propietario else "Google Drive"

    service = GoogleDriveService(folder_id=folder_id)
    try:
        results = await service.scan_and_process(db, allow_duplicate=False, propietario=propietario)
        procesados = results.get("procesados", 0)
        duplicados = results.get("duplicados", 0)
        errores = results.get("errores", 0)

        return {
            "success": True,
            "message": f"Escaneo completado: {procesados} nuevos despachos procesados, {duplicados} omitidos (ya existentes), {errores} con error.",
            "data": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error durante el escaneo de Google Drive: {str(e)}")


@router.post("/api/gdrive/scan-local")
async def scan_gdrive_local_api(
    payload: GDriveLocalScanPayload,
    db: Session = Depends(get_db),
    request: Request = None
):
    require_admin_auth(request)
    if not payload.local_path:
        raise HTTPException(status_code=400, detail="Debes proporcionar una ruta local válida.")

    service = GoogleDriveService()
    try:
        results = await service.scan_local_folder(db, payload.local_path, allow_duplicate=False, propietario=payload.propietario)
        procesados = results.get("procesados", 0)
        duplicados = results.get("duplicados", 0)
        errores = results.get("errores", 0)

        return {
            "success": True,
            "message": f"Escaneo de carpeta local completado: {procesados} nuevos despachos procesados, {duplicados} omitidos, {errores} errores.",
            "data": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al escanear carpeta local: {str(e)}")


@router.get("/api/gdrive/watcher/status")
async def watcher_status_api(request: Request = None):
    from app.services.gdrive_watcher import GDriveWatcher
    watcher = GDriveWatcher.get_instance()
    return watcher.get_status()


@router.post("/api/gdrive/watcher/toggle")
async def watcher_toggle_api(payload: dict = Body(...), request: Request = None):
    require_admin_auth(request)
    from app.services.gdrive_watcher import GDriveWatcher
    enabled = payload.get("enabled", True)
    watcher = GDriveWatcher.get_instance()
    watcher.set_enabled(enabled)
    return {"success": True, "is_enabled": watcher.is_enabled}


@router.post("/api/gdrive/watcher/config")
async def watcher_config_api(payload: dict = Body(...), request: Request = None):
    require_admin_auth(request)
    from app.services.gdrive_watcher import GDriveWatcher
    interval = payload.get("interval_seconds", 300)
    watcher = GDriveWatcher.get_instance()
    watcher.set_interval(interval)
    return {"success": True, "interval_seconds": watcher.interval_seconds}


@router.post("/api/guardar")
async def guardar_configuracion_api(payload: SaveTursoPayload, request: Request = None):
    require_admin_auth(request)
    url = payload.database_url.strip()
    token = payload.auth_token.strip()

    if not url or not token:
        raise HTTPException(status_code=400, detail="URL y Token son obligatorios.")

    os.environ["TURSO_DATABASE_URL"] = url
    os.environ["TURSO_AUTH_TOKEN"] = token
    settings.TURSO_DATABASE_URL = url
    settings.TURSO_AUTH_TOKEN = token

    try:
        if not ENV_PATH.exists():
            ENV_PATH.touch()
        set_key(str(ENV_PATH), "TURSO_DATABASE_URL", url)
        set_key(str(ENV_PATH), "TURSO_AUTH_TOKEN", token)
    except Exception:
        pass

    return {
        "success": True,
        "message": "Configuración guardada correctamente en el sistema y en el archivo .env."
    }


@router.post("/api/test")
async def test_configuracion_api(payload: Optional[SaveTursoPayload] = None, request: Request = None):
    require_admin_auth(request)
    url = payload.database_url if payload else os.getenv("TURSO_DATABASE_URL", "")
    token = payload.auth_token if payload else os.getenv("TURSO_AUTH_TOKEN", "")

    if not url or not token:
        raise HTTPException(status_code=400, detail="Debes proporcionar tanto la URL como el Token de Turso.")

    turso = TursoService(db_url=url, auth_token=token)
    try:
        await turso.test_connection()
        return {
            "success": True,
            "message": "¡Conexión exitosa con la base de datos de Turso Cloud!"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Fallo de conexión: {str(e)}")


@router.post("/api/push")
async def push_configuracion_api(
    payload: Optional[SaveTursoPayload] = None,
    db: Session = Depends(get_db),
    request: Request = None
):
    require_admin_auth(request)
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
    db: Session = Depends(get_db),
    request: Request = None
):
    require_admin_auth(request)
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
async def guardar_notificaciones_api(payload: SaveNotificationsPayload, request: Request = None):
    require_admin_auth(request)
    token = (payload.telegram_bot_token or "").strip()
    chat_id = (payload.telegram_chat_id or "").strip()
    webhook = (payload.webhook_url or "").strip()
    enabled = payload.notifications_enabled if payload.notifications_enabled is not None else True

    os.environ["TELEGRAM_BOT_TOKEN"] = token
    os.environ["TELEGRAM_CHAT_ID"] = chat_id
    os.environ["WEBHOOK_URL"] = webhook
    os.environ["NOTIFICATIONS_ENABLED"] = "true" if enabled else "false"

    settings.TELEGRAM_BOT_TOKEN = token
    settings.TELEGRAM_CHAT_ID = chat_id
    settings.WEBHOOK_URL = webhook
    settings.NOTIFICATIONS_ENABLED = enabled

    try:
        if not ENV_PATH.exists():
            ENV_PATH.touch()
        set_key(str(ENV_PATH), "TELEGRAM_BOT_TOKEN", token)
        set_key(str(ENV_PATH), "TELEGRAM_CHAT_ID", chat_id)
        set_key(str(ENV_PATH), "WEBHOOK_URL", webhook)
        set_key(str(ENV_PATH), "NOTIFICATIONS_ENABLED", "true" if enabled else "false")

        # Actualizar app/config.py para que viaje automáticamente por Git a todas las PCs
        config_file = BASE_DIR / "app" / "config.py"
        if config_file.exists():
            import re
            content = config_file.read_text(encoding="utf-8")
            content = re.sub(r'TELEGRAM_BOT_TOKEN:\s*str\s*=\s*".*?"', f'TELEGRAM_BOT_TOKEN: str = "{token}"', content)
            content = re.sub(r'TELEGRAM_CHAT_ID:\s*str\s*=\s*".*?"', f'TELEGRAM_CHAT_ID: str = "{chat_id}"', content)
            config_file.write_text(content, encoding="utf-8")

            # Intentar sincronizar con Git
            try:
                import subprocess
                subprocess.run(["git", "add", "app/config.py"], cwd=str(BASE_DIR), capture_output=True, timeout=5)
                subprocess.run(["git", "commit", "-m", "chore: sincronizar credenciales Telegram"], cwd=str(BASE_DIR), capture_output=True, timeout=5)
                subprocess.run(["git", "push", "origin", "main"], cwd=str(BASE_DIR), capture_output=True, timeout=10)
            except Exception:
                pass
    except Exception:
        pass

    return {
        "success": True,
        "message": "Configuración de notificaciones guardada y sincronizada para todas las PCs."
    }


@router.post("/api/notificaciones/test")
async def test_notificaciones_api(payload: Optional[SaveNotificationsPayload] = None, request: Request = None):
    require_admin_auth(request)
    from app.services.notification_service import NotificationService
    token = payload.telegram_bot_token if payload and payload.telegram_bot_token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = payload.telegram_chat_id if payload and payload.telegram_chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")
    webhook = payload.webhook_url if payload and payload.webhook_url is not None else os.getenv("WEBHOOK_URL", "")

    service = NotificationService(telegram_token=token, telegram_chat_id=chat_id, webhook_url=webhook)
    result = service.send_test_notification()
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Error al enviar la notificación de prueba."))
    return result
