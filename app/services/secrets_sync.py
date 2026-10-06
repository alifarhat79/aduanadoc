"""
Sincronización de claves (tokens y contraseñas) desde Google Drive.

Las claves NO deben vivir en el código (el repositorio de GitHub es público).
Cada PC, al arrancar, descarga el archivo privado `aduanadoc_claves.env` desde
Google Drive usando la cuenta de servicio (service_account.json) y lo aplica:

    Prioridad:  archivo de Drive  >  .env local  >  valores por defecto de config.py

Los valores descargados se guardan en el `.env` local, así si Drive no responde
la PC sigue funcionando con las últimas claves conocidas.

Además envía un reporte de arranque por Telegram (nombre de PC, versión, estado
de Drive y de las claves) para poder supervisar todas las PCs a distancia.
"""
import io
import os
import sys
import json
import socket
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

from app.config import settings, BASE_DIR

logger = logging.getLogger(__name__)

SECRETS_FILE_NAME = "aduanadoc_claves.env"
ENV_PATH = BASE_DIR / ".env"
REPORT_STAMP = Path(settings.DATA_DIR) / ".ultimo_reporte_arranque.json"
REPORT_MIN_INTERVAL = timedelta(minutes=10)

# Únicas claves que se aceptan desde el archivo de Drive
ALLOWED_KEYS = (
    "TURSO_DATABASE_URL",
    "TURSO_AUTH_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "WEBHOOK_URL",
    "CONFIG_ADMIN_PASSWORD",
    "SECRET_KEY",
    "STARTUP_REPORT_ENABLED",
)


def _is_test_run() -> bool:
    return "pytest" in sys.modules or os.getenv("ADUANADOC_SKIP_SECRETS_SYNC") == "1"


def _credentials_path() -> Path:
    cred = Path(os.getenv("GDRIVE_CREDENTIALS_FILE", settings.GDRIVE_CREDENTIALS_FILE))
    return cred if cred.is_absolute() else (BASE_DIR / cred)


def _keys_in_local_env() -> list:
    """Qué claves sensibles tiene definidas el .env local (sin revelar valores)."""
    if not ENV_PATH.exists():
        return []
    try:
        from dotenv import dotenv_values
        vals = dotenv_values(ENV_PATH)
        return [k for k in ALLOWED_KEYS if (vals.get(k) or "").strip()]
    except Exception:
        return []


def _apply_value(key: str, value: str) -> bool:
    """Aplica una clave en memoria (os.environ + settings) y la persiste en .env. Devuelve True si cambió."""
    from dotenv import dotenv_values, set_key

    current = (dotenv_values(ENV_PATH).get(key) if ENV_PATH.exists() else None) or ""
    changed = current.strip().strip("'\"") != value

    os.environ[key] = value
    if hasattr(settings, key):
        try:
            field_type = type(getattr(settings, key))
            setattr(settings, key, field_type(value) if field_type in (int, float) else value)
        except Exception:
            setattr(settings, key, value)

    if changed:
        if not ENV_PATH.exists():
            ENV_PATH.touch()
        set_key(str(ENV_PATH), key, value)
    return changed


