from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel
import os

from app.database import get_db
from app.services.turso_service import TursoService
from app.config import settings

router = APIRouter(prefix="/turso", tags=["Turso Cloud"])

class TursoConfigPayload(BaseModel):
    database_url: str
    auth_token: str

@router.get("/api/config")
async def get_turso_config():
    db_url = os.getenv("TURSO_DATABASE_URL", "")
    token = os.getenv("TURSO_AUTH_TOKEN", "")
    masked_token = f"{token[:6]}...{token[-4:]}" if len(token) > 12 else ("Configurado" if token else "")
    return {
        "database_url": db_url,
        "is_configured": bool(db_url and token),
        "masked_token": masked_token
    }

@router.post("/api/test")
async def test_turso_endpoint(payload: Optional[TursoConfigPayload] = None):
    url = payload.database_url if payload else None
    token = payload.auth_token if payload else None
    turso = TursoService(db_url=url, auth_token=token)
    try:
        res = await turso.test_connection()
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/api/push")
async def push_to_turso_endpoint(
    payload: Optional[TursoConfigPayload] = None,
    db: Session = Depends(get_db)
):
    """Sube todos los datos locales a Turso en la nube."""
    url = payload.database_url if payload else None
    token = payload.auth_token if payload else None
    turso = TursoService(db_url=url, auth_token=token)

    # Si se pasaron credenciales, guardarlas en el entorno de la sesión
    if url and token:
        os.environ["TURSO_DATABASE_URL"] = url
        os.environ["TURSO_AUTH_TOKEN"] = token

    try:
        res = await turso.push_all_to_turso(db)
        return {
            "success": True,
            "message": f"Sincronización a Turso completada con éxito. Se subieron {res['despachos_subidos']} despachos y {res['items_subidos']} mercancías.",
            "data": res
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al subir a Turso: {str(e)}")

@router.post("/api/pull")
async def pull_from_turso_endpoint(
    payload: Optional[TursoConfigPayload] = None,
    db: Session = Depends(get_db)
):
    """Descarga todos los datos desde Turso Cloud hacia la PC local."""
    url = payload.database_url if payload else None
    token = payload.auth_token if payload else None
    turso = TursoService(db_url=url, auth_token=token)

    if url and token:
        os.environ["TURSO_DATABASE_URL"] = url
        os.environ["TURSO_AUTH_TOKEN"] = token

    try:
        res = await turso.pull_all_from_turso(db)
        return {
            "success": True,
            "message": f"Descarga desde Turso completada con éxito. Se sincronizaron {res['despachos_descargados']} despachos y {res['items_descargados']} mercancías en esta PC.",
            "data": res
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al descargar desde Turso: {str(e)}")
