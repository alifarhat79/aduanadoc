"""
Mantenimiento automático de la base local al arrancar.

Elimina duplicados EXACTOS de despachos: mismo número de despacho Y mismo hash del PDF
(el mismo archivo importado dos veces, p. ej. por dos escaneos simultáneos de versiones
anteriores). Se conserva el registro más antiguo (ID menor).

Seguridad:
- Antes de borrar se crea un respaldo completo de la base en data/.
- Si alguna planilla de valoración usa ítems del duplicado, ese duplicado NO se borra.
- Solo se tocan duplicados con número Y hash idénticos.
"""
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from app.config import settings

logger = logging.getLogger(__name__)


def _db_file() -> Path:
    url = settings.DATABASE_URL
    path = url.split("sqlite:///", 1)[-1]
    p = Path(path)
    if not p.is_absolute():
        from app.config import BASE_DIR
        p = (BASE_DIR / p).resolve()
    return p


def remove_exact_duplicates() -> Dict[str, Any]:
    from app.database import SessionLocal
    from app.models import Despacho, PlanillaItem

    result: Dict[str, Any] = {"eliminados": [], "omitidos": [], "respaldo": None}
    db = SessionLocal()
    try:
        grupos = defaultdict(list)
        for d in db.query(Despacho).order_by(Despacho.id).all():
            if d.numero_despacho and d.hash_archivo:
                grupos[(d.numero_despacho.strip(), d.hash_archivo.strip())].append(d)

        a_borrar = []
        for (num, _), ds in grupos.items():
            for extra in ds[1:]:
                item_ids = [i.id for i in extra.items]
                usados = 0
                if item_ids:
                    usados = db.query(PlanillaItem).filter(PlanillaItem.item_catalogo_id.in_(item_ids)).count()
                if usados:
                    result["omitidos"].append(f"{num} (id {extra.id}: usado en planillas)")
                else:
                    a_borrar.append(extra)

        if not a_borrar:
            return result

        # Respaldo completo antes de borrar
        src_path = _db_file()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bk_path = src_path.parent / f"despachos_antes_limpieza_{stamp}.db"
        src = sqlite3.connect(str(src_path))
        dst = sqlite3.connect(str(bk_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        result["respaldo"] = str(bk_path)

        for extra in a_borrar:
            result["eliminados"].append(f"{extra.numero_despacho} (id {extra.id})")
            db.delete(extra)
        db.commit()
        logger.info(f"[Mantenimiento] Duplicados exactos eliminados: {result['eliminados']} (respaldo: {bk_path.name})")
    except Exception as e:
        db.rollback()
        result["error"] = str(e)[:200]
        logger.warning(f"[Mantenimiento] No se pudo limpiar duplicados: {e}")
    finally:
        db.close()
    return result
