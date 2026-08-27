import os
from fastapi import APIRouter, Depends, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timezone

from app.database import get_db
from app.models import Despacho, DespachoItem, DespachoAuditoria
from app.services.normalizer import parse_date, parse_currency, clean_text
from app.templates_config import templates

router = APIRouter(prefix="/revisar", tags=["Revisión"])

@router.get("", response_class=HTMLResponse)
async def list_pending_review(request: Request, db: Session = Depends(get_db)):
    """Lista todos los despachos que requieren revisión humana."""
    pendientes = db.query(Despacho).filter(
        Despacho.estado_procesamiento.in_(["REVISAR", "PROCESADO", "DUPLICADO"])
    ).order_by(desc(Despacho.created_at)).all()

    return templates.TemplateResponse(
        request=request,
        name="revisar_lista.html",
        context={"despachos": pendientes}
    )

@router.get("/{despacho_id}", response_class=HTMLResponse)
async def review_despacho_view(despacho_id: int, request: Request, db: Session = Depends(get_db)):
    """Pantalla de Revisión Humana Dual: PDF original a la izquierda vs Formulario a la derecha."""
    despacho = db.query(Despacho).filter(Despacho.id == despacho_id).first()
    if not despacho:
        raise HTTPException(status_code=404, detail="Despacho no encontrado")

    items = db.query(DespachoItem).filter(DespachoItem.despacho_id == despacho_id).all()
    metadata = despacho.metadata_extraccion or {}
    
    from pathlib import Path
    from app.config import BASE_DIR
    pdf_exists = False
    if despacho.archivo_pdf:
        p = Path(despacho.archivo_pdf)
        if not p.is_absolute():
            p = (BASE_DIR / despacho.archivo_pdf).resolve()
        pdf_exists = p.exists()

    return templates.TemplateResponse(
        request=request,
        name="revisar.html",
        context={
            "despacho": despacho,
            "items": items,
            "metadata": metadata,
            "pdf_exists": pdf_exists
        }
    )

@router.post("/{despacho_id}")
async def save_reviewed_despacho(
    despacho_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Guarda las correcciones del usuario y registra la trazabilidad en despacho_auditoria."""
    despacho = db.query(Despacho).filter(Despacho.id == despacho_id).first()
    if not despacho:
        raise HTTPException(status_code=404, detail="Despacho no encontrado")

    form_data = await request.form()
    
    # Lista de campos editables
    editable_fields = [
        "propietario", "numero_despacho", "numero_declaracion", "referencia", "fecha_despacho",
        "importador_nombre", "importador_documento", "importador_direccion",
        "exportador_nombre", "exportador_pais", "despachante_nombre",
        "modalidad_transporte", "bl", "awb", "contenedor", "buque",
        "puerto_origen", "puerto_destino", "aduana", "regimen", "canal",
        "valor_fob", "valor_flete", "valor_seguro", "valor_cif", "valor_imponible",
        "moneda", "tipo_cambio", "impuesto_importacion", "iva", "total_general",
        "cantidad_bultos", "peso_bruto", "peso_neto", "observaciones"
    ]

    for field in editable_fields:
        if field in form_data:
            raw_new = form_data.get(field)
            
            # Normalizar según tipo
            if "fecha" in field:
                val_new = parse_date(raw_new) if raw_new else None
            elif any(k in field for k in ["valor", "peso", "cantidad", "total", "tipo_cambio", "impuesto", "iva"]):
                val_new = parse_currency(raw_new) if raw_new else None
            else:
                val_new = clean_text(raw_new)

            val_old = getattr(despacho, field)

            # Comparar si hubo cambio
            if str(val_old or "") != str(val_new or ""):
                # Registrar auditoría
                audit = DespachoAuditoria(
                    despacho_id=despacho.id,
                    campo_modificado=field,
                    valor_anterior=str(val_old) if val_old is not None else None,
                    valor_nuevo=str(val_new) if val_new is not None else None,
                    usuario="Operador Aduanero",
                    fecha_modificacion=datetime.now(timezone.utc)
                )
                db.add(audit)
                setattr(despacho, field, val_new)

    # Actualizar estado a CONFIRMADO
    despacho.estado_procesamiento = "CONFIRMADO"
    despacho.updated_at = datetime.now(timezone.utc)

    db.commit()

    # Sincronización automática a Turso Cloud
    try:
        from app.services.turso_service import TursoService
        turso = TursoService()
        if turso.is_configured():
            await turso.push_despacho_to_turso(despacho.id, db)
    except Exception:
        pass

    return RedirectResponse(url=f"/despachos/{despacho.id}", status_code=303)
