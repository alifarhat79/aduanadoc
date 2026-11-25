from fastapi import APIRouter, HTTPException, BackgroundTasks, Body, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional

from app.services.backup_service import BackupService
from app.services.updater_service import UpdaterService
from app.config import settings
from app.routers.configuracion import require_admin_auth

router = APIRouter(tags=["Backup & Actualizaciones"])

class PublishUpdateRequest(BaseModel):
    version: str
    changelog: Optional[str] = ""

@router.post("/api/backup/crear")
async def create_backup_api(reason: Optional[str] = "MANUAL_WEB"):
    """Genera una copia de seguridad ZIP completa del sistema."""
    service = BackupService()
    result = service.create_system_backup(reason=reason)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Error al crear el respaldo"))
    return result

@router.get("/api/backup/listar")
async def list_backups_api(request: Request):
    """Retorna la lista de respaldos ZIP disponibles (solo programador)."""
    require_admin_auth(request)
    service = BackupService()
    return {"backups": service.list_backups()}

@router.get("/api/backup/download/{filename}")
async def download_backup_api(filename: str, request: Request):
    """Descarga un archivo ZIP de respaldo (contiene TODA la base de datos: solo programador)."""
    require_admin_auth(request)
    service = BackupService()
    target_path = service.get_backup_path(filename)
    if not target_path:
        raise HTTPException(status_code=404, detail="Archivo de respaldo no encontrado.")

    return FileResponse(
        path=target_path,
        media_type="application/zip",
        filename=filename,
        content_disposition_type="attachment"
    )

@router.delete("/api/backup/{filename}")
async def delete_backup_api(filename: str, request: Request):
    """Elimina un archivo de respaldo del servidor (solo programador)."""
    require_admin_auth(request)
    service = BackupService()
    if service.delete_backup(filename):
        return {"success": True, "message": f"Respaldo {filename} eliminado."}
    raise HTTPException(status_code=404, detail="Archivo de respaldo no encontrado.")

@router.get("/api/updates/check")
async def check_updates_api():
    """Comprueba si existe una versión más nueva en Google Drive / Red."""
    updater = UpdaterService()
    return updater.check_for_updates()

@router.post("/api/updates/publish")
async def publish_update_api(req: PublishUpdateRequest, request: Request):
    """Para el PROGRAMADOR: Empaqueta la versión actual y publica el paquete de actualización."""
    require_admin_auth(request)
    updater = UpdaterService()
    result = updater.publish_update(new_version=req.version, changelog=req.changelog or "")
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Error al publicar la actualización"))
    return result

@router.post("/api/updates/apply")
async def apply_update_api():
    """Para OTRAS PCs: Descarga y aplica la actualización más reciente preservando la base de datos."""
    updater = UpdaterService()
    result = updater.apply_update()
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Error al aplicar la actualización"))
    return result

@router.get("/api/updates/git/status")
async def git_status_api():
    """Retorna el estado actual del repositorio Git si está disponible."""
    updater = UpdaterService()
    return updater.get_git_status()

@router.post("/api/updates/git/pull")
async def git_pull_api():
    """Ejecuta git pull para sincronizar cambios remotos desde GitHub/Git."""
    updater = UpdaterService()
    result = updater.git_pull()
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Error al ejecutar git pull"))
    return result

@router.post("/api/updates/git/push")
async def git_push_api(request: Request, payload: dict = Body(default={})):
    """Ejecuta git push para enviar cambios locales a GitHub (repositorio PÚBLICO): solo programador."""
    require_admin_auth(request)
    commit_msg = payload.get("commit_msg", "chore: auto-sync updates")
    updater = UpdaterService()
    result = updater.git_push(commit_msg=commit_msg)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Error al ejecutar git push"))
    return result

@router.post("/api/updates/git/connect")
async def git_connect_api(request: Request, payload: dict = Body(default={})):
    """Vincula esta PC con un repositorio (y hace reset al código remoto): solo programador."""
    require_admin_auth(request)
    repo_url = payload.get("repo_url", "https://github.com/alifarhat79/aduanadoc.git")
    updater = UpdaterService()
    result = updater.connect_git_repo(repo_url=repo_url)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Error al vincular repositorio Git"))
    return result