def sync_secrets_from_drive() -> Dict[str, Any]:
    """
    Descarga `aduanadoc_claves.env` desde Google Drive y aplica las claves.
    Nunca lanza excepciones: devuelve un diccionario con el estado.
    """
    result: Dict[str, Any] = {
        "estado": "sin_intentar",
        "detalle": "",
        "claves_aplicadas": [],
        "claves_actualizadas": [],
        "env_local_tenia": _keys_in_local_env(),
        "service_account": _credentials_path().exists(),
    }

    if _is_test_run():
        result.update(estado="omitido", detalle="Ejecución de pruebas")
        return result

    if not result["service_account"]:
        result.update(estado="sin_service_account", detalle=f"No existe {_credentials_path().name}")
        return result

    try:
        from app.services.gdrive_service import GoogleDriveService
        if not GoogleDriveService.is_api_available():
            result.update(estado="sin_libreria_google", detalle="Falta google-api-python-client")
            return result

        drive = GoogleDriveService().get_drive_service()
        found = drive.files().list(
            q=f"name = '{SECRETS_FILE_NAME}' and trashed = false",
            fields="files(id, name, modifiedTime)",
            orderBy="modifiedTime desc",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=5,
        ).execute().get("files", [])

        if not found:
            result.update(
                estado="no_compartido",
                detalle=f"'{SECRETS_FILE_NAME}' no está compartido con la cuenta de servicio",
            )
            return result

        content = drive.files().get_media(fileId=found[0]["id"]).execute()
        text = content.decode("utf-8-sig") if isinstance(content, bytes) else str(content)

        from dotenv import dotenv_values
        values = dotenv_values(stream=io.StringIO(text))

        for key in ALLOWED_KEYS:
            val = (values.get(key) or "").strip().strip("'\"")
            if not val:
                continue
            if _apply_value(key, val):
                result["claves_actualizadas"].append(key)
            result["claves_aplicadas"].append(key)

        result.update(estado="ok", detalle=f"Archivo de Drive modificado {found[0].get('modifiedTime', '')}")
        logger.info(
            f"[Claves] {len(result['claves_aplicadas'])} claves aplicadas desde Drive "
            f"({len(result['claves_actualizadas'])} actualizadas)."
        )
    except Exception as e:
        result.update(estado="error", detalle=str(e)[:200])
        logger.warning(f"[Claves] No se pudieron sincronizar las claves desde Drive: {e}")

    return result


def _version_actual() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(BASE_DIR), "log", "-1", "--format=%h %cd", "--date=format:%d/%m/%Y"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    try:
        data = json.loads((Path(settings.DATA_DIR) / "installed_version.json").read_text(encoding="utf-8"))
        return data.get("commit_hash", settings.APP_VERSION)
    except Exception:
        return settings.APP_VERSION


def _contar_despachos() -> str:
    try:
        from app.database import SessionLocal
        from app.models import Despacho
        db = SessionLocal()
        try:
            return str(db.query(Despacho).count())
        finally:
            db.close()
    except Exception:
        return "?"


def send_startup_report(sync_result: Dict[str, Any], origen: str = "Servidor web") -> Dict[str, Any]:
    """Envía por Telegram el estado de esta PC al arrancar (máximo 1 cada 10 minutos)."""
    if _is_test_run():
        return {"success": False, "error": "omitido en pruebas"}
    if os.getenv("STARTUP_REPORT_ENABLED", "true").strip().lower() in ("0", "false", "no"):
        return {"success": False, "error": "reporte desactivado"}

    pc = socket.gethostname()
    limpieza = sync_result.get("limpieza") or {}
    try:
        if REPORT_STAMP.exists() and not limpieza.get("eliminados"):
            last = json.loads(REPORT_STAMP.read_text(encoding="utf-8"))
            last_at = datetime.fromisoformat(last.get("at", "2000-01-01T00:00:00"))
            if datetime.now() - last_at < REPORT_MIN_INTERVAL and last.get("estado") == sync_result.get("estado"):
                return {"success": False, "error": "reporte reciente, omitido"}
    except Exception:
        pass

    estado = sync_result.get("estado")
    iconos = {
        "ok": "✅ OK (claves desde Drive)",
        "no_compartido": "⚠️ Archivo de claves NO compartido con la cuenta del sistema",
        "sin_service_account": "❌ Falta service_account.json (no lee Google Drive)",
        "sin_libreria_google": "❌ Faltan librerías de Google",
        "error": f"❌ Error: {sync_result.get('detalle', '')}",
    }
    env_local = sync_result.get("env_local_tenia") or []
    texto = (
        f"🖥️ <b>AduanaDoc arrancó en {pc}</b>\n"
        f"• Origen: {origen}\n"
        f"• Usuario: {os.getenv('USERNAME', '?')}\n"
        f"• Carpeta: <code>{BASE_DIR}</code>\n"
        f"• Versión: {_version_actual()}\n"
        f"• Despachos en la base: {_contar_despachos()} (en Turso: {_contar_turso()})\n"
        f"• service_account.json: {'sí' if sync_result.get('service_account') else 'NO'}\n"
        f"• .env propio con claves: {', '.join(env_local) if env_local else 'NO'}\n"
        f"• Claves Drive: {iconos.get(estado, estado)}"
    )
    if limpieza.get("eliminados"):
        texto += f"\n🧹 Duplicados exactos eliminados: {len(limpieza['eliminados'])} ({', '.join(limpieza['eliminados'][:5])})"
    if limpieza.get("omitidos"):
        texto += f"\n⚠️ Duplicados NO eliminados (usados en planillas): {', '.join(limpieza['omitidos'][:5])}"
    turso_res = sync_result.get("turso") or {}
    if "subidos" in turso_res:
        texto += f"\n☁️ Sync Turso al arrancar: {turso_res['subidos']} subidos, {turso_res['bajados']} bajados"
    if limpieza.get("error"):
        texto += f"\n⚠️ Error en limpieza de duplicados: {limpieza['error']}"
    if "Google Drive" in str(BASE_DIR) or "My Drive" in str(BASE_DIR) or "Mi unidad" in str(BASE_DIR):
        texto += "\n⚠️ El programa corre DENTRO de Google Drive: conviene moverlo a C:\\aduanadoc"

    try:
        from app.services.notification_service import NotificationService
        res = NotificationService().send_telegram_message(texto)
        if res.get("success"):
            REPORT_STAMP.parent.mkdir(parents=True, exist_ok=True)
            REPORT_STAMP.write_text(
                json.dumps({"at": datetime.now().isoformat(), "estado": estado}), encoding="utf-8"
            )
        return res
    except Exception as e:
        logger.warning(f"[Reporte] No se pudo enviar el reporte de arranque: {e}")
        return {"success": False, "error": str(e)}


