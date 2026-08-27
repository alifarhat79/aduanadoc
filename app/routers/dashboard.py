from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date

from app.database import get_db
from app.models import Despacho, DespachoItem, ProcessingLog
from app.templates_config import templates

router = APIRouter(tags=["Dashboard"])

@router.get("/", response_class=HTMLResponse)
async def dashboard_view(request: Request, db: Session = Depends(get_db)):
    """Vista principal del Dashboard empresarial."""
    hoy = date.today()
    primer_dia_mes = hoy.replace(day=1)

    despachos_este_mes = db.query(func.count(Despacho.id)).filter(Despacho.fecha_despacho >= primer_dia_mes).scalar() or 0
    # KPIs
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

    ultimos_despachos = db.query(Despacho).order_by(Despacho.created_at.desc()).limit(10).all()
    ultimos_logs = db.query(ProcessingLog).order_by(ProcessingLog.created_at.desc()).limit(5).all()

    stats = {
        "total_despachos": total_despachos,
        "despachos_este_mes": despachos_este_mes,
        "total_fob": total_fob,
        "total_cif": total_cif,
        "total_impuestos": total_impuestos,
        "total_peso": total_peso,
        "pendientes_revision": pendientes_revision,
        "confirmados": confirmados,
        "procesados": procesados
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
