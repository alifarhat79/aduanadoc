import os
import re
import glob
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
import pymupdf

from app.models import Despacho, DespachoItem
from app.services.table_extractor import parse_observation_details, extract_items_from_pages
from app.services.turso_service import TursoService

logger = logging.getLogger(__name__)

def diagnose_item(descripcion: Optional[str], marca: Optional[str] = "", codigo_producto: Optional[str] = "") -> Dict[str, Any]:
    """
    Analiza la descripción, marca y código de un ítem para detectar anomalías
    y generar una sugerencia razonable y limpia.
    """
    raw_desc = (descripcion or "").strip()
    raw_marca = (marca or "").strip()
    raw_cod = (codigo_producto or "").strip() if codigo_producto else ""

    issues = []
    confidence = "none"
    needs_pdf = False

    # 1. Corte por salto de página en PDF (Subítem X - Posición Y)
    if re.match(r"^(?:Subítem|Subitem|Ítem|Item)\s+\d+\s*-\s*Posición", raw_desc, re.IGNORECASE):
        issues.append("Corte por salto de página en PDF")
        confidence = "cutoff"
        needs_pdf = True
        return {
            "is_clean": False,
            "issues": issues,
            "suggested_desc": raw_desc,
            "suggested_codigo": raw_cod,
            "suggested_marca": raw_marca,
            "confidence": confidence,
            "needs_pdf": True
        }

    # 2. Filtración de encabezados administrativos de SOFIA
    if re.search(r"HOJA\s+\d+\s+de\s+\d+|TRAMITADO\s+DIGITALMENTE|Estado\s*:\s*[A-Z]+", raw_desc, re.IGNORECASE):
        issues.append("Texto de encabezado administrativo pegado")

    # 3. Código SKU / REF / ITEM incrustado
    has_start_sku = bool(re.match(r"^(?:COD|ITEM|REF|ART|ARTICULO|SKU)\s*[:.]\s*([A-Za-z0-9_-]{3,25})\s*[-–:]\s*", raw_desc, re.IGNORECASE))
    has_end_sku = bool(re.search(r"[-–]?\s*(?:REF|ITEM|COD|ART|ARTICULO|SKU)\s*[:.]\s*([A-Za-z0-9_-]{3,25})\s*$", raw_desc, re.IGNORECASE))
    if has_start_sku or has_end_sku:
        issues.append("Código/SKU incrustado en la descripción")

    # 4. Prefijo arancelario burocrático (LOS DEMAS EN X UNIDADES, UNIDADES DE...)
    if re.search(r"^(?:LOS\s+DEM[AÁ]S|LAS\s+DEM[AÁ]S|\d+[.,\d]*\s*UNIDADES|UNIDADES\s+DE)", raw_desc, re.IGNORECASE):
        issues.append("Prefijo arancelario o de unidades")

    # 5. Artefactos residuales de puntuación o palabras cortadas al final
    if re.search(r"\s*[-–]\s*(?:\.{1,3}|ITEM)\s*$", raw_desc, re.IGNORECASE) or raw_desc.endswith("-"):
        issues.append("Residuos de puntuación o corte")

    # Procesar con el extractor para obtener la sugerencia
    clean_cod, clean_desc = parse_observation_details(raw_desc, raw_marca)

    # Si se extrajo un código nuevo y antes no había
    suggested_cod = clean_cod or raw_cod
    suggested_desc = clean_desc if clean_desc else raw_desc
    suggested_marca = raw_marca

    # Verificar si hubo un cambio real
    has_changes = (suggested_desc != raw_desc) or (suggested_cod != raw_cod and bool(suggested_cod))

    if has_changes and not issues:
        issues.append("Normalización y limpieza de formato")

    if issues:
        confidence = "high" if any(i in issues for i in ["Código/SKU incrustado en la descripción", "Texto de encabezado administrativo pegado", "Prefijo arancelario o de unidades"]) else "medium"

    return {
        "is_clean": not issues and not has_changes,
        "issues": issues,
        "suggested_desc": suggested_desc,
        "suggested_codigo": suggested_cod,
        "suggested_marca": suggested_marca,
        "confidence": confidence,
        "needs_pdf": False
    }