def _contar_turso() -> str:
    try:
        import asyncio
        from app.services.turso_service import TursoService
        t = TursoService()
        if not t.is_configured():
            return "no configurado"
        res = asyncio.run(asyncio.wait_for(t.execute_raw([{"sql": "SELECT COUNT(*) FROM despachos;"}]), timeout=10))
        val = res["results"][0]["response"]["result"]["rows"][0][0]
        return str(val.get("value") if isinstance(val, dict) else val)
    except Exception:
        return "?"


def startup_secrets_and_report(origen: str = "Servidor web", sync_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Atajo para el arranque: limpia duplicados exactos, sincroniza claves y envía el reporte."""
    limpieza: Dict[str, Any] = {}
    if not _is_test_run():
        try:
            from app.services.maintenance import remove_exact_duplicates
            limpieza = remove_exact_duplicates()
        except Exception as e:
            limpieza = {"error": str(e)[:200]}
    result = sync_result if sync_result is not None else sync_secrets_from_drive()
    result["limpieza"] = limpieza
    if not _is_test_run():
        result["turso"] = _sincronizar_turso()
        if result["turso"].get("error"):
            limpieza.setdefault("error", f"Sync Turso: {result['turso']['error']}")
    try:
        result["reporte"] = send_startup_report(result, origen=origen)
    except Exception as e:
        result["reporte"] = {"success": False, "error": str(e)}
    return result


def _sincronizar_turso() -> Dict[str, Any]:
    """Sube a Turso los despachos locales que falten y baja los nuevos de otras PCs."""
    try:
        import asyncio
        from app.database import SessionLocal
        from app.services.turso_service import TursoService
        t = TursoService()
        if not t.is_configured():
            return {"omitido": "Turso no configurado"}

        async def _run():
            db = SessionLocal()
            try:
                subida = await t.push_delta_to_turso(db)
                bajada = await t.pull_despachos_quick(db)
                return {"subidos": subida.get("despachos_subidos", 0), "bajados": bajada.get("despachos_nuevos", 0)}
            finally:
                db.close()

        res = asyncio.run(asyncio.wait_for(_run(), timeout=90))
        logger.info(f"[Arranque] Sync Turso: {res}")
        return res
    except Exception as e:
        logger.warning(f"[Arranque] Sync Turso falló: {e}")
        return {"error": str(e)[:150]}
