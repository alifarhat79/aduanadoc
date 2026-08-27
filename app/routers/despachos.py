import os
from pathlib import Path
from fastapi import APIRouter, Depends, Request, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, desc
from typing import Optional


from app.database import get_db
from app.models import Despacho, DespachoItem, DespachoAuditoria
from app.templates_config import templates

router = APIRouter(prefix="/despachos", tags=["Despachos"])

@router.get("", response_class=HTMLResponse)
async def list_despachos_view(
    request: Request,
    q: Optional[str] = Query(None, description="Búsqueda global"),
    estado: Optional[str] = Query(None),
    importador: Optional[str] = Query(None),
    propietario: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Vista de tabla principal de despachos con filtros y ordenación."""
    query = db.query(Despacho)

    if q:
        search_pattern = f"%{q}%"
        query = query.filter(
            or_(
                Despacho.numero_despacho.ilike(search_pattern),
                Despacho.importador_nombre.ilike(search_pattern),
                Despacho.exportador_nombre.ilike(search_pattern),
                Despacho.importador_documento.ilike(search_pattern),
                Despacho.propietario.ilike(search_pattern),
                Despacho.bl.ilike(search_pattern),
                Despacho.contenedor.ilike(search_pattern),
                Despacho.aduana.ilike(search_pattern),
                Despacho.despachante_nombre.ilike(search_pattern)
            )
        )

    if estado:
        query = query.filter(Despacho.estado_procesamiento == estado)

    if importador:
        query = query.filter(Despacho.importador_nombre.ilike(f"%{importador}%"))

    if propietario:
        query = query.filter(Despacho.propietario.ilike(f"%{propietario}%"))

    despachos = query.order_by(desc(Despacho.created_at)).all()

    # Obtener lista única de importadores para el filtro
    importadores = [r[0] for r in db.query(Despacho.importador_nombre).distinct().filter(Despacho.importador_nombre.isnot(None)).all()]

    return templates.TemplateResponse(
        request=request,
        name="despachos.html",
        context={
            "despachos": despachos,
            "q": q,
            "estado_sel": estado,
            "importador_sel": importador,
            "propietario_sel": propietario,
            "importadores": importadores
        }
    )

@router.get("/{despacho_id}", response_class=HTMLResponse)
async def despacho_detail_view(despacho_id: int, request: Request, db: Session = Depends(get_db)):
    """Vista de detalle completo de un despacho aduanero y sus ítems."""
    despacho = db.query(Despacho).filter(Despacho.id == despacho_id).first()
    if not despacho:
        raise HTTPException(status_code=404, detail="Despacho no encontrado")

    items = db.query(DespachoItem).filter(DespachoItem.despacho_id == despacho_id).all()
    auditorias = db.query(DespachoAuditoria).filter(DespachoAuditoria.despacho_id == despacho_id).order_by(desc(DespachoAuditoria.fecha_modificacion)).all()

    return templates.TemplateResponse(
        request=request,
        name="despacho_detalle.html",
        context={
            "despacho": despacho,
            "items": items,
            "auditorias": auditorias
        }
    )

@router.get("/api/{despacho_id}/items")
async def get_despacho_items_api(despacho_id: int, db: Session = Depends(get_db)):
    """Retorna la lista de mercancías/ítems de un despacho en formato JSON."""
    items = db.query(DespachoItem).filter(DespachoItem.despacho_id == despacho_id).order_by(DespachoItem.numero_item, DespachoItem.numero_subitem).all()
    return [
        {
            "id": it.id,
            "numero_item": it.numero_item,
            "numero_subitem": it.numero_subitem,
            "codigo_ncm": it.codigo_ncm,
            "codigo_producto": it.codigo_producto,
            "marca": it.marca,
            "descripcion": it.descripcion,
            "cantidad": it.cantidad,
            "valor_unitario": it.valor_unitario,
            "valor_total": it.valor_total
        }
        for it in items
    ]

@router.get("/api/{despacho_id}/info")
async def get_despacho_info_api(despacho_id: int, db: Session = Depends(get_db)):
    """Retorna la auditoría completa de campos extraídos y normalizados de un despacho en formato JSON."""
    despacho = db.query(Despacho).filter(Despacho.id == despacho_id).first()
    if not despacho:
        raise HTTPException(status_code=404, detail="Despacho no encontrado")

    campos_config = [
        ("numero_despacho", "Número de Despacho"),
        ("propietario", "Dueño / Cliente"),
        ("fecha_despacho", "Fecha Oficial"),
        ("importador_nombre", "Importador"),
        ("importador_documento", "RUC / Documento"),
        ("importador_direccion", "Dirección Importador"),
        ("exportador_nombre", "Proveedor / Exportador"),
        ("pais_origen", "País de Origen"),
        ("despachante_nombre", "Despachante de Aduanas"),
        ("aduana", "Aduana"),
        ("regimen", "Régimen Aduanero"),
        ("canal", "Canal Asignado"),
        ("valor_fob", "Valor FOB (USD)"),
        ("valor_flete", "Flete (USD)"),
        ("valor_seguro", "Seguro (USD)"),
        ("valor_cif", "Valor CIF Total (USD)"),
        ("tipo_cambio", "Tipo de Cambio"),
        ("valor_imponible", "Valor Imponible (Gs)"),
        ("total_general", "Total Tributos (Gs)"),
        ("peso_neto", "Peso Neto (Kg)"),
        ("peso_bruto", "Peso Bruto (Kg)"),
        ("cantidad_bultos", "Cantidad de Bultos"),
        ("bl", "Documento BL / AWB"),
        ("contenedor", "Contenedor")
    ]

    campos_extraidos = []
    for key, label in campos_config:
        val = getattr(despacho, key, None)
        if val is not None and val != "":
            # Formateo amigable
            if isinstance(val, float):
                if any(k in key for k in ["imponible", "total_general", "bultos"]):
                    val_str = f"{val:,.0f}"
                else:
                    val_str = f"{val:,.2f}"
            elif hasattr(val, "strftime"):
                val_str = val.strftime("%d/%m/%Y")
            else:
                val_str = str(val)

            meta = (despacho.metadata_extraccion or {}).get(key, {})
            campos_extraidos.append({
                "campo": key,
                "label": label,
                "valor": val_str,
                "valor_raw": val,
                "confianza": meta.get("confidence", 0.95),
                "pagina": meta.get("pagina", 1),
                "metodo": meta.get("metodo", "TEXT")
            })

    return {
        "id": despacho.id,
        "numero_despacho": despacho.numero_despacho,
        "propietario": despacho.propietario,
        "estado": despacho.estado_procesamiento,
        "canal": despacho.canal,
        "archivo": despacho.nombre_archivo_original,
        "campos": campos_extraidos
    }

@router.get("/{despacho_id}/pdf")
async def get_despacho_pdf(despacho_id: int, db: Session = Depends(get_db)):
    """Sirve el archivo PDF original para visualización embebida."""
    despacho = db.query(Despacho).filter(Despacho.id == despacho_id).first()
    if not despacho or not despacho.archivo_pdf:
        raise HTTPException(status_code=404, detail="Despacho no encontrado")

    from app.config import BASE_DIR
    pdf_path = Path(despacho.archivo_pdf)
    if not pdf_path.is_absolute():
        pdf_path = (BASE_DIR / despacho.archivo_pdf).resolve()

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="El archivo PDF físico no fue encontrado en el servidor")

    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        content_disposition_type="inline",
        filename=despacho.nombre_archivo_original or pdf_path.name,
        headers={"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=3600"}
    )


@router.delete("/{despacho_id}")
async def delete_despacho(despacho_id: int, db: Session = Depends(get_db)):
    """Elimina un despacho y sus ítems asociados de la base de datos."""
    despacho = db.query(Despacho).filter(Despacho.id == despacho_id).first()
    if not despacho:
        raise HTTPException(status_code=404, detail="Despacho no encontrado")

    # Si se desea eliminar el archivo físico
    if os.path.exists(despacho.archivo_pdf):
        try:
            os.remove(despacho.archivo_pdf)
        except Exception:
            pass

    db.delete(despacho)
    db.commit()
    return {"status": "success", "message": "Despacho eliminado correctamente"}
