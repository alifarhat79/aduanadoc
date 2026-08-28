import os
import math
from pathlib import Path
from fastapi import APIRouter, Depends, Request, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, desc, func
from typing import Optional
from datetime import datetime, timezone
from pydantic import BaseModel

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
    origen: Optional[str] = Query(None),
    anio: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(35, ge=10, le=100),
    db: Session = Depends(get_db)
):
    """Vista de tabla principal de despachos ordenada por fecha más reciente y con paginación."""
    query = db.query(Despacho)

    if q:
        search_pattern = f"%{q}%"
        filters = [
            Despacho.numero_despacho.ilike(search_pattern),
            Despacho.importador_nombre.ilike(search_pattern),
            Despacho.exportador_nombre.ilike(search_pattern),
            Despacho.importador_documento.ilike(search_pattern),
            Despacho.propietario.ilike(search_pattern),
            Despacho.pais_origen.ilike(search_pattern),
            Despacho.bl.ilike(search_pattern),
            Despacho.contenedor.ilike(search_pattern),
            Despacho.aduana.ilike(search_pattern),
            Despacho.despachante_nombre.ilike(search_pattern)
        ]
        if q.strip().isdigit() and len(q.strip()) == 4:
            filters.append(func.strftime("%Y", Despacho.fecha_despacho) == q.strip())
        query = query.filter(or_(*filters))

    if estado:
        query = query.filter(Despacho.estado_procesamiento == estado)

    if importador:
        query = query.filter(Despacho.importador_nombre.ilike(f"%{importador}%"))

    if propietario:
        query = query.filter(Despacho.propietario.ilike(f"%{propietario}%"))

    if origen:
        query = query.filter(Despacho.pais_origen.ilike(f"%{origen}%"))

    if anio:
        query = query.filter(func.strftime("%Y", Despacho.fecha_despacho) == str(anio).strip())

    # Ordenar por fecha más reciente primero (y por ID secundario)
    query = query.order_by(desc(Despacho.fecha_despacho), desc(Despacho.id))

    total_items = query.count()
    total_pages = max(1, math.ceil(total_items / per_page))
    current_page = min(page, total_pages)

    despachos = query.offset((current_page - 1) * per_page).limit(per_page).all()

    # Obtener lista única de importadores, orígenes y años para los filtros
    importadores = [r[0] for r in db.query(Despacho.importador_nombre).distinct().filter(Despacho.importador_nombre.isnot(None), Despacho.importador_nombre != '').order_by(Despacho.importador_nombre).all()]
    origenes = [r[0] for r in db.query(Despacho.pais_origen).distinct().filter(Despacho.pais_origen.isnot(None), Despacho.pais_origen != '').order_by(Despacho.pais_origen).all()]
    anios = [r[0] for r in db.query(func.strftime("%Y", Despacho.fecha_despacho)).distinct().filter(Despacho.fecha_despacho.isnot(None)).order_by(desc(func.strftime("%Y", Despacho.fecha_despacho))).all() if r[0]]

    return templates.TemplateResponse(
        request=request,
        name="despachos.html",
        context={
            "despachos": despachos,
            "q": q,
            "estado_sel": estado,
            "importador_sel": importador,
            "propietario_sel": propietario,
            "origen_sel": origen,
            "anio_sel": anio,
            "importadores": importadores,
            "origenes": origenes,
            "anios": anios,
            "page": current_page,
            "per_page": per_page,
            "total_pages": total_pages,
            "total_items": total_items,
            "has_prev": current_page > 1,
            "has_next": current_page < total_pages,
            "prev_page": current_page - 1,
            "next_page": current_page + 1
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
    """Sirve el archivo PDF original para visualización embebida inline en el navegador sin forzar descarga."""
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
        headers={
            "Content-Disposition": "inline",
            "Content-Type": "application/pdf",
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600"
        }
    )

@router.get("/{despacho_id}/descargar-pdf")
async def descargar_despacho_pdf(despacho_id: int, db: Session = Depends(get_db)):
    """Descarga explícita del archivo PDF."""
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
        content_disposition_type="attachment",
        filename=despacho.nombre_archivo_original or pdf_path.name
    )


class PropietarioUpdatePayload(BaseModel):
    propietario: str


@router.post("/api/{despacho_id}/propietario")
@router.patch("/api/{despacho_id}/propietario")
async def update_despacho_propietario_api(
    despacho_id: int,
    payload: PropietarioUpdatePayload,
    db: Session = Depends(get_db)
):
    """Actualiza de forma inline el dueño / cliente de un despacho, sincroniza con Turso y notifica por Telegram."""
    despacho = db.query(Despacho).filter(Despacho.id == despacho_id).first()
    if not despacho:
        raise HTTPException(status_code=404, detail="Despacho no encontrado")

    anterior = despacho.propietario or "Sin Asignar"
    nuevo = payload.propietario.strip()
    if not nuevo:
        nuevo = "Sin Asignar"

    despacho.propietario = nuevo
    despacho.updated_at = datetime.now(timezone.utc)

    # Registrar en auditoría
    audit = DespachoAuditoria(
        despacho_id=despacho.id,
        campo_modificado="propietario",
        valor_anterior=anterior,
        valor_nuevo=nuevo,
        usuario="Operador Aduanero",
        fecha_modificacion=datetime.now(timezone.utc)
    )
    db.add(audit)
    db.commit()
    db.refresh(despacho)

    # Sincronizar automáticamente a Turso Cloud
    turso_synced = False
    try:
        from app.services.turso_service import TursoService
        turso = TursoService()
        if turso.is_configured():
            await turso.push_despacho_to_turso(despacho.id, db)
            turso_synced = True
    except Exception as t_err:
        pass

    # Enviar notificación a Telegram
    telegram_sent = False
    try:
        from app.services.notification_service import NotificationService
        noti = NotificationService()
        res_noti = noti.notify_propietario_actualizado(
            numero_despacho=despacho.numero_despacho or "S/N",
            propietario_nuevo=nuevo,
            propietario_anterior=anterior,
            importador_nombre=despacho.importador_nombre
        )
        telegram_sent = res_noti.get("results", {}).get("telegram", {}).get("success", False)
    except Exception:
        pass

    return {
        "success": True,
        "despacho_id": despacho.id,
        "propietario": nuevo,
        "turso_synced": turso_synced,
        "telegram_sent": telegram_sent,
        "message": f"Dueño actualizado a '{nuevo}' y sincronizado con éxito."
    }


@router.delete("/{despacho_id}")
async def delete_despacho(despacho_id: int, db: Session = Depends(get_db)):
    """Elimina un despacho y sus ítems asociados de la base de datos y de Turso Cloud."""
    despacho = db.query(Despacho).filter(Despacho.id == despacho_id).first()
    if not despacho:
        raise HTTPException(status_code=404, detail="Despacho no encontrado")

    numero_despacho = despacho.numero_despacho
    hash_archivo = despacho.hash_archivo

    # Si se desea eliminar el archivo físico
    if os.path.exists(despacho.archivo_pdf):
        try:
            os.remove(despacho.archivo_pdf)
        except Exception:
            pass

    db.delete(despacho)
    db.commit()

    # Eliminar también de Turso Cloud para evitar que vuelva al sincronizar
    try:
        from app.services.turso_service import TursoService
        turso = TursoService()
        if turso.is_configured():
            await turso.delete_despacho_from_turso(
                numero_despacho=numero_despacho,
                hash_archivo=hash_archivo,
                despacho_id=despacho_id
            )
    except Exception:
        pass

    return {"status": "success", "message": "Despacho eliminado correctamente"}