def audit_catalog_summary(db: Session) -> Dict[str, Any]:
    """
    Retorna métricas generales de calidad del catálogo de mercancías.
    """
    items = db.query(DespachoItem.id, DespachoItem.descripcion, DespachoItem.marca, DespachoItem.codigo_producto).all()
    total = len(items)
    cutoffs = 0
    headers = 0
    skus = 0
    prefixes = 0
    total_with_issues = 0

    for item_id, desc, marca, cod in items:
        diag = diagnose_item(desc, marca, cod)
        if not diag["is_clean"]:
            total_with_issues += 1
            if diag["needs_pdf"]:
                cutoffs += 1
            for issue in diag["issues"]:
                if "encabezado" in issue:
                    headers += 1
                elif "Código/SKU" in issue:
                    skus += 1
                elif "Prefijo" in issue:
                    prefixes += 1

    clean_count = total - total_with_issues
    pct_clean = round((clean_count / total * 100), 1) if total > 0 else 100.0

    return {
        "total_items": total,
        "clean_items": clean_count,
        "pct_clean": pct_clean,
        "total_issues": total_with_issues,
        "cutoffs_count": cutoffs,
        "headers_count": headers,
        "skus_count": skus,
        "prefixes_count": prefixes
    }

def get_items_with_suggestions(
    db: Session,
    filter_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> Dict[str, Any]:
    """
    Retorna una lista paginada de mercancías con anomalías y sus sugerencias limpias.
    Soporta filtro por tipo de anomalía ('cutoffs', 'skus', 'headers', 'all').
    """
    query = db.query(DespachoItem, Despacho).join(Despacho, DespachoItem.despacho_id == Despacho.id)
    
    # Pre-filtro rápido en base de datos para no iterar los 28.000 si no es necesario
    if filter_type == "cutoffs":
        query = query.filter(DespachoItem.descripcion.like("Subítem %"))
    elif filter_type == "headers":
        query = query.filter(DespachoItem.descripcion.like("%HOJA %"))
    elif filter_type == "skus":
        query = query.filter(
            (DespachoItem.descripcion.like("%REF.%")) | 
            (DespachoItem.descripcion.like("%ITEM:%")) | 
            (DespachoItem.descripcion.like("%COD.%")) |
            (DespachoItem.descripcion.like("%REF:%"))
        )

    all_matched_records = []
    
    for item, desp in query.all():
        diag = diagnose_item(item.descripcion, item.marca, item.codigo_producto)
        if not diag["is_clean"]:
            # Aplicar filtro en memoria para mayor precisión
            if filter_type == "cutoffs" and not diag["needs_pdf"]:
                continue
            if filter_type == "headers" and not any("encabezado" in i for i in diag["issues"]):
                continue
            if filter_type == "skus" and not any("Código/SKU" in i for i in diag["issues"]):
                continue

            all_matched_records.append({
                "id": item.id,
                "despacho_id": item.despacho_id,
                "numero_despacho": desp.numero_despacho,
                "numero_item": item.numero_item,
                "numero_subitem": item.numero_subitem,
                "marca_actual": item.marca,
                "codigo_actual": item.codigo_producto,
                "descripcion_actual": item.descripcion,
                "issues": diag["issues"],
                "suggested_desc": diag["suggested_desc"],
                "suggested_codigo": diag["suggested_codigo"],
                "suggested_marca": diag["suggested_marca"],
                "confidence": diag["confidence"],
                "needs_pdf": diag["needs_pdf"]
            })

    total_matches = len(all_matched_records)
    paginated_items = all_matched_records[offset : offset + limit]

    return {
        "total": total_matches,
        "items": paginated_items,
        "limit": limit,
        "offset": offset
    }

def apply_single_correction(
    db: Session,
    item_id: int,
    new_desc: str,
    new_codigo: Optional[str] = None,
    new_marca: Optional[str] = None
) -> Dict[str, Any]:
    """
    Aplica una corrección a un ítem individual y lo sincroniza a Turso Cloud.
    """
    item = db.query(DespachoItem).filter(DespachoItem.id == item_id).first()
    if not item:
        return {"success": False, "error": f"Ítem ID {item_id} no encontrado"}

    item.descripcion = (new_desc or "").strip()
    if new_codigo is not None:
        item.codigo_producto = (new_codigo or "").strip() or None
    if new_marca is not None:
        item.marca = (new_marca or "").strip() or "Sin Marca"

    db.commit()

    # Sincronizar despacho con Turso
    turso = TursoService()
    turso_res = {}
    if turso.is_configured():
        turso_res = turso.sync_push_despacho(item.despacho_id, db)

    return {
        "success": True,
        "item_id": item.id,
        "despacho_id": item.despacho_id,
        "descripcion": item.descripcion,
        "codigo_producto": item.codigo_producto,
        "marca": item.marca,
        "turso": turso_res
    }

def batch_apply_high_confidence_fixes(db: Session) -> Dict[str, Any]:
    """
    Aplica en lote todas las correcciones seguras y deterministas:
    - Códigos SKU/REF extraídos a la columna de código
    - Encabezados de página eliminados
    - Prefijos arancelarios removidos
    - Residuos de puntuación saneados
    """
    items = db.query(DespachoItem).all()
    updated_count = 0
    affected_despachos = set()

    for item in items:
        diag = diagnose_item(item.descripcion, item.marca, item.codigo_producto)
        # Solo aplicar correcciones de alta certeza que no requieran PDF
        if diag["confidence"] == "high" and not diag["needs_pdf"]:
            changed = False
            if diag["suggested_desc"] != item.descripcion:
                item.descripcion = diag["suggested_desc"]
                changed = True
            if diag["suggested_codigo"] and diag["suggested_codigo"] != item.codigo_producto:
                item.codigo_producto = diag["suggested_codigo"]
                changed = True
            
            if changed:
                updated_count += 1
                affected_despachos.add(item.despacho_id)

    db.commit()

    # Sincronizar despachos afectados a Turso
    turso = TursoService()
    pushed_despachos = 0
    if turso.is_configured() and affected_despachos:
        for desp_id in affected_despachos:
            res = turso.sync_push_despacho(desp_id, db)
            if res.get("success"):
                pushed_despachos += 1

    return {
        "success": True,
        "items_actualizados": updated_count,
        "despachos_afectados": len(affected_despachos),
        "despachos_sincronizados_turso": pushed_despachos
    }

def reprocess_page_break_cutoffs(db: Session) -> Dict[str, Any]:
    """
    Identifica los despachos con subítems cortados por salto de página y los
    reprocesa directamente desde sus archivos PDF originales con el motor continuo.
    """
    # 1. Localizar despachos afectados
    subquery = db.query(DespachoItem.despacho_id).filter(
        (DespachoItem.descripcion.like("Subítem %")) | 
        (DespachoItem.descripcion.like("Subitem %"))
    ).distinct().all()

    affected_desp_ids = [r[0] for r in subquery]
    logger.info(f"[ItemAuditor] Se encontraron {len(affected_desp_ids)} despachos con subítems cortados.")

    repaired_despachos = 0
    recovered_items_count = 0
    turso = TursoService()

    for desp_id in affected_desp_ids:
        despacho = db.query(Despacho).filter(Despacho.id == desp_id).first()
        if not despacho:
            continue

        pdf_path = despacho.archivo_pdf
        if not pdf_path or not os.path.exists(pdf_path):
            if despacho.numero_despacho:
                candidates = glob.glob(f"uploads/**/*{despacho.numero_despacho}*.pdf", recursive=True)
                if candidates:
                    pdf_path = candidates[0]
                    despacho.archivo_pdf = pdf_path
                    db.commit()

        if not pdf_path or not os.path.exists(pdf_path):
            continue

        try:
            doc = pymupdf.open(pdf_path)
            pages_data = [{"page_num": idx + 1, "text": doc[idx].get_text()} for idx in range(len(doc))]
            doc.close()

            # Extraer con el nuevo motor continuo
            new_items = extract_items_from_pages(pages_data)
            if not new_items:
                continue

            # Crear diccionario indexado por (numero_item, numero_subitem)
            new_items_map = {}
            for ni in new_items:
                key = (ni.get("numero_item"), ni.get("numero_subitem"))
                new_items_map[key] = ni

            # Actualizar los ítems existentes en la BD
            existing_items = db.query(DespachoItem).filter(DespachoItem.despacho_id == desp_id).all()
            despacho_recovered = 0

            for it in existing_items:
                key = (it.numero_item, it.numero_subitem)
                if key in new_items_map:
                    candidate = new_items_map[key]
                    c_desc = candidate.get("descripcion")
                    c_marca = candidate.get("marca")
                    c_cod = candidate.get("codigo_producto")

                    # Si el ítem estaba cortado (Subítem X...) y el candidato tiene texto real
                    if it.descripcion and "Subítem" in it.descripcion and c_desc and "Subítem" not in c_desc:
                        it.descripcion = c_desc
                        if c_marca and c_marca != "Sin Marca":
                            it.marca = c_marca
                        if c_cod:
                            it.codigo_producto = c_cod
                        despacho_recovered += 1
                        recovered_items_count += 1

            if despacho_recovered > 0:
                db.commit()
                repaired_despachos += 1
                if turso.is_configured():
                    turso.sync_push_despacho(desp_id, db)

        except Exception as e:
            logger.warning(f"[ItemAuditor] Error reprocesando despacho ID {desp_id}: {e}")

    return {
        "success": True,
        "despachos_reparados": repaired_despachos,
        "items_recuperados": recovered_items_count
    }
