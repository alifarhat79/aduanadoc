"""
Servicio de Gestión, Detección y Normalización Inteligente de Marcas.
Permite agrupar variaciones (ej: 'LIF POD', 'LIFE POD', 'LIFEPOD'),
unificar masivamente en la base de datos y mantener reglas permanentes
para que nuevos despachos se corrijan automáticamente.
"""
import re
import time
import unicodedata
from typing import List, Dict, Any, Optional, Set, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, or_

from app.models import DespachoItem, PlanillaItem, MarcaSubitemEtiqueta, MarcaRegla, Despacho
from app.services.subitem_cleaner import invalidate_brand_cache

# Caché en memoria de reglas de normalización
_RULES_CACHE: Dict[str, str] = {}
_RULES_CACHE_TIME: float = 0.0
_RULES_CACHE_TTL: float = 60.0  # 60 segundos

IGNORABLE_SUFFIXES = {
    "INC", "CORP", "LLC", "SA", "SRL", "LTDA", "COMPANY", "CO",
    "PERFUME", "PERFUMES", "ELECTRONICS", "OFFICIAL", "GLOBAL",
    "PARFUMS", "PARFUM", "BEAUTY", "COSMETICS"
}


def _normalize_skeleton(text: str) -> str:
    """Devuelve un 'esqueleto' alfanumérico limpio para comparar similitud ignorando espacios y símbolos."""
    if not text:
        return ""
    # Quitar acentos
    norm = unicodedata.normalize('NFD', text.upper()).encode('ascii', 'ignore').decode('utf-8')
    # Quitar caracteres no alfanuméricos
    clean = re.sub(r'[^A-Z0-9\s]', ' ', norm)
    tokens = [w for w in clean.split() if w and w not in IGNORABLE_SUFFIXES]
    return "".join(tokens)


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Calcula la distancia de Levenshtein entre dos cadenas cortas."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def get_brands_summary(db: Session, q: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Obtiene la lista consolidada de todas las marcas con:
    - Nombre de marca
    - Total de ítems
    - Total de despachos donde aparece
    - Fecha más reciente
    """
    query = (
        db.query(
            DespachoItem.marca,
            func.count(DespachoItem.id).label("total_items"),
            func.count(func.distinct(DespachoItem.despacho_id)).label("total_despachos"),
            func.max(Despacho.fecha_despacho).label("ultima_fecha")
        )
        .join(Despacho, DespachoItem.despacho_id == Despacho.id)
        .filter(DespachoItem.marca.isnot(None), DespachoItem.marca != "")
        .group_by(DespachoItem.marca)
    )

    if q:
        query = query.filter(DespachoItem.marca.ilike(f"%{q.strip()}%"))

    results = query.order_by(desc("total_items"), DespachoItem.marca).all()

    marcas_list = []
    for r in results:
        marca_name = r[0]
        if not marca_name:
            continue
        marcas_list.append({
            "marca": marca_name,
            "total_items": r[1] or 0,
            "total_despachos": r[2] or 0,
            "ultima_fecha": r[3].strftime("%d/%m/%Y") if r[3] else "-"
        })

    return marcas_list


def detect_brand_clusters(db: Session) -> List[Dict[str, Any]]:
    """
    Analiza todas las marcas registradas y detecta grupos de marcas sospechosas
    de ser la misma (por ejemplo: 'LIFEPOD', 'LIFE POD', 'LIF POD' o 'LATTAFA', 'LATAFFA').
    """
    all_brands = get_brands_summary(db)
    if not all_brands:
        return []

    # Filtrar marcas genéricas o nulas
    filtered_brands = [
        b for b in all_brands
        if b["marca"].strip().upper() not in ("SIN MARCA", "*", "***", "GENERICO", "GENÉRICO", "VARIOS", "-")
    ]

    # 1. Agrupamiento por esqueleto directo (ej: "LIFEPOD", "LIFE POD" -> "LIFEPOD")
    skeleton_map: Dict[str, List[Dict[str, Any]]] = {}
    for b in filtered_brands:
        skel = _normalize_skeleton(b["marca"])
        if len(skel) >= 3:
            skeleton_map.setdefault(skel, []).append(b)

    clusters: List[Dict[str, Any]] = []
    visited_marcas: Set[str] = set()
    cluster_idx = 1

    # Agregar grupos por mismo esqueleto
    for skel, group in skeleton_map.items():
        if len(group) > 1:
            # Ordenar por cantidad de ítems descendente: la marca con más ítems suele ser la correcta
            sorted_group = sorted(group, key=lambda x: x["total_items"], reverse=True)
            canonical = sorted_group[0]["marca"]
            cluster_marcas = [b["marca"] for b in sorted_group]

            clusters.append({
                "cluster_id": f"cluster_{cluster_idx}",
                "marca_sugerida": canonical,
                "motivo": "Mismo nombre con diferente espaciado o puntuación",
                "variantes": sorted_group,
                "total_variantes": len(sorted_group),
                "total_items_afectados": sum(b["total_items"] for b in sorted_group[1:])
            })
            visited_marcas.update(cluster_marcas)
            cluster_idx += 1

    # 2. Agrupamiento secundario por distancia de edición (Levenshtein <= 2 para nombres >= 5 caracteres)
    remaining_brands = [b for b in filtered_brands if b["marca"] not in visited_marcas and len(_normalize_skeleton(b["marca"])) >= 5]
    used_in_fuzzy: Set[str] = set()

    for i in range(len(remaining_brands)):
        b1 = remaining_brands[i]
        if b1["marca"] in used_in_fuzzy:
            continue

        skel1 = _normalize_skeleton(b1["marca"])
        fuzzy_group = [b1]

        for j in range(i + 1, len(remaining_brands)):
            b2 = remaining_brands[j]
            if b2["marca"] in used_in_fuzzy:
                continue

            skel2 = _normalize_skeleton(b2["marca"])
            # Si la longitud difiere en más de 2 caracteres, omitir cálculo costoso
            if abs(len(skel1) - len(skel2)) > 2:
                continue

            dist = _levenshtein_distance(skel1, skel2)
            if dist <= 2 and dist > 0:
                fuzzy_group.append(b2)

        if len(fuzzy_group) > 1:
            sorted_fuzzy = sorted(fuzzy_group, key=lambda x: x["total_items"], reverse=True)
            canonical = sorted_fuzzy[0]["marca"]
            for f in sorted_fuzzy:
                used_in_fuzzy.add(f["marca"])

            clusters.append({
                "cluster_id": f"cluster_{cluster_idx}",
                "marca_sugerida": canonical,
                "motivo": "Diferencia tipográfica leve (posible error de tipeo)",
                "variantes": sorted_fuzzy,
                "total_variantes": len(sorted_fuzzy),
                "total_items_afectados": sum(b["total_items"] for b in sorted_fuzzy[1:])
            })
            cluster_idx += 1

    # Ordenar sugerencias por cantidad de ítems afectados (lo más urgente primero)
    clusters.sort(key=lambda c: c["total_items_afectados"], reverse=True)
    return clusters


def unify_brands(
    db: Session,
    marcas_origen: List[str],
    marca_destino: str,
    crear_regla: bool = True
) -> Dict[str, Any]:
    """
    Unifica una o varias marcas origen en una marca destino:
    - Actualiza todos los despacho_items
    - Actualiza planilla_items
    - Actualiza marca_subitem_etiquetas
    - Registra en marca_reglas para auto-normalizar futuros despachos
    - Invalida cachés
    """
    clean_destino = (marca_destino or "").strip().upper()
    if not clean_destino:
        raise ValueError("El nombre de la marca destino no puede estar vacío.")

    clean_orígenes = [
        m.strip() for m in marcas_origen
        if m and m.strip().upper() != clean_destino
    ]

    if not clean_orígenes:
        return {
            "success": True,
            "items_actualizados": 0,
            "marca_destino": clean_destino,
            "message": "No hay marcas diferentes que unificar."
        }

    total_items_actualizados = 0
    total_planillas_actualizadas = 0
    total_etiquetas_actualizadas = 0

    try:
        for m_orig in clean_orígenes:
            m_orig_clean = m_orig.strip()

            # 1. Actualizar despacho_items
            items_upd = (
                db.query(DespachoItem)
                .filter(func.upper(func.trim(DespachoItem.marca)) == m_orig_clean.upper())
                .update({DespachoItem.marca: clean_destino}, synchronize_session=False)
            )
            total_items_actualizados += items_upd

            # 2. Actualizar planilla_items
            planillas_upd = (
                db.query(PlanillaItem)
                .filter(func.upper(func.trim(PlanillaItem.marca)) == m_orig_clean.upper())
                .update({PlanillaItem.marca: clean_destino}, synchronize_session=False)
            )
            total_planillas_actualizadas += planillas_upd

            # 3. Actualizar marca_subitem_etiquetas
            etiquetas_upd = (
                db.query(MarcaSubitemEtiqueta)
                .filter(func.upper(func.trim(MarcaSubitemEtiqueta.marca)) == m_orig_clean.upper())
                .update({MarcaSubitemEtiqueta.marca: clean_destino}, synchronize_session=False)
            )
            total_etiquetas_actualizadas += etiquetas_upd

            # 4. Guardar regla de normalización permanente si se solicitó
            if crear_regla:
                regla_existente = (
                    db.query(MarcaRegla)
                    .filter(func.upper(func.trim(MarcaRegla.patron_origen)) == m_orig_clean.upper())
                    .first()
                )
                if regla_existente:
                    regla_existente.marca_destino = clean_destino
                else:
                    nueva_regla = MarcaRegla(
                        patron_origen=m_orig_clean.upper(),
                        marca_destino=clean_destino
                    )
                    db.add(nueva_regla)

        db.commit()

        # Invalidar cachés
        invalidate_brand_cache()
        invalidate_rules_cache()
        from app.routers.mercancias import _DROPDOWN_CACHE
        _DROPDOWN_CACHE["last_updated"] = 0

        return {
            "success": True,
            "items_actualizados": total_items_actualizados,
            "planillas_actualizadas": total_planillas_actualizadas,
            "etiquetas_actualizadas": total_etiquetas_actualizadas,
            "marcas_unificadas": clean_orígenes,
            "marca_destino": clean_destino,
            "message": f"Se unificaron exitosamente {total_items_actualizados} mercancías bajo la marca '{clean_destino}'."
        }

    except Exception as e:
        db.rollback()
        raise e


def rename_single_brand(
    db: Session,
    marca_actual: str,
    nueva_marca: str,
    crear_regla: bool = True
) -> Dict[str, Any]:
    """Renombra una marca en toda la base de datos."""
    return unify_brands(db, [marca_actual], nueva_marca, crear_regla)


def get_all_rules(db: Session) -> List[Dict[str, Any]]:
    """Obtiene todas las reglas de normalización registradas."""
    rules = db.query(MarcaRegla).order_by(MarcaRegla.marca_destino, MarcaRegla.patron_origen).all()
    return [
        {
            "id": r.id,
            "patron_origen": r.patron_origen,
            "marca_destino": r.marca_destino,
            "created_at": r.created_at.strftime("%d/%m/%Y %H:%M") if r.created_at else "-"
        }
        for r in rules
    ]


def create_manual_rule(db: Session, patron_origen: str, marca_destino: str) -> Dict[str, Any]:
    """Crea o actualiza manualmente una regla de normalización."""
    patron = (patron_origen or "").strip().upper()
    destino = (marca_destino or "").strip().upper()
    if not patron or not destino:
        raise ValueError("El patrón de origen y la marca destino son obligatorios.")

    regla = db.query(MarcaRegla).filter(func.upper(func.trim(MarcaRegla.patron_origen)) == patron).first()
    if regla:
        regla.marca_destino = destino
    else:
        regla = MarcaRegla(patron_origen=patron, marca_destino=destino)
        db.add(regla)

    db.commit()
    invalidate_rules_cache()
    return {"success": True, "id": regla.id, "patron_origen": patron, "marca_destino": destino}


def delete_rule(db: Session, rule_id: int) -> bool:
    """Elimina una regla de normalización por su ID."""
    regla = db.query(MarcaRegla).filter(MarcaRegla.id == rule_id).first()
    if regla:
        db.delete(regla)
        db.commit()
        invalidate_rules_cache()
        return True
    return False


def invalidate_rules_cache():
    """Invalida la caché en memoria de reglas."""
    global _RULES_CACHE, _RULES_CACHE_TIME
    _RULES_CACHE.clear()
    _RULES_CACHE_TIME = 0.0


def get_normalized_brand(raw_marca: Optional[str], db: Optional[Session] = None) -> str:
    """
    Normaliza una marca extraída aplicando el diccionario de reglas.
    Si no hay regla para esta marca, devuelve la original limpia.
    """
    if not raw_marca:
        return "Sin Marca"

    marca_clean = raw_marca.strip()
    marca_upper = marca_clean.upper()

    # Si es sin marca o caracteres nulos
    if marca_upper in ("SIN MARCA", "*", "***", "**********", "-", "N/A", "NONE"):
        return "Sin Marca"

    # Revisar caché en memoria
    global _RULES_CACHE, _RULES_CACHE_TIME
    now = time.time()
    if now - _RULES_CACHE_TIME > _RULES_CACHE_TTL or not _RULES_CACHE:
        if db:
            try:
                rules = db.query(MarcaRegla.patron_origen, MarcaRegla.marca_destino).all()
                _RULES_CACHE = {r[0].strip().upper(): r[1].strip() for r in rules}
                _RULES_CACHE_TIME = now
            except Exception:
                pass

    if marca_upper in _RULES_CACHE:
        return _RULES_CACHE[marca_upper]

    return marca_clean
