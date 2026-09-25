from fastapi import APIRouter, Depends, Request, Query, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any

from app.database import get_db
from app.templates_config import templates
from app.services.brand_normalizer import (
    get_brands_summary,
    detect_brand_clusters,
    unify_brands,
    rename_single_brand,
    get_all_rules,
    create_manual_rule,
    delete_rule
)

router = APIRouter(prefix="/marcas", tags=["Marcas"])


@router.get("", response_class=HTMLResponse)
async def marcas_view(
    request: Request,
    db: Session = Depends(get_db),
    q: Optional[str] = Query(None)
):
    """Vista principal para la gestión, unificación y corrección de marcas."""
    marcas = get_brands_summary(db, q=q)
    clusters = detect_brand_clusters(db)
    rules = get_all_rules(db)

    total_marcas = len(marcas) if not q else len(get_brands_summary(db))
    total_items = sum(m["total_items"] for m in marcas)

    return templates.TemplateResponse(
        request=request,
        name="marcas.html",
        context={
            "marcas": marcas,
            "q": q or "",
            "clusters": clusters,
            "rules": rules,
            "total_marcas": total_marcas,
            "total_items": total_items,
            "total_sugerencias": len(clusters),
            "total_reglas": len(rules)
        }
    )


@router.get("/api/marcas")
async def api_marcas_list(
    db: Session = Depends(get_db),
    q: Optional[str] = Query(None)
):
    """Endpoint JSON que lista todas las marcas con filtros en tiempo real."""
    return get_brands_summary(db, q=q)


@router.get("/api/sugerencias")
async def api_sugerencias_list(db: Session = Depends(get_db)):
    """Endpoint JSON que detecta y devuelve agrupaciones de marcas similares."""
    return detect_brand_clusters(db)


@router.post("/api/unificar")
async def api_unificar_marcas(
    db: Session = Depends(get_db),
    payload: Dict[str, Any] = Body(...)
):
    """
    Unifica varias marcas en un nombre definitivo.
    Payload:
    {
        "marcas_origen": ["LIF POD", "LIFE POD"],
        "marca_destino": "LIFEPOD",
        "crear_regla": true
    }
    """
    marcas_origen = payload.get("marcas_origen", [])
    marca_destino = payload.get("marca_destino", "")
    crear_regla = payload.get("crear_regla", True)

    if not marcas_origen or not marca_destino:
        raise HTTPException(
            status_code=400,
            detail="Se requieren 'marcas_origen' (lista) y 'marca_destino' (texto no vacío)."
        )

    try:
        res = unify_brands(
            db=db,
            marcas_origen=marcas_origen,
            marca_destino=marca_destino,
            crear_regla=crear_regla
        )
        return JSONResponse(content=res)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/renombrar")
async def api_renombrar_marca(
    db: Session = Depends(get_db),
    payload: Dict[str, Any] = Body(...)
):
    """
    Renombra una marca individual en toda la base de datos.
    Payload:
    {
        "marca_actual": "XIOMI",
        "nueva_marca": "XIAOMI",
        "crear_regla": true
    }
    """
    marca_actual = payload.get("marca_actual", "")
    nueva_marca = payload.get("nueva_marca", "")
    crear_regla = payload.get("crear_regla", True)

    if not marca_actual or not nueva_marca:
        raise HTTPException(
            status_code=400,
            detail="Se requieren 'marca_actual' y 'nueva_marca'."
        )

    try:
        res = rename_single_brand(
            db=db,
            marca_actual=marca_actual,
            nueva_marca=nueva_marca,
            crear_regla=crear_regla
        )
        return JSONResponse(content=res)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/reglas")
async def api_get_reglas(db: Session = Depends(get_db)):
    """Lista las reglas de normalización automáticas registradas."""
    return get_all_rules(db)


@router.post("/api/reglas")
async def api_create_regla(
    db: Session = Depends(get_db),
    payload: Dict[str, Any] = Body(...)
):
    """Crea una regla manual en el diccionario de marcas."""
    patron_origen = payload.get("patron_origen", "")
    marca_destino = payload.get("marca_destino", "")

    if not patron_origen or not marca_destino:
        raise HTTPException(status_code=400, detail="El patrón de origen y marca destino son requeridos.")

    try:
        res = create_manual_rule(db, patron_origen, marca_destino)
        return JSONResponse(content=res)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/reglas/{rule_id}")
async def api_delete_regla(
    rule_id: int,
    db: Session = Depends(get_db)
):
    """Elimina una regla de normalización."""
    success = delete_rule(db, rule_id)
    if not success:
        raise HTTPException(status_code=404, detail="Regla no encontrada.")
    return JSONResponse(content={"success": True, "message": "Regla eliminada correctamente."})
