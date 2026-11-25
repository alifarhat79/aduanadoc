"""
Servicio del Arancel de Aduanas.

- Carga en la base local el arancel incluido con el sistema (app/recursos/arancel) la primera vez
  que se usa, y cada vez que llega una edición más nueva con una actualización.
- Mantiene un índice en memoria de la versión activa (búsqueda instantánea).
- Cruza el arancel con los ítems de los despachos SOLO PARA LEERLOS: nunca modifica datos que
  vienen de los PDFs. La validación se calcula al mostrar (no cambia estados ni valores).
"""
import logging
import re
import threading
import unicodedata
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import insert, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    ArancelCapitulo, ArancelPosicion, ArancelSeccion, ArancelVersion, Despacho, DespachoItem,
)
from app.services.arancel_importer import (
    construir_dataset, hash_dataset, leer_dataset, parsear_excel_aec, parsear_pdf_mef, validar_dataset,
)

logger = logging.getLogger(__name__)

ARANCEL_INCLUIDO = Path(__file__).resolve().parent.parent / "recursos" / "arancel" / "arancel_vigente.json.gz"

# Solo las listas cuyo significado define el propio arancel; las demás se muestran tal cual.
LISTAS_OFICIALES = {
    "BK": "Bienes de Capital",
    "BIT": "Bienes de Informática y Telecomunicaciones",
}
TOLERANCIA_TASA = 0.05  # puntos porcentuales
MARCAS_GENERICAS = {"SIN MARCA", "GENERAL", "S/M", "SM", ""}

_LOCK = threading.RLock()
_estado: Dict[str, Any] = {"incluido_revisado": False, "indice": None, "uso": None, "uso_clave": None}

# Clave corta del dataset -> columna de ArancelPosicion
_COLUMNAS = {
    "c": "codigo", "d": "digitos", "n": "nivel", "t": "descripcion", "g": "guiones",
    "aec": "aec", "aec_t": "aec_texto", "anv": "anv", "anv_t": "anv_texto", "anv_dec": "anv_decreto",
    "lista": "lista", "iva": "iva", "iva_t": "iva_texto", "renta": "renta", "renta_t": "renta_texto",
    "isc": "isc", "isc_t": "isc_texto", "omc": "omc", "ai": "autopartes_intrazona",
    "ae": "autopartes_extrazona", "obs": "observaciones", "fi": "fuente_impuestos", "pag": "pagina",
}


# ======================================================================================
# Utilidades
# ======================================================================================

def normalizar(texto: Optional[str]) -> str:
    """Minúsculas y sin tildes, para buscar 'telefono' y encontrar 'Teléfonos'."""
    descompuesto = unicodedata.normalize("NFD", texto or "")
    return "".join(ch for ch in descompuesto if not unicodedata.combining(ch)).lower()


def digitos(codigo: Optional[str]) -> str:
    return re.sub(r"\D", "", codigo or "")


def formatear_ncm(d: str) -> str:
    if len(d) >= 8:
        base = f"{d[:4]}.{d[4:6]}.{d[6:8]}"
        return base + (f".{d[8:]}" if len(d) > 8 else "")
    if len(d) >= 6:
        return f"{d[:4]}.{d[4:6]}"
    return d


