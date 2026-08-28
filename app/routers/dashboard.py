from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import date

from app.database import get_db
from app.models import Despacho, DespachoItem, ProcessingLog
from app.templates_config import templates

router = APIRouter(tags=["Dashboard"])

@router.get("/", response_class=HTMLResponse)
async def dashboard_view(request: Request, db: Session = Depends(get_db)):
    """Vista principal del Dashboard empresarial."""
    # KPIs de Período y Empresas
    hoy = date.today()
    primer_dia_anio = date(hoy.year, 1, 1)
    primer_dia_mes = hoy.replace(day=1)

    despachos_este_anio = db.query(func.count(Despacho.id)).filter(Despacho.fecha_despacho >= primer_dia_anio).scalar() or 0
    despachos_este_mes = db.query(func.count(Despacho.id)).filter(Despacho.fecha_despacho >= primer_dia_mes).scalar() or 0

    importadores_set = set(r[0] for r in db.query(Despacho.importador_nombre).filter(Despacho.importador_nombre.isnot(None), Despacho.importador_nombre != "").all() if r[0])
    exportadores_set = set(r[0] for r in db.query(Despacho.exportador_nombre).filter(Despacho.exportador_nombre.isnot(None), Despacho.exportador_nombre != "").all() if r[0])
    total_empresas = len(importadores_set.union(exportadores_set))
    total_importadores = len(importadores_set)
    total_exportadores = len(exportadores_set)

    total_despachos = db.query(func.count(Despacho.id)).scalar() or 0
    total_fob = db.query(func.sum(Despacho.valor_fob)).scalar() or 0.0
    
    # Calcular CIF efectivo
    total_cif = db.query(func.sum(Despacho.valor_cif)).scalar() or 0.0
    if total_cif == 0.0 and total_fob > 0.0:
        total_flete = db.query(func.sum(Despacho.valor_flete)).scalar() or 0.0
        total_seguro = db.query(func.sum(Despacho.valor_seguro)).scalar() or 0.0
        total_cif = total_fob + total_flete + total_seguro

    total_impuestos = db.query(func.sum(Despacho.total_general)).scalar() or 0.0
    total_peso = db.query(func.sum(Despacho.peso_neto)).scalar() or 0.0

    pendientes_revision = db.query(func.count(Despacho.id)).filter(Despacho.estado_procesamiento == "REVISAR").scalar() or 0
    confirmados = db.query(func.count(Despacho.id)).filter(Despacho.estado_procesamiento == "CONFIRMADO").scalar() or 0
    procesados = db.query(func.count(Despacho.id)).filter(Despacho.estado_procesamiento == "PROCESADO").scalar() or 0

    # Nuevos KPIs solicitados:
    # 1. Total líneas de productos (ítems de mercancías)
    total_lineas_productos = db.query(func.count(DespachoItem.id)).scalar() or 0

    # 2. Total marcas únicas
    total_marcas = db.query(func.count(func.distinct(DespachoItem.marca))).filter(DespachoItem.marca.isnot(None), DespachoItem.marca != '').scalar() or 0

    # 3. Cantidad de despachos por año
    despachos_por_anio = (
        db.query(
            func.strftime("%Y", Despacho.fecha_despacho).label("anio"),
            func.count(Despacho.id).label("total_despachos"),
            func.sum(Despacho.valor_fob).label("total_fob")
        )
        .filter(Despacho.fecha_despacho.isnot(None))
        .group_by("anio")
        .order_by(desc("anio"))
        .all()
    )

    # 4. Cantidad de despachos por país de origen
    despachos_por_origen = (
        db.query(
            Despacho.pais_origen.label("origen"),
            func.count(Despacho.id).label("total_despachos"),
            func.sum(Despacho.valor_fob).label("total_fob")
        )
        .filter(Despacho.pais_origen.isnot(None), Despacho.pais_origen != "")
        .group_by(Despacho.pais_origen)
        .order_by(desc("total_despachos"))
        .limit(8)
        .all()
    )

    ultimos_despachos = db.query(Despacho).order_by(desc(Despacho.fecha_despacho), desc(Despacho.id)).limit(10).all()
    ultimos_logs = db.query(ProcessingLog).order_by(ProcessingLog.created_at.desc()).limit(5).all()

    stats = {
        "total_despachos": total_despachos,
        "despachos_este_anio": despachos_este_anio,
        "despachos_este_mes": despachos_este_mes,
        "hoy_anio": hoy.year,
        "total_empresas": total_empresas,
        "total_importadores": total_importadores,
        "total_exportadores": total_exportadores,
        "total_fob": total_fob,
        "total_cif": total_cif,
        "total_impuestos": total_impuestos,
        "total_tributos_pagos": total_impuestos,
        "total_peso": total_peso,
        "pendientes_revision": pendientes_revision,
        "confirmados": confirmados,
        "procesados": procesados,
        "total_lineas_productos": total_lineas_productos,
        "total_marcas": total_marcas,
        "despachos_por_anio": despachos_por_anio,
        "despachos_por_origen": despachos_por_origen
    }

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "stats": stats,
            **stats,
            "ultimos_despachos": ultimos_despachos,
            "logs": ultimos_logs,
            "ultimos_logs": ultimos_logs
        }
    )
