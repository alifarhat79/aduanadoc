"""
Página "Arancel": buscador de posiciones NCM, árbol por capítulos, uso en los despachos,
validación de los códigos declarados, simulador de tributos y versiones del arancel.

Las consultas solo LEEN los despachos. Importar, activar o eliminar versiones del arancel
requiere la sesión de programador (Configuración).
"""
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers.configuracion import is_admin_authenticated, require_admin_auth
from app.services import arancel_service
from app.templates_config import templates

router = APIRouter(prefix="/arancel", tags=["Arancel"])

MAX_PDF_BYTES = 60 * 1024 * 1024
MAX_EXCEL_BYTES = 25 * 1024 * 1024


def _sin_arancel():
    raise HTTPException(status_code=503, detail="El arancel todavía no está cargado en esta PC.")


@router.get("", response_class=HTMLResponse)
def arancel_view(request: Request, ncm: Optional[str] = Query(None)):
    return templates.TemplateResponse(
        request=request,
        name="arancel.html",
        context={"ncm_inicial": (ncm or "")[:40], "es_admin": is_admin_authenticated(request)},
    )


@router.get("/api/resumen")
def api_resumen(db: Session = Depends(get_db)):
    data = arancel_service.resumen(db)
    if data is None:
        _sin_arancel()
    return data


@router.get("/api/buscar")
def api_buscar(
    q: str = Query("", max_length=200),
    capitulo: str = Query("", max_length=2),
    lista: str = Query("", max_length=30),
    mios: bool = Query(False),
    db: Session = Depends(get_db),
):
    return arancel_service.buscar(db, q=q, capitulo=capitulo, lista=lista, solo_mios=mios)


@router.get("/api/ncm/{codigo}")
def api_ficha(codigo: str, db: Session = Depends(get_db)):
    data = arancel_service.ficha(db, codigo[:40])
    if data is None:
        raise HTTPException(status_code=404, detail="Código no encontrado en el arancel vigente.")
    return data


@router.get("/api/capitulo/{numero}")
def api_capitulo(numero: str, db: Session = Depends(get_db)):
    if not re.fullmatch(r"\d{1,2}", numero or ""):
        raise HTTPException(status_code=400, detail="Capítulo inválido.")
    data = arancel_service.capitulo(db, numero)
    if data is None:
        raise HTTPException(status_code=404, detail="Capítulo no encontrado.")
    return data


@router.get("/api/mios")
def api_mios(db: Session = Depends(get_db)):
    return arancel_service.mis_ncm(db)


@router.get("/api/validacion")
def api_validacion(db: Session = Depends(get_db)):
    return arancel_service.validacion_global(db)


@router.get("/api/versiones")
def api_versiones(db: Session = Depends(get_db)):
    return arancel_service.listar_versiones(db)


def _guardar_subida(archivo: UploadFile, destino: Path, extensiones, limite: int) -> None:
    nombre = archivo.filename or ""
    if Path(nombre).suffix.lower() not in extensiones:
        raise HTTPException(status_code=400, detail=f"Formato no admitido: {nombre or 'sin nombre'}.")
    total = 0
    with open(destino, "wb") as out:
        while True:
            bloque = archivo.file.read(1024 * 1024)
            if not bloque:
                break
            total += len(bloque)
            if total > limite:
                raise HTTPException(status_code=413, detail=f"El archivo {nombre} supera {limite // (1024 * 1024)} MB.")
            out.write(bloque)


@router.post("/api/importar")
def api_importar(
    request: Request,
    pdf: UploadFile = File(...),
    excel: Optional[UploadFile] = File(None),
    nombre: str = Form(..., max_length=200),
    fecha_vigencia: str = Form(...),
    db: Session = Depends(get_db),
):
    """Importa un PDF nuevo del MEF como versión INACTIVA y devuelve las diferencias con la activa."""
    require_admin_auth(request)
    try:
        datetime.strptime(fecha_vigencia, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Fecha de vigencia inválida (AAAA-MM-DD).")
    if not nombre.strip():
        raise HTTPException(status_code=400, detail="Indique un nombre para la versión.")

    carpeta = Path(tempfile.mkdtemp(prefix="arancel_import_"))
    try:
        ruta_pdf = carpeta / "arancel.pdf"
        _guardar_subida(pdf, ruta_pdf, {".pdf"}, MAX_PDF_BYTES)
        ruta_excel = None
        if excel is not None and excel.filename:
            ruta_excel = carpeta / ("aec" + Path(excel.filename).suffix.lower())
            _guardar_subida(excel, ruta_excel, {".xls", ".xlsx"}, MAX_EXCEL_BYTES)
        try:
            return arancel_service.importar_pdf_mef(
                db, ruta_pdf, nombre.strip(), fecha_vigencia, ruta_excel,
                nombre_pdf=Path(pdf.filename or "arancel.pdf").name,
                nombre_excel=Path(excel.filename).name if ruta_excel else "",
            )
        except (ValueError, RuntimeError) as e:
            db.rollback()
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=422, detail=f"No se pudo leer el archivo: {type(e).__name__}: {e}")
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


@router.post("/api/versiones/{version_id}/activar")
def api_activar(version_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin_auth(request)
    try:
        return arancel_service.activar_version(db, version_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/api/versiones/{version_id}")
def api_eliminar(version_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin_auth(request)
    try:
        arancel_service.eliminar_version(db, version_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}
