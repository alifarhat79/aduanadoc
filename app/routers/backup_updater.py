from fastapi import APIRouter, HTTPException, BackgroundTasks, Body
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional

from app.services.backup_service import BackupService
from app.services.updater_service import UpdaterService
from app.config import settings

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
async def list_backups_api():
    """Retorna la lista de respaldos ZIP disponibles."""
    service = BackupService()
    return {"backups": service.list_backups()}

@router.get("/api/backup/download/{filename}")
async def download_backup_api(filename: str):
    """Descarga un archivo ZIP de respaldo directamente al navegador."""
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
async def delete_backup_api(filename: str):
    """Elimina un archivo de respaldo del servidor."""
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
async def publish_update_api(req: PublishUpdateRequest):
    """Para el PROGRAMADOR: Empaqueta la versión actual y publica el paquete de actualización."""
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

@router.post("/api/updates/git/connect")
async def git_connect_api(payload: dict = Body(default={})):
    """Vincula y conecta esta computadora con el repositorio GitHub oficial (o URL personalizada)."""
    repo_url = payload.get("repo_url", "https://github.com/alifarhat79/aduanadoc.git")
    updater = UpdaterService()
    result = updater.connect_git_repo(repo_url=repo_url)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Error al vincular repositorio Git"))
    return result