def _fecha(valor) -> Optional[date]:
    if not valor:
        return None
    if isinstance(valor, date):
        return valor
    try:
        return datetime.strptime(str(valor)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _pct(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return (f"{v:.2f}".rstrip("0").rstrip(".") + "%").replace(".", ",")


def _invalidar_cache() -> None:
    _estado["indice"] = None
    _estado["uso"] = None
    _estado["uso_clave"] = None


# ======================================================================================
# Carga de versiones
# ======================================================================================

def cargar_dataset(db: Session, dataset: Dict[str, Any], origen: str, activar: bool) -> ArancelVersion:
    """Guarda un dataset del importador como nueva versión. Si ya existe (mismo contenido), la devuelve."""
    h = dataset.get("meta", {}).get("hash") or hash_dataset(dataset)
    existente = db.query(ArancelVersion).filter(ArancelVersion.hash_contenido == h).first()
    if existente:
        if activar and not existente.activa:
            _activar(db, existente)
            db.commit()
            _invalidar_cache()
        return existente

    meta = dataset.get("meta", {})
    version = ArancelVersion(
        nombre=(meta.get("nombre") or "Arancel")[:200], fuente=meta.get("fuente"), hash_contenido=h,
        fecha_vigencia=_fecha(meta.get("fecha_vigencia")), origen=origen, activa=False,
        total_ncm=(dataset.get("resumen") or {}).get("ncm", 0), leyendas=dataset.get("leyendas") or {},
        resumen=dataset.get("resumen") or {},
    )
    db.add(version)
    db.flush()

    filas = []
    for p in dataset["posiciones"]:
        fila = {col: None for col in _COLUMNAS.values()}
        fila.update({"version_id": version.id, "capitulo": p["d"][:2], "guiones": 0, "observaciones": []})
        for clave, col in _COLUMNAS.items():
            if clave in p:
                fila[col] = p[clave]
        filas.append(fila)
    if filas:
        db.execute(insert(ArancelPosicion), filas)
    capitulos = [{"version_id": version.id, "numero": c["numero"], "seccion": c.get("seccion"),
                  "titulo": c.get("titulo"), "notas": c.get("notas")} for c in dataset.get("capitulos", [])]
    if capitulos:
        db.execute(insert(ArancelCapitulo), capitulos)
    secciones = [{"version_id": version.id, "numero": s["numero"], "titulo": s.get("titulo"),
                  "notas": s.get("notas")} for s in dataset.get("secciones", [])]
    if secciones:
        db.execute(insert(ArancelSeccion), secciones)
    if activar:
        _activar(db, version)
    db.commit()
    _invalidar_cache()
    return version


def _borrar_version(db: Session, version: ArancelVersion) -> None:
    """Borra una versión y sus filas explícitamente (no depende de PRAGMA foreign_keys)."""
    for modelo in (ArancelPosicion, ArancelCapitulo, ArancelSeccion):
        db.query(modelo).filter(modelo.version_id == version.id).delete(synchronize_session=False)
    db.query(ArancelVersion).filter(ArancelVersion.id == version.id).delete(synchronize_session=False)


def _activar(db: Session, version: ArancelVersion) -> None:
    db.query(ArancelVersion).filter(ArancelVersion.id != version.id, ArancelVersion.activa.is_(True)).update(
        {"activa": False}, synchronize_session=False)
    version.activa = True


def version_activa(db: Session) -> Optional[ArancelVersion]:
    return db.query(ArancelVersion).filter(ArancelVersion.activa.is_(True)).order_by(ArancelVersion.id.desc()).first()


def _revisar_incluido(db: Session) -> None:
    """Carga el arancel incluido si todavía no está; lo activa si es más nuevo que el activo."""
    if not ARANCEL_INCLUIDO.exists():
        return
    dataset = leer_dataset(ARANCEL_INCLUIDO)
    h = dataset["meta"].get("hash") or hash_dataset(dataset)
    activa = version_activa(db)
    ya = db.query(ArancelVersion).filter(ArancelVersion.hash_contenido == h).first()
    if ya:
        if activa is None:
            _activar(db, ya)
            db.commit()
        return
    fecha = _fecha(dataset["meta"].get("fecha_vigencia")) or date.min
    activar = (
        activa is None
        or (activa.fecha_vigencia or date.min) < fecha
        or (activa.origen == "incluido" and (activa.fecha_vigencia or date.min) <= fecha)
    )
    nueva = cargar_dataset(db, dataset, origen="incluido", activar=activar)
    if activar:
        # Versiones incluidas anteriores ya no sirven: se eliminan para no ocupar espacio
        for vieja in db.query(ArancelVersion).filter(ArancelVersion.origen == "incluido",
                                                     ArancelVersion.id != nueva.id,
                                                     ArancelVersion.activa.is_(False)).all():
            _borrar_version(db, vieja)
        db.commit()
    logger.info("Arancel incluido cargado: %s (activa=%s)", nueva.nombre, activar)


def asegurar_arancel(db: Session) -> Optional[ArancelVersion]:
    """Devuelve la versión activa, cargando antes el arancel incluido si hace falta (una vez por proceso)."""
    if not _estado["incluido_revisado"]:
        with _LOCK:
            if not _estado["incluido_revisado"]:
                try:
                    _revisar_incluido(db)
                except IntegrityError:
                    db.rollback()  # otro proceso lo cargó al mismo tiempo
                except Exception:
                    db.rollback()
                    logger.exception("No se pudo cargar el arancel incluido")
                _estado["incluido_revisado"] = True
                _invalidar_cache()
    return version_activa(db)


# ======================================================================================
# Índice en memoria de la versión activa
# ======================================================================================

def indice(db: Session) -> Optional[Dict[str, Any]]:
    version = asegurar_arancel(db)
    if version is None:
        return None
    idx = _estado["indice"]
    if idx is not None and idx["version_id"] == version.id:
        return idx
    with _LOCK:
        idx = _estado["indice"]
        if idx is not None and idx["version_id"] == version.id:
            return idx
        idx = _construir_indice(db, version)
        _estado["indice"] = idx
        _estado["uso"] = None
        _estado["uso_clave"] = None
        return idx


def _construir_indice(db: Session, version: ArancelVersion) -> Dict[str, Any]:
    from app.services.ncm_helper import CAPITULOS_SISTEMA_ARMONIZADO

    claves = list(_COLUMNAS.keys()) + ["cap"]
    columnas = [getattr(ArancelPosicion, _COLUMNAS[k]) for k in _COLUMNAS] + [ArancelPosicion.capitulo]
    nodos: List[Dict[str, Any]] = []
    for fila in db.query(*columnas).filter(ArancelPosicion.version_id == version.id).order_by(ArancelPosicion.id):
        nodos.append(dict(zip(claves, fila)))

    por_d = {n["d"]: n for n in nodos}
    por_sub: Dict[str, List[Dict[str, Any]]] = {}
    aperturas: Dict[str, List[Dict[str, Any]]] = {}
    fichas = []
    for n in nodos:
        if n["n"] not in ("ncm", "apertura"):
            continue
        d = n["d"]
        anc_d = [d[:4], d[:6]] + ([d[:7]] if len(d) >= 7 else []) + ([d[:8]] if n["n"] == "apertura" else [])
        anc = [por_d[x] for x in anc_d if x in por_d and x != d]
        n["anc"] = [a["d"] for a in anc]
        n["ruta"] = " › ".join(a["t"] or "" for a in anc)
        n["busq"] = normalizar(f"{n['c']} {d} {n['ruta']} {n['t']}")
        n["tn"] = normalizar(n["t"])
        fichas.append(n)
        if n["n"] == "ncm":
            por_sub.setdefault(d[:6], []).append(n)
        else:
            aperturas.setdefault(d[:8], []).append(n)

    caps = {}
    for c in db.query(ArancelCapitulo).filter(ArancelCapitulo.version_id == version.id).order_by(ArancelCapitulo.numero):
        caps[c.numero] = {"numero": c.numero, "seccion": c.seccion,
                          "titulo": c.titulo or CAPITULOS_SISTEMA_ARMONIZADO.get(c.numero, ""), "notas": c.notas or ""}
    secs = {}
    for s in db.query(ArancelSeccion).filter(ArancelSeccion.version_id == version.id).order_by(ArancelSeccion.id):
        secs[s.numero] = {"numero": s.numero, "titulo": s.titulo or "", "notas": s.notas or ""}

    return {
        "version_id": version.id, "version": _version_dict(version), "nodos": nodos, "fichas": fichas,
        "por_d": por_d, "por_sub": por_sub, "aperturas": aperturas, "caps": caps, "secs": secs,
        "leyendas": version.leyendas or {},
    }


def _hora_local(momento: Optional[datetime]) -> Optional[str]:
    """created_at se guarda en UTC (SQLite lo devuelve sin zona): se muestra en la hora de la PC."""
    if momento is None:
        return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone().strftime("%d/%m/%Y %H:%M")


def _version_dict(v: ArancelVersion) -> Dict[str, Any]:
    return {
        "id": v.id, "nombre": v.nombre, "fuente": v.fuente, "origen": v.origen, "activa": bool(v.activa),
        "fecha_vigencia": v.fecha_vigencia.isoformat() if v.fecha_vigencia else None,
        "total_ncm": v.total_ncm, "resumen": v.resumen or {},
        "cargada": _hora_local(v.created_at),
    }


# ======================================================================================
# Validación de un código NCM contra el arancel (solo lectura)
# ======================================================================================

def evaluar(idx: Dict[str, Any], codigo_ncm: Optional[str], tasa_arancel: Optional[float]) -> Dict[str, Any]:
    """
    Estados: ok | tasa_distinta | sin_tasa (el arancel no trae ANV numérico) | no_existe | sin_ncm.
    `tasa_arancel` viene del PDF como fracción (0.06 = 6%).
    """
    d = digitos(codigo_ncm)
    if not d:
        return {"estado": "sin_ncm", "texto": "El ítem no tiene código NCM."}
    if len(d) < 8:
        return {"estado": "no_existe", "texto": f"Código incompleto ({len(d)} dígitos; el NCM tiene 8)."}
    ncm = idx["por_d"].get(d[:8])
    if ncm is None or ncm["n"] != "ncm":
        if d[:6] in idx["por_d"]:
            motivo = (f"La subpartida {d[:4]}.{d[4:6]} existe, pero el NCM {formatear_ncm(d[:8])} "
                      "no figura en el arancel vigente.")
        else:
            motivo = ("La subpartida no existe en el arancel vigente (posible error de lectura del PDF "
                      "o código de una nomenclatura anterior).")
        return {"estado": "no_existe", "texto": motivo, "d8": d[:8]}

    apertura = idx["por_d"].get(d[:11]) if len(d) >= 11 else None
    if apertura is not None and apertura["n"] != "apertura":
        apertura = None
    res = {"d8": ncm["d"], "codigo_oficial": ncm["c"], "anv": ncm.get("anv"), "anv_t": ncm.get("anv_t")}
    if tasa_arancel is None:
        return {**res, "estado": "ok", "texto": f"NCM vigente · ANV {_pct(ncm.get('anv'))}."}
    tasa = round(float(tasa_arancel) * 100, 2)
    res["tasa_pdf"] = tasa
    validas = [x for x in (ncm.get("anv"), apertura.get("anv") if apertura else None) if x is not None]
    if not validas:
        return {**res, "estado": "sin_tasa",
                "texto": f"NCM vigente; el arancel no trae una tasa ANV numérica ({ncm.get('anv_t') or 'vacía'}). "
                         f"Tasa en el PDF: {_pct(tasa)}."}
    if any(abs(tasa - x) <= TOLERANCIA_TASA for x in validas):
        return {**res, "estado": "ok", "texto": f"NCM vigente · tasa {_pct(tasa)} = ANV."}
    return {**res, "estado": "tasa_distinta",
            "texto": f"Tasa en el PDF {_pct(tasa)} · ANV vigente {_pct(ncm.get('anv'))}. Puede ser un régimen "
                     "especial o un cambio de tasa posterior al despacho."}


def validar_items(db: Session, items: Iterable[DespachoItem]) -> Optional[Dict[str, Any]]:
    """Validación de los ítems de un despacho para mostrar en su detalle (no guarda nada)."""
    idx = indice(db)
    if idx is None:
        return None
    por_item = {}
    resumen: Counter = Counter()
    for it in items:
        ev = evaluar(idx, it.codigo_ncm, it.tasa_arancel)
        por_item[it.id] = ev
        resumen[ev["estado"]] += 1
    return {"por_item": por_item, "resumen": dict(resumen), "version": idx["version"]["nombre"]}


# ======================================================================================
# Uso del arancel en los despachos (agregado, en caché hasta que cambien los datos)
# ======================================================================================

def uso_en_despachos(db: Session) -> Dict[str, Any]:
    idx = indice(db)
    if idx is None:
        return {"por_ncm": {}, "no_encontrados": [], "tasa_distinta": [], "kpi": {}}
    huella_items = tuple(db.execute(text("SELECT count(*), max(id), total(valor_total) FROM despacho_items")).one())
    huella_desp = tuple(str(x) for x in db.execute(text("SELECT count(*), max(updated_at) FROM despachos")).one())
    clave = (idx["version_id"],) + huella_items + huella_desp
    if _estado["uso_clave"] == clave and _estado["uso"] is not None:
        return _estado["uso"]

    filas = (
        db.query(DespachoItem.codigo_ncm, DespachoItem.despacho_id, DespachoItem.valor_total, DespachoItem.marca,
                 DespachoItem.tasa_arancel, Despacho.numero_despacho, Despacho.fecha_despacho)
        .join(Despacho, Despacho.id == DespachoItem.despacho_id)
        .filter(or_(Despacho.estado_procesamiento.is_(None), Despacho.estado_procesamiento != "DUPLICADO"))
        .all()
    )
    por_ncm: Dict[str, Dict[str, Any]] = {}
    no_enc: Dict[str, Dict[str, Any]] = {}
    tasa_dist: Dict[str, Dict[str, Any]] = {}
    kpi: Counter = Counter()
    for codigo, desp_id, valor, marca, tasa, numero, fecha in filas:
        ev = evaluar(idx, codigo, tasa)
        kpi[ev["estado"]] += 1
        valor = valor or 0.0
        if ev["estado"] in ("ok", "tasa_distinta", "sin_tasa"):
            u = por_ncm.setdefault(ev["d8"], {"ni": 0, "fob": 0.0, "marcas": Counter(), "tasas": Counter(),
                                              "ult": None, "desp": {}})
            u["ni"] += 1
            u["fob"] += valor
            m = (marca or "").strip().upper()
            if m not in MARCAS_GENERICAS:
                u["marcas"][m] += 1
            if "tasa_pdf" in ev:
                u["tasas"][ev["tasa_pdf"]] += 1
            if fecha and (u["ult"] is None or fecha > u["ult"]):
                u["ult"] = fecha
            dd = u["desp"].setdefault(desp_id, [numero, fecha, 0, 0.0])
            dd[2] += 1
            dd[3] += valor
            if ev["estado"] == "tasa_distinta":
                t = tasa_dist.setdefault(ev["d8"], {"tasas": Counter(), "ni": 0, "fob": 0.0, "desp": {}})
                t["tasas"][ev["tasa_pdf"]] += 1
                t["ni"] += 1
                t["fob"] += valor
                t["desp"][desp_id] = numero
        elif ev["estado"] == "no_existe":
            cod = re.sub(r"\s+", "", codigo or "")
            g = no_enc.setdefault(cod, {"motivo": ev["texto"], "ni": 0, "fob": 0.0, "desp": {}})
            g["ni"] += 1
            g["fob"] += valor
            g["desp"][desp_id] = numero

    def ejemplos(desp: Dict[int, Any], n: int = 8):
        return [{"id": i, "numero": num or f"ID {i}"} for i, num in sorted(desp.items(), key=lambda x: -x[0])[:n]]

    resultado = {
        "por_ncm": por_ncm,
        "no_encontrados": sorted(
            [{"codigo": c or "(vacío)", "motivo": g["motivo"], "items": g["ni"], "despachos": len(g["desp"]),
              "fob": round(g["fob"], 2), "ejemplos": ejemplos(g["desp"])} for c, g in no_enc.items()],
            key=lambda x: -x["fob"]),
        "tasa_distinta": sorted(
            [{"d": d, "c": idx["por_d"][d]["c"], "t": idx["por_d"][d]["t"], "anv": idx["por_d"][d].get("anv"),
              "tasas_pdf": sorted(t["tasas"].items(), key=lambda x: -x[1]), "items": t["ni"],
              "despachos": len(t["desp"]), "fob": round(t["fob"], 2), "ejemplos": ejemplos(t["desp"])}
             for d, t in tasa_dist.items()],
            key=lambda x: -x["fob"]),
        "kpi": {
            "items": sum(kpi.values()),
            "items_validos": kpi["ok"] + kpi["tasa_distinta"] + kpi["sin_tasa"],
            "items_no_existe": kpi["no_existe"], "items_sin_ncm": kpi["sin_ncm"],
            "items_tasa_distinta": kpi["tasa_distinta"], "items_sin_tasa": kpi["sin_tasa"],
            "ncm_usados": len(por_ncm),
        },
    }
    with _LOCK:
        _estado["uso"] = resultado
        _estado["uso_clave"] = clave
    return resultado


def _uso_resumido(u: Optional[Dict[str, Any]], detalle: bool = False) -> Optional[Dict[str, Any]]:
    if not u:
        return None
    r = {"items": u["ni"], "despachos": len(u["desp"]), "fob": round(u["fob"], 2),
         "ultimo": u["ult"].isoformat() if u["ult"] else None,
         "marcas": [m for m, _ in u["marcas"].most_common(6)],
         "tasas_pdf": sorted(u["tasas"].items(), key=lambda x: -x[1])}
    if detalle:
        recientes = sorted(u["desp"].items(), key=lambda x: (x[1][1] or date.min, x[0]), reverse=True)[:15]
        r["despachos_recientes"] = [
            {"id": i, "numero": v[0] or f"ID {i}", "fecha": v[1].isoformat() if v[1] else None,
             "items": v[2], "fob": round(v[3], 2)} for i, v in recientes]
    return r


# ======================================================================================
# Consultas para la página
# ======================================================================================

def _fila(n: Dict[str, Any], por_ncm: Dict[str, Any]) -> Dict[str, Any]:
    u = por_ncm.get(n["d"]) if n["n"] == "ncm" else None
    return {"c": n["c"], "d": n["d"], "n": n["n"], "t": n["t"], "ruta": n.get("ruta", ""), "anv": n.get("anv"),
            "anv_t": n.get("anv_t"), "iva_t": n.get("iva_t"), "lista": n.get("lista"),
            "uso_items": u["ni"] if u else 0}


def resumen(db: Session) -> Optional[Dict[str, Any]]:
    idx = indice(db)
    if idx is None:
        return None
    uso = uso_en_despachos(db)
    fichas = idx["fichas"]
    tc = (db.query(Despacho.tipo_cambio, Despacho.fecha_despacho)
          .filter(Despacho.tipo_cambio > 0).order_by(Despacho.fecha_despacho.desc()).first())
    listas = Counter(n.get("lista") for n in fichas if n["n"] == "ncm" and n.get("lista"))
    return {
        "version": idx["version"],
        "kpi": {**uso["kpi"], "ncm": sum(1 for n in fichas if n["n"] == "ncm"),
                "aperturas": sum(1 for n in fichas if n["n"] == "apertura"),
                "codigos_no_encontrados": len(uso["no_encontrados"]),
                "ncm_tasa_distinta": len(uso["tasa_distinta"])},
        "capitulos": [{"numero": c["numero"], "seccion": c["seccion"], "titulo": c["titulo"]} for c in idx["caps"].values()],
        "secciones": [{"numero": s["numero"], "titulo": s["titulo"]} for s in idx["secs"].values()],
        "listas": [{"lista": k, "ncm": v, "descripcion": LISTAS_OFICIALES.get(k)} for k, v in sorted(listas.items())],
        "tc": {"valor": tc[0] if tc else None, "fecha": tc[1].isoformat() if tc and tc[1] else None},
    }


def buscar(db: Session, q: str = "", capitulo: str = "", lista: str = "", solo_mios: bool = False,
           limite: int = 200) -> Dict[str, Any]:
    idx = indice(db)
    if idx is None:
        return {"total": 0, "sin_filtros": True, "resultados": []}
    por_ncm = uso_en_despachos(db)["por_ncm"]
    qn = normalizar((q or "").strip())
    sin_filtros = not (qn or capitulo or lista or solo_mios)
    if sin_filtros:
        res = sorted((n for n in idx["fichas"] if n["n"] == "ncm" and n["d"] in por_ncm),
                     key=lambda n: -por_ncm[n["d"]]["fob"])
    else:
        tokens = qn.split()
        es_codigo = bool(qn) and re.fullmatch(r"[\d.\s]+", qn) is not None
        q_dig = digitos(qn)
        res = []
        for n in idx["fichas"]:
            if capitulo and n["cap"] != capitulo:
                continue
            if lista and n.get("lista") != lista:
                continue
            if solo_mios and not (n["n"] == "ncm" and n["d"] in por_ncm):
                continue
            if qn:
                if es_codigo:
                    if not n["d"].startswith(q_dig):
                        continue
                elif not all(t in n["busq"] for t in tokens):
                    continue
            res.append(n)
        if qn and not es_codigo:
            # Primero los que tienen las palabras en su propia descripción (no solo en la partida),
            # y dentro de eso los NCM que usás, por valor importado. El orden por código se mantiene.
            def prioridad(n):
                u = por_ncm.get(n["d"]) if n["n"] == "ncm" else None
                return (0 if all(t in n["tn"] for t in tokens) else 1, 0 if u else 1, -(u["fob"] if u else 0))
            res.sort(key=prioridad)
    return {"total": len(res), "sin_filtros": sin_filtros, "resultados": [_fila(n, por_ncm) for n in res[:limite]]}


def ficha(db: Session, codigo: str) -> Optional[Dict[str, Any]]:
    idx = indice(db)
    if idx is None:
        return None
    d = digitos(codigo)
    n = idx["por_d"].get(d)
    if (n is None or n["n"] not in ("ncm", "apertura")) and len(d) > 8:
        n = idx["por_d"].get(d[:11]) or idx["por_d"].get(d[:8])
    if n is None or n["n"] not in ("ncm", "apertura"):
        return None
    por_ncm = uso_en_despachos(db)["por_ncm"]
    cap = idx["caps"].get(n["cap"]) or {"numero": n["cap"], "seccion": None, "titulo": "", "notas": ""}
    sec = idx["secs"].get(cap.get("seccion")) if cap.get("seccion") else None

    # Símbolos de las tasas: "1,5*" -> leyenda "*"; "5 (a)" -> leyenda "a"; lista "AZ*" -> leyenda "AZ*"
    impuestos_txt = " ".join(x or "" for x in (n.get("iva_t"), n.get("renta_t"), n.get("isc_t")))
    leyendas = {}
    for simbolo, texto in (idx["leyendas"] or {}).items():
        if simbolo == "*":
            aplica = "*" in impuestos_txt
        else:
            aplica = f"({simbolo})" in impuestos_txt or simbolo == n.get("lista")
        if aplica:
            leyendas[simbolo] = texto

    u = por_ncm.get(n["d"][:8])
    hermanos = [{"c": h["c"], "d": h["d"], "t": h["t"], "anv": h.get("anv")}
                for h in idx["por_sub"].get(n["d"][:6], []) if h["d"] != n["d"]]
    aperturas = [{"c": a["c"], "d": a["d"], "t": a["t"], "anv": a.get("anv")}
                 for a in idx["aperturas"].get(n["d"][:8], []) if a["d"] != n["d"]]
    campos = {k: n.get(k) for k in ("c", "d", "n", "t", "g", "aec", "aec_t", "anv", "anv_t", "anv_dec", "lista",
                                     "iva", "iva_t", "renta", "renta_t", "isc", "isc_t", "omc", "ai", "ae", "obs",
                                     "fi", "pag", "ruta", "cap")}
    return {
        **campos,
        "lista_descripcion": LISTAS_OFICIALES.get(n.get("lista") or ""),
        "ancestros": [{"c": idx["por_d"][a]["c"], "d": a, "t": idx["por_d"][a]["t"], "n": idx["por_d"][a]["n"]}
                      for a in n.get("anc", [])],
        "capitulo": cap, "seccion": sec, "leyendas": leyendas,
        "hermanos": hermanos, "aperturas": aperturas,
        "uso": _uso_resumido(u, detalle=True),
        "version": idx["version"],
    }


def capitulo(db: Session, numero: str) -> Optional[Dict[str, Any]]:
    idx = indice(db)
    if idx is None:
        return None
    numero = (numero or "").zfill(2)[:2]
    if numero not in idx["caps"] and not any(n["d"][:2] == numero for n in idx["nodos"]):
        return None
    por_ncm = uso_en_despachos(db)["por_ncm"]
    cap = idx["caps"].get(numero) or {"numero": numero, "seccion": None, "titulo": "", "notas": ""}
    nodos = [{"c": n["c"], "d": n["d"], "n": n["n"], "t": n["t"], "anv": n.get("anv"), "iva_t": n.get("iva_t"),
              "uso_items": por_ncm[n["d"]]["ni"] if n["n"] == "ncm" and n["d"] in por_ncm else 0}
             for n in idx["nodos"] if n["d"][:2] == numero]
    return {"capitulo": cap, "seccion": idx["secs"].get(cap.get("seccion")) if cap.get("seccion") else None,
            "nodos": nodos}


def mis_ncm(db: Session) -> List[Dict[str, Any]]:
    idx = indice(db)
    if idx is None:
        return []
    uso = uso_en_despachos(db)
    por_ncm = uso["por_ncm"]
    # Misma regla que la validación (acepta también la tasa de una apertura nacional)
    con_tasa_distinta = {x["d"] for x in uso["tasa_distinta"]}
    filas = []
    for d, u in por_ncm.items():
        n = idx["por_d"].get(d)
        if not n:
            continue
        filas.append({"c": n["c"], "d": d, "t": n["t"], "ruta": n.get("ruta", ""), "anv": n.get("anv"),
                      "iva_t": n.get("iva_t"), "lista": n.get("lista"), "uso": _uso_resumido(u),
                      "tasa_distinta": d in con_tasa_distinta})
    filas.sort(key=lambda x: -x["uso"]["fob"])
    return filas


def validacion_global(db: Session) -> Dict[str, Any]:
    idx = indice(db)
    if idx is None:
        return {"version": None, "kpi": {}, "no_encontrados": [], "tasa_distinta": []}
    uso = uso_en_despachos(db)
    return {"version": idx["version"], "kpi": uso["kpi"], "no_encontrados": uso["no_encontrados"],
            "tasa_distinta": uso["tasa_distinta"]}


# ======================================================================================
# Versiones: listar, importar un PDF nuevo del MEF, activar, eliminar
# ======================================================================================

def listar_versiones(db: Session) -> List[Dict[str, Any]]:
    asegurar_arancel(db)
    return [_version_dict(v) for v in db.query(ArancelVersion).order_by(ArancelVersion.fecha_vigencia.desc(),
                                                                         ArancelVersion.id.desc()).all()]


def _complemento_desde_version(db: Session, version: ArancelVersion) -> Dict[str, Any]:
    """Convierte una versión guardada al formato de nodos del importador (para heredar IVA/ISC/notas)."""
    inverso = {col: clave for clave, col in _COLUMNAS.items()}
    nodos = []
    for p in db.query(ArancelPosicion).filter(ArancelPosicion.version_id == version.id,
                                              ArancelPosicion.nivel.in_(("ncm", "apertura"))):
        nodo = {}
        for col, clave in inverso.items():
            valor = getattr(p, col)
            if valor not in (None, "", []):
                nodo[clave] = valor
        nodos.append(nodo)
    return {
        "secciones": [{"numero": s.numero, "titulo": s.titulo, "notas": s.notas}
                      for s in db.query(ArancelSeccion).filter(ArancelSeccion.version_id == version.id).order_by(ArancelSeccion.id)],
        "capitulos": [{"numero": c.numero, "seccion": c.seccion, "titulo": c.titulo, "notas": c.notas}
                      for c in db.query(ArancelCapitulo).filter(ArancelCapitulo.version_id == version.id)],
        "leyendas": version.leyendas or {},
        "nodos": nodos,
    }


def diferencias(db: Session, nueva_id: int, base_id: Optional[int]) -> Dict[str, Any]:
    """Qué cambia entre dos versiones (solo NCM de 8 dígitos)."""
    def ncm_de(version_id):
        return {d: (c, t, anv) for d, c, t, anv in db.query(ArancelPosicion.digitos, ArancelPosicion.codigo,
                                                            ArancelPosicion.descripcion, ArancelPosicion.anv)
                .filter(ArancelPosicion.version_id == version_id, ArancelPosicion.nivel == "ncm")}
    nueva = ncm_de(nueva_id)
    if not base_id:
        return {"base": None, "nuevos": len(nueva), "eliminados": 0, "anv_cambiados": 0, "ejemplos": []}
    base = ncm_de(base_id)
    nuevos = sorted(set(nueva) - set(base))
    eliminados = sorted(set(base) - set(nueva))
    cambios = sorted(d for d in set(nueva) & set(base) if nueva[d][2] != base[d][2])
    por_ncm = uso_en_despachos(db)["por_ncm"]
    # Primero los que te afectan (NCM que usás en tus despachos)
    cambios.sort(key=lambda d: -(por_ncm[d]["fob"] if d in por_ncm else 0))
    return {
        "base": base_id, "nuevos": len(nuevos), "eliminados": len(eliminados), "anv_cambiados": len(cambios),
        "eliminados_usados": [base[d][0] for d in eliminados if d in por_ncm][:30],
        "ejemplos": [{"c": nueva[d][0], "t": nueva[d][1], "antes": base[d][2], "ahora": nueva[d][2],
                      "lo_usas": d in por_ncm} for d in cambios[:40]],
    }


def importar_pdf_mef(db: Session, ruta_pdf, nombre: str, fecha_vigencia: Optional[str],
                     ruta_excel=None, nombre_pdf: str = "", nombre_excel: str = "") -> Dict[str, Any]:
    """
    Crea una versión NUEVA (inactiva) desde un PDF del MEF. IVA/Renta/ISC y notas legales se toman
    del Excel si se envía, o de la versión activa. Devuelve la versión y sus diferencias.
    """
    nodos = parsear_pdf_mef(ruta_pdf)
    activa = asegurar_arancel(db)
    if ruta_excel:
        complemento, fuente_comp = parsear_excel_aec(ruta_excel), f"Excel {nombre_excel or 'AEC'}"
    elif activa is not None:
        complemento, fuente_comp = _complemento_desde_version(db, activa), f"versión anterior ({activa.nombre})"
    else:
        complemento, fuente_comp = None, ""
    fuente = nombre_pdf or Path(str(ruta_pdf)).name
    if ruta_excel:
        fuente += f" + {nombre_excel} (IVA, Renta, ISC, notas legales)"
    elif activa is not None:
        fuente += f" + IVA/Renta/ISC y notas de: {activa.nombre}"
    dataset = construir_dataset(nodos, complemento,
                                meta={"nombre": nombre, "fecha_vigencia": fecha_vigencia, "fuente": fuente},
                                fuente_complemento=fuente_comp[:80])
    validar_dataset(dataset)
    ya_existia = db.query(ArancelVersion.id).filter(ArancelVersion.hash_contenido == dataset["meta"]["hash"]).first()
    version = cargar_dataset(db, dataset, origen="importado", activar=False)
    return {"version": _version_dict(version), "ya_existia": bool(ya_existia),
            "diferencias": diferencias(db, version.id, activa.id if activa else None)}


def activar_version(db: Session, version_id: int) -> Dict[str, Any]:
    v = db.query(ArancelVersion).filter(ArancelVersion.id == version_id).first()
    if v is None:
        raise LookupError("Versión no encontrada")
    _activar(db, v)
    db.commit()
    _invalidar_cache()
    return _version_dict(v)


def eliminar_version(db: Session, version_id: int) -> None:
    v = db.query(ArancelVersion).filter(ArancelVersion.id == version_id).first()
    if v is None:
        raise LookupError("Versión no encontrada")
    if v.activa:
        raise ValueError("No se puede eliminar la versión activa: active otra primero.")
    _borrar_version(db, v)
    db.commit()
    db.expire_all()
    _invalidar_cache()
