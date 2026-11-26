"""
Inteligencia Artificial con Claude (Anthropic): asistente de clasificación arancelaria (NCM).

Funcionamiento (dos pasos, siempre dentro del arancel vigente cargado en la PC):
  1. Claude lee la lista de TODAS las partidas (4 dígitos) y elige las que podrían corresponder.
     Esa lista es fija por versión del arancel y se envía con caché (las consultas siguientes cuestan
     una fracción).
  2. El sistema arma las posiciones completas (NCM y aperturas) de esas partidas, con tasas, notas
     legales de los capítulos y el historial de tus despachos, y Claude elige hasta 3 NCM.
Toda sugerencia se valida contra el arancel local: un código que no está en la lista se descarta.

Qué se envía a Anthropic: la descripción que escribe el usuario, texto del arancel y hasta 2
descripciones de productos de tus despachos por NCM candidato. Nunca importadores, clientes ni valores.
"""
import json
import logging
import math
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.config import settings
from app.models import DespachoItem
from app.services import arancel_service
from app.services.arancel_service import digitos, normalizar

logger = logging.getLogger(__name__)

MODELO = "claude-opus-5-5"
# Precios por millón de tokens de claude-opus-5-5 (para mostrar un costo ESTIMADO de cada consulta)
_PRECIO = {"entrada": 4.00, "salida": 20.00, "cache_escritura": 5.00, "cache_escritura_1h": 8.00, "cache_lectura": 0.20}
MAX_PARTIDAS = 6
MAX_SUGERENCIAS = 3
MAX_LINEAS_CANDIDATOS = 450
MAX_NOTAS_CAPITULO = 9000  # caracteres por capítulo

_fallbacks_disponible = True  # se apaga solo si la API rechazara el parámetro
_cache_partidas: Dict[str, Any] = {"version_id": None, "texto": None}
_lock = threading.Lock()


class IAError(Exception):
    """Error con un mensaje listo para mostrar al usuario y un código HTTP sugerido."""

    def __init__(self, mensaje: str, status: int = 502):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.status = status


# ======================================================================================
# Clave de API
# ======================================================================================

def clave_api() -> str:
    return (os.getenv("ANTHROPIC_API_KEY") or getattr(settings, "ANTHROPIC_API_KEY", "") or "").strip().strip("'\"")


def clave_enmascarada(clave: Optional[str] = None) -> Optional[str]:
    clave = clave if clave is not None else clave_api()
    if not clave:
        return None
    return f"{clave[:7]}…{clave[-4:]}" if len(clave) > 14 else "…"


def esta_configurada() -> bool:
    return bool(clave_api())


def _sdk():
    try:
        import anthropic
        return anthropic
    except ImportError as e:
        raise IAError("Falta instalar el paquete de Anthropic en esta PC (pip install -r requirements.txt).", 500) from e


def _cliente(clave: Optional[str] = None):
    anthropic = _sdk()
    clave = clave or clave_api()
    if not clave:
        raise IAError("La IA no está configurada: ingrese la API key de Anthropic en Configuración.", 400)
    return anthropic.Anthropic(api_key=clave, timeout=180.0, max_retries=2)


def _traducir_error(e: Exception) -> IAError:
    anthropic = _sdk()
    if isinstance(e, IAError):
        return e
    if isinstance(e, anthropic.AuthenticationError):
        return IAError("La API key de Anthropic no es válida o fue revocada.", 400)
    if isinstance(e, anthropic.PermissionDeniedError):
        return IAError("La API key no tiene permiso para usar este modelo (revise su cuenta de Anthropic).", 400)
    if isinstance(e, anthropic.NotFoundError):
        return IAError(f"El modelo {MODELO} no está disponible para esta cuenta de Anthropic.", 400)
    if isinstance(e, anthropic.RateLimitError):
        return IAError("Se alcanzó el límite de consultas de la cuenta de Anthropic. Intente en un minuto.", 429)
    if isinstance(e, anthropic.APIConnectionError):  # incluye tiempo de espera agotado
        return IAError("No hay conexión con Anthropic (revise internet) o tardó demasiado. Intente de nuevo.", 503)
    if isinstance(e, anthropic.BadRequestError):
        return IAError(f"Anthropic rechazó la consulta: {getattr(e, 'message', str(e))[:300]}", 400)
    if isinstance(e, anthropic.APIStatusError):
        return IAError(f"Anthropic devolvió un error ({e.status_code}). Intente más tarde.", 502)
    return IAError(f"Error inesperado al consultar la IA: {type(e).__name__}: {str(e)[:200]}", 500)


def probar_clave(clave: Optional[str] = None) -> Dict[str, Any]:
    """Verifica la clave sin gastar tokens: consulta los datos del modelo (Models API, gratuita)."""
    try:
        modelo = _cliente(clave).with_options(timeout=20.0, max_retries=1).models.retrieve(MODELO)
        return {"ok": True, "modelo": MODELO, "nombre_modelo": getattr(modelo, "display_name", MODELO),
                "mensaje": f"Clave válida. Modelo disponible: {getattr(modelo, 'display_name', MODELO)}."}
    except Exception as e:  # noqa: BLE001 - se traduce a un mensaje claro
        err = _traducir_error(e)
        return {"ok": False, "mensaje": err.mensaje, "sin_conexion": err.status == 503}


# ======================================================================================
# Llamada a Claude con salida JSON validada por esquema
# ======================================================================================

def _llamar(cliente, *, system: List[Dict[str, Any]], mensaje: Any, esquema: Dict[str, Any],
            effort: str, max_tokens: int = 16000, streaming: bool = False) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """
    `mensaje` es texto o una lista de bloques (p. ej. un PDF + instrucciones).
    Con `streaming` (respuestas largas) se usa stream + get_final_message para no agotar el tiempo de espera.
    """
    global _fallbacks_disponible
    anthropic = _sdk()
    kwargs = dict(
        model=MODELO,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": mensaje}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": esquema}},
    )

    def ejecutar(api, **extra):
        if streaming:
            with api.stream(**extra, **kwargs) as s:
                return s.get_final_message()
        return api.create(**extra, **kwargs)

    try:
        if _fallbacks_disponible:
            try:
                # Si el modelo declina la consulta, Anthropic la reintenta en otro modelo (fallbacks "default").
                resp = ejecutar(cliente.beta.messages, betas=["server-side-fallback-2026-07-01"],
                                extra_body={"fallbacks": "default"})
            except anthropic.BadRequestError as e:
                if "fallback" not in str(e).lower():
                    raise
                _fallbacks_disponible = False
                resp = ejecutar(cliente.messages)
        else:
            resp = ejecutar(cliente.messages)
    except Exception as e:  # noqa: BLE001
        raise _traducir_error(e) from e

    if resp.stop_reason == "refusal":
        raise IAError("La IA no respondió esta consulta. Reformule la descripción del producto.", 422)
    if resp.stop_reason == "max_tokens":
        raise IAError("La respuesta de la IA quedó incompleta. Intente con una descripción más concreta.", 502)
    texto = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
    try:
        datos = json.loads(texto or "")
    except (TypeError, ValueError) as e:
        raise IAError("La IA devolvió una respuesta que no se pudo leer. Intente de nuevo.", 502) from e
    u = resp.usage
    escrito = getattr(u, "cache_creation_input_tokens", 0) or 0
    escrito_1h = getattr(getattr(u, "cache_creation", None), "ephemeral_1h_input_tokens", 0) or 0
    uso = {"entrada": getattr(u, "input_tokens", 0) or 0, "salida": getattr(u, "output_tokens", 0) or 0,
           "cache_escritura": max(0, escrito - escrito_1h), "cache_escritura_1h": escrito_1h,
           "cache_lectura": getattr(u, "cache_read_input_tokens", 0) or 0}
    return datos, uso


# ======================================================================================
# Material del arancel para los prompts
# ======================================================================================

_SISTEMA_BASE = (
    "Sos un asistente experto en clasificación arancelaria de mercaderías para un despachante de aduanas "
    "de Paraguay. Se usa la Nomenclatura Común del MERCOSUR (NCM 2022, VII Enmienda del Sistema Armonizado) "
    "y las Reglas Generales Interpretativas (RGI): la clasificación se determina por los textos de las "
    "partidas y las notas de sección y de capítulo (RGI 1); la partida más específica prevalece y, en "
    "productos compuestos o surtidos, el carácter esencial (RGI 3); a igualdad, la última en orden "
    "numérico; las subpartidas se comparan solo entre las del mismo nivel (RGI 6). "
    "Nunca inventes códigos: elegí solamente códigos que aparecen en el material que se te da. "
    "Si la descripción es ambigua, decilo y pedí el dato que falta en lugar de adivinar. "
    "El historial de despachos del usuario es una pista, no una prueba: puede contener errores. "
    "Respondé en español, de forma breve y concreta."
)

_ESQUEMA_PARTIDAS = {
    "type": "object",
    "properties": {
        "interpretacion": {"type": "string",
                           "description": "Qué es el producto (material, función, uso), en una o dos frases."},
        "partidas": {
            "type": "array",
            "description": "De 1 a 6 partidas de la lista, de la más probable a la menos probable.",
            "items": {
                "type": "object",
                "properties": {"codigo": {"type": "string", "description": "Partida tal como figura, ej. 85.18"},
                               "motivo": {"type": "string"}},
                "required": ["codigo", "motivo"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["interpretacion", "partidas"],
    "additionalProperties": False,
}

_ESQUEMA_NCM = {
    "type": "object",
    "properties": {
        "sugerencias": {
            "type": "array",
            "description": "De 1 a 3 NCM del material, de la más probable a la menos probable.",
            "items": {
                "type": "object",
                "properties": {
                    "ncm": {"type": "string", "description": "Código exacto del material, ej. 8518.30.00"},
                    "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
                    "fundamento": {"type": "string", "description": "Por qué corresponde (RGI, texto de la partida)."},
                    "notas_aplicadas": {"type": "string",
                                        "description": "Notas legales de sección/capítulo que lo sostienen o descartan otras opciones; vacío si ninguna."},
                    "por_que_no_otras": {"type": "string",
                                         "description": "Por qué no las alternativas cercanas; vacío si no aplica."},
                },
                "required": ["ncm", "confianza", "fundamento", "notas_aplicadas", "por_que_no_otras"],
                "additionalProperties": False,
            },
        },
        "informacion_faltante": {"type": "array", "items": {"type": "string"},
                                 "description": "Datos del producto que cambiarían la clasificación (vacío si ninguno)."},
        "advertencia": {"type": "string", "description": "Advertencia importante o vacío."},
    },
    "required": ["sugerencias", "informacion_faltante", "advertencia"],
    "additionalProperties": False,
}


def _texto_partidas(idx: Dict[str, Any]) -> str:
    """Lista de todas las partidas por capítulo. Fija por versión: es el bloque que se guarda en caché."""
    with _lock:
        if _cache_partidas["version_id"] == idx["version_id"]:
            return _cache_partidas["texto"]
        lineas, cap_actual = [], None
        for n in idx["nodos"]:
            if n["n"] != "partida":
                continue
            cap = n["d"][:2]
            if cap != cap_actual:
                titulo = (idx["caps"].get(cap) or {}).get("titulo", "")
                lineas.append(f"\nCapítulo {cap}: {titulo}")
                cap_actual = cap
            texto = (n["t"] or "").strip()
            lineas.append(f"{n['c']} {texto[:180]}{'…' if len(texto) > 180 else ''}")
        texto = (f"LISTA DE PARTIDAS DEL ARANCEL VIGENTE ({idx['version']['nombre']}):\n" + "\n".join(lineas)).strip()
        _cache_partidas.update(version_id=idx["version_id"], texto=texto)
        return texto


def _sistema_partidas(idx: Dict[str, Any], tarea: str) -> List[Dict[str, Any]]:
    """
    Instrucciones + lista completa de partidas (~65.000 tokens). La lista va ANTES de la tarea y con
    caché de 1 hora: "Sugerir NCM" y "Separar lista" comparten la misma caché, y las consultas de la
    misma hora cuestan una fracción (la primera de cada hora paga la escritura de la caché).
    """
    return [
        {"type": "text", "text": _SISTEMA_BASE},
        {"type": "text", "text": _texto_partidas(idx), "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": tarea},
    ]


def _pct(v) -> str:
    return "—" if v is None else (f"{v:.2f}".rstrip("0").rstrip(".") + "%").replace(".", ",")


def _antecedentes(db: Session, texto: str, idx: Dict[str, Any], limite: int = 8) -> List[Dict[str, Any]]:
    """NCM con que se declararon productos de descripción parecida en los despachos del usuario."""
    tokens = [t for t in normalizar(texto).split() if len(t) >= 3 and not t.isdigit()][:8]
    if not tokens:
        return []
    filtro = sorted(tokens, key=len, reverse=True)[:2]
    q = db.query(DespachoItem.codigo_ncm, DespachoItem.descripcion)
    for t in filtro:
        t_sql = t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        q = q.filter(DespachoItem.descripcion.ilike(f"%{t_sql}%", escape="\\"))
    minimo = max(1, (len(tokens) + 1) // 2)
    grupos: Dict[str, Dict[str, Any]] = {}
    for codigo, desc in q.limit(4000):
        dn = normalizar(desc)
        puntaje = sum(1 for t in tokens if t in dn)
        d8 = digitos(codigo)[:8]
        nodo = idx["por_d"].get(d8)
        if puntaje < minimo or nodo is None or nodo["n"] != "ncm":
            continue
        g = grupos.setdefault(d8, {"ncm": nodo["c"], "lineas": 0, "puntaje": 0, "ejemplos": []})
        g["lineas"] += 1
        g["puntaje"] = max(g["puntaje"], puntaje)
        corto = re.sub(r"\s+", " ", desc or "").strip()[:90]
        if corto and corto not in g["ejemplos"] and len(g["ejemplos"]) < 2:
            g["ejemplos"].append(corto)
    return sorted(grupos.values(), key=lambda g: (-g["puntaje"], -g["lineas"]))[:limite]


def _ejemplos_por_ncm(db: Session, candidatos: set, por_ncm: int = 2) -> Dict[str, List[str]]:
    ejemplos: Dict[str, List[str]] = {}
    for codigo, desc in db.query(DespachoItem.codigo_ncm, DespachoItem.descripcion).filter(DespachoItem.codigo_ncm.isnot(None)):
        d8 = digitos(codigo)[:8]
        if d8 in candidatos:
            lista = ejemplos.setdefault(d8, [])
            corto = re.sub(r"\s+", " ", desc or "").strip()[:90]
            if corto and corto not in lista and len(lista) < por_ncm:
                lista.append(corto)
    return ejemplos


def _material_candidatos(db: Session, idx: Dict[str, Any], partidas: List[str],
                         max_lineas: int = MAX_LINEAS_CANDIDATOS) -> Tuple[str, set]:
    """Texto con las posiciones completas de las partidas elegidas, sus tasas, notas e historial."""
    uso = arancel_service.uso_en_despachos(db)["por_ncm"]
    validos: set = set()
    nodos_por_partida = {p: [n for n in idx["nodos"] if n["d"].startswith(p) and n["d"] != p] for p in partidas}
    ncm_candidatos = {n["d"] for ns in nodos_por_partida.values() for n in ns if n["n"] == "ncm"}
    ejemplos = _ejemplos_por_ncm(db, ncm_candidatos)
    sangria = {"subpartida": "  ", "agrupacion": "    ", "ncm": "    ", "apertura": "      "}

    bloques, total = [], 0
    for p in partidas:
        partida = idx["por_d"][p]
        cap = (idx["caps"].get(p[:2]) or {})
        bloques.append(f"\n=== Capítulo {p[:2]} ({cap.get('titulo', '')}) · Partida {partida['c']}: {partida['t']}")
        for n in nodos_por_partida[p]:
            if total >= max_lineas:
                bloques.append("  (lista recortada por longitud)")
                break
            linea = f"{sangria.get(n['n'], '  ')}{n['c']} {n['t']}"
            if n["n"] in ("ncm", "apertura"):
                validos.add(n["d"])
                datos = [f"ANV {_pct(n.get('anv')) if n.get('anv') is not None else (n.get('anv_t') or '—')}"]
                if n.get("iva_t"):
                    datos.append(f"IVA {n['iva_t']}")
                if n.get("lista"):
                    datos.append(f"lista {n['lista']}")
                linea += f"  [{' | '.join(datos)}]"
                u = uso.get(n["d"]) if n["n"] == "ncm" else None
                if u:
                    linea += f"  · en tus despachos: {u['ni']} líneas"
                    if ejemplos.get(n["d"]):
                        linea += " (ej.: " + "; ".join(f"'{e}'" for e in ejemplos[n["d"]]) + ")"
            bloques.append(linea)
            total += 1

    notas = []
    for cap_num in sorted({p[:2] for p in partidas}):
        cap = idx["caps"].get(cap_num) or {}
        if cap.get("notas"):
            texto = cap["notas"]
            notas.append(f"\n--- Notas legales del Capítulo {cap_num} ---\n{texto[:MAX_NOTAS_CAPITULO]}"
                         f"{' (recortadas)' if len(texto) > MAX_NOTAS_CAPITULO else ''}")
        sec = idx["secs"].get(cap.get("seccion")) if cap.get("seccion") else None
        if sec and sec.get("notas") and f"Sección {sec['numero']}" not in "".join(notas):
            notas.append(f"\n--- Notas legales de la Sección {sec['numero']} ---\n{sec['notas'][:4000]}")
    material = "POSICIONES CANDIDATAS DEL ARANCEL VIGENTE:" + "\n".join(bloques)
    if notas:
        material += ("\n\nNOTAS LEGALES (del Excel AEC 2017; pueden no reflejar todos los cambios de la VII Enmienda):"
                     + "".join(notas))
    return material, validos


def _costo(usos: List[Dict[str, int]]) -> Dict[str, Any]:
    total = {k: sum(u.get(k, 0) for u in usos) for k in _PRECIO}
    busquedas = sum(u.get("busquedas_web", 0) for u in usos)
    usd = sum(total[k] * _PRECIO[k] for k in _PRECIO) / 1_000_000 + busquedas * PRECIO_BUSQUEDA_WEB
    return {**total, "busquedas_web": busquedas, "usd_estimado": round(usd, 4)}


# ======================================================================================
# Asistente de clasificación
# ======================================================================================

def clasificar_ncm(db: Session, descripcion: str, detalles: str = "", buscar_internet: bool = True) -> Dict[str, Any]:
    descripcion = (descripcion or "").strip()
    detalles = (detalles or "").strip()
    if len(descripcion) < 3:
        raise IAError("Escriba una descripción del producto (al menos unas palabras).", 400)
    idx = arancel_service.indice(db)
    if idx is None:
        raise IAError("El arancel todavía no está cargado en esta PC.", 503)
    cliente = _cliente()

    producto = f"Mercadería: {descripcion}" + (f"\nDetalles adicionales: {detalles}" if detalles else "")
    antecedentes = _antecedentes(db, f"{descripcion} {detalles}", idx)
    busqueda = arancel_service.buscar(db, q=descripcion)["resultados"][:10]
    pistas = []
    if antecedentes:
        pistas.append("Antecedentes en los despachos del usuario (productos de descripción parecida):\n" + "\n".join(
            f"- {a['ncm']}: {a['lineas']} líneas, ej.: " + "; ".join(f"'{e}'" for e in a["ejemplos"]) for a in antecedentes))
    if busqueda:
        pistas.append("Coincidencias por palabras en el arancel (pueden no ser relevantes):\n" + "\n".join(
            f"- {r['c']} {r['t']} ({r['ruta'][:120]})" for r in busqueda))

    # Paso 1: partidas (lista completa en caché)
    # Mismo esquema y mismo prefijo que "Separar lista" (un producto = lista de 1): ambas funciones
    # comparten la caché de la lista de partidas.
    sistema_1 = _sistema_partidas(idx, "Tarea: el usuario consulta UN producto (número 1). Decí brevemente qué es "
                                       "y elegí las PARTIDAS (4 dígitos) de la lista que podrían corresponderle, de "
                                       f"la más a la menos probable (entre 1 y {MAX_PARTIDAS}). Si hay dudas "
                                       "razonables entre capítulos, incluí ambas opciones.")
    paso1_lote, uso1 = _llamar(cliente, system=sistema_1, esquema=_ESQUEMA_PARTIDAS_LOTE, effort="medium",
                               mensaje="PRODUCTOS:\n1. " + producto.replace("Mercadería: ", "", 1)
                                       + ("\n\n" + "\n\n".join(pistas) if pistas else ""))
    r1 = next((r for r in paso1_lote.get("resultados", []) if r.get("n") == 1),
              (paso1_lote.get("resultados") or [{}])[0])
    paso1 = {"interpretacion": (r1.get("interpretacion") or "").strip(),
             "partidas": [{"codigo": c, "motivo": ""} for c in (r1.get("partidas") or [])]}

    partidas: List[str] = []
    motivos: Dict[str, str] = {}
    for p in paso1.get("partidas", []):
        d = digitos(p.get("codigo"))[:4]
        nodo = idx["por_d"].get(d)
        if nodo and nodo["n"] == "partida" and d not in partidas:
            partidas.append(d)
            motivos[d] = p.get("motivo", "")
        if len(partidas) >= MAX_PARTIDAS:
            break
    for a in antecedentes[:3]:  # la práctica del usuario siempre se revisa
        d = digitos(a["ncm"])[:4]
        if d not in partidas and d in idx["por_d"]:
            partidas.append(d)
            motivos.setdefault(d, "Usada antes en tus despachos para productos parecidos")
    if not partidas:
        raise IAError("La IA no encontró partidas para esa descripción. Agregue más detalles del producto.", 422)

    # Paso 2: NCM exactos dentro de esas partidas
    material, validos = _material_candidatos(db, idx, partidas)
    sistema_2 = [{"type": "text", "text": _SISTEMA_BASE + "\n\nEn este paso elegí entre 1 y "
                                                         f"{MAX_SUGERENCIAS} códigos NCM (8 dígitos, o una apertura "
                                                         "nacional si corresponde) de las posiciones candidatas. "
                                                         "Citá las notas legales cuando sostengan la elección."}]
    paso2, uso2 = _llamar(cliente, system=sistema_2,
                          mensaje=f"{producto}\n\nInterpretación previa: {paso1.get('interpretacion', '')}\n\n{material}",
                          esquema=_ESQUEMA_NCM, effort="high")

    usos_por_ncm = arancel_service.uso_en_despachos(db)["por_ncm"]
    usos = [uso1, uso2]
    sugerencias, descartadas = _armar_sugerencias(paso2, validos, idx, usos_por_ncm)

    # Paso 3 (opcional): sin una respuesta definitiva, buscar en internet qué es exactamente el producto
    # y volver a decidir entre las MISMAS posiciones candidatas del arancel.
    investigacion = None
    if buscar_internet and (not sugerencias or sugerencias[0]["confianza"] != "alta"):
        try:
            investigacion, uso_web = _investigar_en_internet(cliente, producto, paso1.get("interpretacion", ""),
                                                             sugerencias, paso2.get("informacion_faltante", []))
            usos.append(uso_web)
            paso3, uso3 = _llamar(
                cliente, system=sistema_2, esquema=_ESQUEMA_NCM, effort="high",
                mensaje=(f"{producto}\n\nInterpretación previa: {paso1.get('interpretacion', '')}\n\n"
                         f"INVESTIGACIÓN EN INTERNET SOBRE EL PRODUCTO (puede contener errores; las fuentes "
                         f"web no reemplazan al arancel):\n{investigacion['resumen']}\n\n{material}"))
            usos.append(uso3)
            nuevas, descartadas_3 = _armar_sugerencias(paso3, validos, idx, usos_por_ncm)
            if nuevas:
                sugerencias, paso2 = nuevas, paso3
                descartadas += descartadas_3
                investigacion["usada"] = True
        except IAError as e:
            investigacion = {"error": f"No se pudo buscar en internet: {e.mensaje}", "fuentes": []}
    if descartadas:
        logger.warning("[IA] Sugerencias fuera del arancel descartadas: %s", descartadas)

    consumo = _costo(usos)
    registrar_consumo("sugerir", consumo, True, idx)
    return {
        "modelo": MODELO,
        "version_arancel": idx["version"]["nombre"],
        "interpretacion": paso1.get("interpretacion", ""),
        "partidas_revisadas": [{"codigo": idx["por_d"][p]["c"], "descripcion": idx["por_d"][p]["t"],
                                "motivo": motivos.get(p, "")} for p in partidas],
        "sugerencias": sugerencias,
        "descartadas": descartadas,
        "informacion_faltante": paso2.get("informacion_faltante", []),
        "advertencia": paso2.get("advertencia", ""),
        "antecedentes": antecedentes,
        "investigacion_web": investigacion,
        "consumo": consumo,
    }


def _armar_sugerencias(paso: Dict[str, Any], validos: set, idx: Dict[str, Any],
                       usos_por_ncm: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Sugerencias de la IA validadas contra el arancel: un código fuera de las candidatas se descarta."""
    sugerencias, descartadas = [], []
    for s in paso.get("sugerencias", []):
        d = digitos(s.get("ncm"))
        d = d if d in validos else (d[:8] if d[:8] in validos else None)
        if d is None:
            descartadas.append(s.get("ncm"))
            continue
        if any(x["d"] == d for x in sugerencias):
            continue
        n = idx["por_d"][d]
        u = usos_por_ncm.get(d[:8])
        sugerencias.append({
            "d": d, "ncm": n["c"], "descripcion": n["t"], "ruta": n.get("ruta", ""),
            "anv": n.get("anv"), "anv_t": n.get("anv_t"), "aec": n.get("aec"), "iva_t": n.get("iva_t"),
            "renta_t": n.get("renta_t"), "isc_t": n.get("isc_t"), "lista": n.get("lista"),
            "confianza": s.get("confianza", "baja"), "fundamento": s.get("fundamento", ""),
            "notas_aplicadas": s.get("notas_aplicadas", ""), "por_que_no_otras": s.get("por_que_no_otras", ""),
            "uso": {"items": u["ni"], "despachos": len(u["desp"])} if u else None,
        })
        if len(sugerencias) >= MAX_SUGERENCIAS:
            break
    return sugerencias, descartadas


MAX_BUSQUEDAS_WEB = 3
PRECIO_BUSQUEDA_WEB = 0.01  # US$ por búsqueda (US$ 10 cada 1.000)


def _fuentes_web(contenido: List[Any]) -> List[Dict[str, str]]:
    """URLs consultadas: resultados de búsqueda y citas del texto (solo http/https)."""
    fuentes, vistas = [], set()

    def agregar(url, titulo):
        url = (url or "").strip()
        if url.startswith(("https://", "http://")) and url not in vistas and len(fuentes) < 8:
            vistas.add(url)
            fuentes.append({"url": url, "titulo": (titulo or url)[:150]})

    for b in contenido:
        tipo = getattr(b, "type", None)
        if tipo == "text":
            for c in getattr(b, "citations", None) or []:
                agregar(getattr(c, "url", None), getattr(c, "title", None))
        elif tipo == "web_search_tool_result":
            resultados = getattr(b, "content", None)
            if isinstance(resultados, list):  # si es un objeto, es un error de la búsqueda
                for r in resultados[:3]:
                    agregar(getattr(r, "url", None), getattr(r, "title", None))
    return fuentes


def _investigar_en_internet(cliente, producto: str, interpretacion: str, sugerencias: List[Dict[str, Any]],
                            faltantes: List[str]) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """
    Claude busca en internet (hasta 3 búsquedas) datos técnicos del producto: qué es, composición,
    función y uso. Texto libre con fuentes; la decisión final la toma otra llamada dentro del arancel.
    Solo se envía la descripción del producto (nada de clientes ni valores).
    """
    global _fallbacks_disponible
    anthropic = _sdk()
    opciones = "\n".join(f"- {s['ncm']}: {s['descripcion']} (confianza {s['confianza']})" for s in sugerencias) or "- (ninguna clara)"
    pedido = (f"{producto}\n\nInterpretación hasta ahora: {interpretacion or '—'}\n"
              f"Posiciones del arancel en duda:\n{opciones}\n"
              + (f"Datos que faltan para decidir: {'; '.join(faltantes)}\n" if faltantes else "")
              + "\nBuscá en internet información técnica de ESTE producto (marca y modelo si los hay): qué es, "
                "composición o materiales, función principal y uso. Respondé en español con un resumen breve "
                "de los datos que deciden la clasificación y qué posición parece corresponder. No inventes datos: "
                "si no encontrás información confiable, decilo.")
    kwargs = dict(
        model=MODELO, max_tokens=16000,
        system=[{"type": "text", "text": "Sos un asistente que investiga productos para clasificarlos en el arancel "
                                         "de aduanas del Paraguay (NCM 2022). Usá fuentes de fabricantes o vendedores "
                                         "reconocidos; las clasificaciones arancelarias de terceros son solo una pista."}],
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": MAX_BUSQUEDAS_WEB}],
        output_config={"effort": "medium"},
    )
    mensajes = [{"role": "user", "content": pedido}]
    contenido_total, uso = [], {k: 0 for k in _PRECIO}
    uso["busquedas_web"] = 0
    try:
        for _ in range(4):  # la búsqueda puede pausarse (pause_turn): se reanuda hasta 3 veces
            if _fallbacks_disponible:
                try:
                    resp = cliente.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                        extra_body={"fallbacks": "default"},
                                                        messages=mensajes, **kwargs)
                except anthropic.BadRequestError as e:
                    if "fallback" not in str(e).lower():
                        raise
                    _fallbacks_disponible = False
                    resp = cliente.messages.create(messages=mensajes, **kwargs)
            else:
                resp = cliente.messages.create(messages=mensajes, **kwargs)
            u = resp.usage
            uso["entrada"] += getattr(u, "input_tokens", 0) or 0
            uso["salida"] += getattr(u, "output_tokens", 0) or 0
            uso["cache_escritura"] += getattr(u, "cache_creation_input_tokens", 0) or 0
            uso["cache_lectura"] += getattr(u, "cache_read_input_tokens", 0) or 0
            uso["busquedas_web"] += getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0
            contenido_total += list(resp.content)
            if resp.stop_reason != "pause_turn":
                break
            mensajes = [{"role": "user", "content": pedido}, {"role": "assistant", "content": resp.content}]
    except Exception as e:  # noqa: BLE001
        raise _traducir_error(e) from e
    if resp.stop_reason == "refusal":
        raise IAError("La IA no quiso investigar este producto.", 422)
    resumen = "\n".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text").strip()
    if not resumen:
        raise IAError("La búsqueda en internet no devolvió información.", 502)
    return {"resumen": resumen[:6000], "fuentes": _fuentes_web(contenido_total), "usada": False,
            "busquedas": uso["busquedas_web"]}, uso


# ======================================================================================
# Clasificación de una LISTA de productos (texto, Excel/CSV o factura PDF) entre NCM elegidos
# ======================================================================================

MAX_CANDIDATOS = 10
MAX_PRODUCTOS = 300
TAMANO_LOTE = 40
NINGUNO = "NINGUNO"


def resolver_candidatos(db: Session, texto: str) -> List[Dict[str, Any]]:
    """Convierte "3304.99.10, 3304.99.90" en posiciones del arancel. Explica los códigos que no existen."""
    idx = arancel_service.indice(db)
    if idx is None:
        raise IAError("El arancel todavía no está cargado en esta PC.", 503)
    partes = [p for p in re.split(r"[,;\n\s]+", texto or "") if p.strip()]
    validos, errores = [], []
    for parte in partes:
        d = digitos(parte)
        nodo = idx["por_d"].get(d) if len(d) in (8, 11) else None
        if nodo is None or nodo["n"] not in ("ncm", "apertura"):
            sugeridos = []
            if len(d) >= 8:
                # Error de tipeo típico: mismo capítulo y mismas cuatro últimas cifras (3301.99.90 -> 3304.99.90)
                sugeridos = [n["c"] for n in idx["fichas"] if n["n"] == "ncm" and n["d"][:2] == d[:2]
                             and n["d"][4:8] == d[4:8]][:3]
            errores.append(f"{parte} no existe en el arancel vigente"
                           + (f" (¿quiso decir {' o '.join(sugeridos)}?)" if sugeridos else ""))
            continue
        if all(v["d"] != nodo["d"] for v in validos):
            validos.append(nodo)
    if errores:
        raise IAError("; ".join(errores) + ".", 400)
    if not validos:
        raise IAError("Indique al menos un NCM posible (ej.: 3304.99.10, 3304.99.90).", 400)
    if len(validos) > MAX_CANDIDATOS:
        raise IAError(f"Indique como máximo {MAX_CANDIDATOS} NCM posibles.", 400)
    return validos


def productos_desde_texto(texto: str) -> List[Dict[str, Any]]:
    productos = []
    for linea in (texto or "").splitlines():
        linea = re.sub(r"\s+", " ", linea).strip(" \t-•*·")
        if len(linea) >= 3:
            productos.append({"descripcion": linea[:400], "marca": "", "codigo": "", "cantidad": ""})
    return productos


_PALABRAS_ENCABEZADO = {
    "descripcion", "mercaderia", "mercaderias", "mercancia", "producto", "productos", "articulo", "detalle",
    "item", "items", "marca", "cantidad", "cant", "codigo", "cod", "ean", "sku", "referencia", "ref", "modelo",
    "precio", "unitario", "total", "importe", "unidad", "description", "product", "quantity", "qty", "price",
    "brand", "amount", "nro", "n", "no",
}


def _es_encabezado(fila: List[Any]) -> bool:
    """¿La fila son nombres de columna (Descripción, Marca...) y no un producto?"""
    celdas = [normalizar(str(c)).strip(" :.#°º") for c in fila if str(c).strip()]
    if not celdas:
        return False
    return any(c in _PALABRAS_ENCABEZADO or (len(c) <= 20 and c.split(" ")[0] in _PALABRAS_ENCABEZADO
                                              and not re.search(r"\d", c)) for c in celdas)


def productos_desde_planilla(contenido: bytes, nombre: str) -> List[Dict[str, Any]]:
    """Excel/CSV: busca la columna de descripción (y marca/código/cantidad si existen). No exige precios."""
    import tempfile
    from pathlib import Path as _Path
    from app.services.arancel_importer import _filas_excel
    from app.services.client_sheet_importer import ClientSheetImporter, match_columns_to_fields

    ext = _Path(nombre).suffix.lower()
    if ext == ".csv":
        filas = [[str(c if c is not None else "").strip() for c in f] for f in ClientSheetImporter._read_csv(contenido)]
    else:
        with tempfile.TemporaryDirectory() as tmp:
            ruta = _Path(tmp) / f"lista{ext}"
            ruta.write_bytes(contenido)
            filas = _filas_excel(ruta)
    filas = [f for f in filas if any(str(c).strip() for c in f)]
    if not filas:
        raise IAError("El archivo no tiene filas con datos.", 400)
    i_enc, encabezado = ClientSheetImporter._find_header_row(filas)
    if _es_encabezado(encabezado):
        mapa = match_columns_to_fields(encabezado)
        datos = filas[i_enc + 1:]
    else:  # lista sin encabezado: la primera fila ya es un producto
        mapa, datos = {}, filas
    col = mapa.get("mercaderia")
    if col is None:  # sin columna de descripción reconocible: la columna con textos más largos
        ancho = max(len(f) for f in datos)

        def largo_texto(c):
            return sum(len(str(f[c])) for f in datos if c < len(f) and not re.fullmatch(r"[\d.,\s]*", str(f[c])))
        col = max(range(ancho), key=largo_texto)

    def celda(fila, clave):
        c = mapa.get(clave)
        return str(fila[c]).strip() if c is not None and c < len(fila) else ""

    productos = []
    for f in datos:
        desc = str(f[col]).strip() if col < len(f) else ""
        if len(desc) < 3 or re.fullmatch(r"[\d.,\s$-]*", desc) or re.match(r"(?i)^(sub)?total\b", desc):
            continue
        productos.append({"descripcion": re.sub(r"\s+", " ", desc)[:400], "marca": celda(f, "marca"),
                          "codigo": celda(f, "codigo_producto"), "cantidad": celda(f, "cantidad")})
    return productos


_ESQUEMA_EXTRAER = {
    "type": "object",
    "properties": {"productos": {"type": "array", "items": {
        "type": "object",
        "properties": {"descripcion": {"type": "string"}, "marca": {"type": "string"},
                       "codigo": {"type": "string"}, "cantidad": {"type": "string"}},
        "required": ["descripcion", "marca", "codigo", "cantidad"],
        "additionalProperties": False}}},
    "required": ["productos"],
    "additionalProperties": False,
}


def productos_desde_factura_pdf(cliente, contenido: bytes) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Claude lee la factura (también escaneada) y devuelve cada línea de producto."""
    import base64
    bloques = [
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                        "data": base64.standard_b64encode(contenido).decode("ascii")}},
        {"type": "text", "text": "Extraé TODAS las líneas de producto de esta factura, en el orden en que aparecen. "
                                 "Copiá la descripción tal como figura (sin traducirla). Marca, código del producto "
                                 "y cantidad solo si figuran; si no, dejá el campo vacío. No incluyas subtotales, "
                                 "totales, fletes, seguros, impuestos ni descuentos."},
    ]
    datos, uso = _llamar(cliente, system=[{"type": "text", "text": "Sos un asistente que lee facturas comerciales con precisión."}],
                         mensaje=bloques, esquema=_ESQUEMA_EXTRAER, effort="medium", max_tokens=64000, streaming=True)
    productos = [{"descripcion": re.sub(r"\s+", " ", p.get("descripcion", "")).strip()[:400],
                  "marca": (p.get("marca") or "").strip(), "codigo": (p.get("codigo") or "").strip(),
                  "cantidad": (p.get("cantidad") or "").strip()}
                 for p in datos.get("productos", []) if len((p.get("descripcion") or "").strip()) >= 3]
    return productos, uso


def _material_lista(db: Session, idx: Dict[str, Any], candidatos: List[Dict[str, Any]]) -> str:
    """Texto fijo para todos los lotes (va en caché): cada NCM posible con su contexto, notas e historial."""
    ejemplos = _ejemplos_por_ncm(db, {c["d"][:8] for c in candidatos}, por_ncm=6)
    bloques = []
    for c in candidatos:
        d = c["d"]
        hermanos = [n for n in idx["por_sub"].get(d[:6], []) if n["d"] != d[:8]]
        texto = f"\n### {c['c']}: {c['t']}\nUbicación: {c.get('ruta', '')}\nANV {_pct(c.get('anv'))} | IVA {c.get('iva_t') or '—'}"
        if hermanos:
            texto += ("\nOtras posiciones de la misma subpartida (NO están entre las opciones): "
                      + "; ".join(f"{h['c']} {h['t']}" for h in hermanos))
        if ejemplos.get(d[:8]):
            texto += ("\nEjemplos de productos que el usuario declaró antes con este NCM: "
                      + "; ".join(f"'{e}'" for e in ejemplos[d[:8]]))
        bloques.append(texto)
    notas = []
    for cap_num in sorted({c["d"][:2] for c in candidatos}):
        cap = idx["caps"].get(cap_num) or {}
        if cap.get("notas"):
            notas.append(f"\n--- Notas legales del Capítulo {cap_num} ({cap.get('titulo', '')}) ---\n"
                         f"{cap['notas'][:MAX_NOTAS_CAPITULO]}")
    material = "NCM POSIBLES ELEGIDOS POR EL USUARIO:" + "".join(bloques)
    if notas:
        material += "\n\nNOTAS LEGALES (Excel AEC 2017):" + "".join(notas)
    return material


def _esquema_lote(codigos: List[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {"resultados": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "Número del producto en la lista"},
                "ncm": {"type": "string", "enum": codigos + [NINGUNO]},
                "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
                "motivo": {"type": "string", "description": "Motivo breve (una frase)."},
                "otro_ncm": {"type": "string", "description": "Solo si ncm es NINGUNO: el NCM que correspondería; si no, vacío."},
            },
            "required": ["n", "ncm", "confianza", "motivo", "otro_ncm"],
            "additionalProperties": False}}},
        "required": ["resultados"],
        "additionalProperties": False,
    }


_ESQUEMA_PARTIDAS_LOTE = {
    "type": "object",
    "properties": {"resultados": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "n": {"type": "integer", "description": "Número del producto en la lista"},
            "interpretacion": {"type": "string", "description": "Qué es el producto (tipo, material, uso), en pocas palabras."},
            "partidas": {"type": "array", "items": {"type": "string"},
                         "description": "Partidas de la lista (ej. 33.03), la más probable primero; cuántas, lo indica la tarea."},
        },
        "required": ["n", "interpretacion", "partidas"],
        "additionalProperties": False}}},
    "required": ["resultados"],
    "additionalProperties": False,
}

_ESQUEMA_NCM_LOTE = {
    "type": "object",
    "properties": {"resultados": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "n": {"type": "integer", "description": "Número del producto en la lista"},
            "ncm": {"type": "string", "description": "Código exacto de las posiciones candidatas, ej. 3303.00.10"},
            "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
            "motivo": {"type": "string", "description": "Motivo breve (una frase)."},
        },
        "required": ["n", "ncm", "confianza", "motivo"],
        "additionalProperties": False}}},
    "required": ["resultados"],
    "additionalProperties": False,
}
MAX_PARTIDAS_LOTE = 14
MAX_LINEAS_LOTE = 900


def _linea_producto(p: Dict[str, Any], antecedentes: Optional[List[Dict[str, Any]]] = None,
                    interpretacion: str = "") -> str:
    extra = [f"marca: {p['marca']}" if p.get("marca") else "", f"código: {p['codigo']}" if p.get("codigo") else ""]
    texto = f"{p['n']}. {p['descripcion']}" + "".join(f" | {x}" for x in extra if x)
    if interpretacion:
        texto += f"\n   Qué es (paso anterior): {interpretacion}"
    if antecedentes:
        texto += "\n   En los despachos del usuario: " + "; ".join(
            f"{a['ncm']} ({a['lineas']} líneas" + (f", ej. '{a['ejemplos'][0]}')" if a["ejemplos"] else ")")
            for a in antecedentes)
    return texto


def _clasificar_lista_automatica(db: Session, idx: Dict[str, Any], cliente, productos: List[Dict[str, Any]],
                                 origen: str, usos: List[Dict[str, int]]) -> Dict[str, Any]:
    """
    Sin NCM elegidos por el usuario: para cada producto (aunque venga solo con marca y modelo) la IA
    dice qué es, elige partidas en todo el arancel y después el NCM exacto entre sus posiciones.
    """
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor
    from app.database import SessionLocal

    # Historial de cada producto en los despachos (el mismo modelo ya declarado antes)
    antecedentes = {p["n"]: _antecedentes(db, f"{p['descripcion']} {p.get('marca', '')}", idx, limite=3) for p in productos}
    sistema_1 = _sistema_partidas(idx, "Tarea (paso 1 de 2): para cada producto de la lista decí brevemente qué "
                                       "es y elegí de 1 a 3 PARTIDAS (4 dígitos) de la lista que podrían "
                                       "corresponderle. Los productos pueden venir solo con marca y modelo: usá tu "
                                       "conocimiento de productos comerciales y los antecedentes de los despachos "
                                       "del usuario. Respondé un resultado por cada número de producto, sin omitir ninguno.")
    sistema_2 = [{"type": "text", "text": _SISTEMA_BASE + "\n\nTarea (paso 2 de 2): para cada producto elegí el código "
                                                           "NCM exacto (8 dígitos, o una apertura nacional si corresponde) "
                                                           "de las POSICIONES CANDIDATAS, aplicando las RGI y las notas "
                                                           "legales. Si un producto no se puede identificar con "
                                                           "seguridad, elegí el más probable con confianza baja y decilo "
                                                           "en el motivo. Respondé un resultado por cada número de "
                                                           "producto, sin omitir ninguno."}]

    def lote(parte: List[Dict[str, Any]]):
        consumo = []
        paso1, u1 = _llamar(cliente, system=sistema_1, esquema=_ESQUEMA_PARTIDAS_LOTE, effort="medium",
                            mensaje="PRODUCTOS:\n" + "\n".join(_linea_producto(p, antecedentes[p["n"]]) for p in parte))
        consumo.append(u1)
        interpretacion: Dict[int, str] = {}
        frecuencia: Counter = Counter()
        for r in paso1.get("resultados", []):
            if not isinstance(r.get("n"), int):
                continue
            interpretacion[r["n"]] = (r.get("interpretacion") or "").strip()
            for codigo in (r.get("partidas") or [])[:3]:
                nodo = idx["por_d"].get(digitos(codigo)[:4])
                if nodo and nodo["n"] == "partida":
                    frecuencia[nodo["d"]] += 1
        for p in parte:  # la práctica del usuario siempre se revisa
            for a in antecedentes[p["n"]]:
                frecuencia[digitos(a["ncm"])[:4]] += 1
        partidas = [d for d, _ in frecuencia.most_common(MAX_PARTIDAS_LOTE) if d in idx["por_d"]]
        if not partidas:
            return {}, consumo
        sesion = SessionLocal()  # cada lote corre en su propio hilo: sesión propia
        try:
            material, validos = _material_candidatos(sesion, idx, partidas, max_lineas=MAX_LINEAS_LOTE)
        finally:
            sesion.close()
        lineas = [_linea_producto(p, antecedentes[p["n"]], interpretacion.get(p["n"], "")) for p in parte]
        paso2, u2 = _llamar(cliente, system=sistema_2, esquema=_ESQUEMA_NCM_LOTE, effort="high",
                            mensaje="PRODUCTOS:\n" + "\n".join(lineas) + "\n\n" + material)
        consumo.append(u2)
        respuestas = {}
        for r in paso2.get("resultados", []):
            if isinstance(r.get("n"), int) and r["n"] not in respuestas:
                d = digitos(r.get("ncm"))
                d = d if d in validos else (d[:8] if d[:8] in validos else None)
                respuestas[r["n"]] = {**r, "d": d, "interpretacion": interpretacion.get(r["n"], "")}
        return respuestas, consumo

    partes = [productos[i:i + TAMANO_LOTE] for i in range(0, len(productos), TAMANO_LOTE)]
    resultados = [lote(partes[0])]  # el primero solo: deja la lista de partidas en caché
    if len(partes) > 1:
        with ThreadPoolExecutor(max_workers=3) as ex:
            resultados += list(ex.map(lote, partes[1:]))

    por_n: Dict[int, Dict[str, Any]] = {}
    for respuestas, consumo in resultados:
        usos.extend(consumo)
        por_n.update(respuestas)

    filas, conteo = [], Counter()
    for p in productos:
        r = por_n.get(p["n"])
        if not r or not r.get("d"):
            motivo = ("La IA no devolvió este producto." if not r
                      else f"La IA propuso {r.get('ncm')}, que no está entre las posiciones vigentes revisadas.")
            fila = {**p, "ncm": "SIN_RESPUESTA", "descripcion_ncm": "", "confianza": "", "motivo": motivo, "otro_ncm": ""}
        else:
            nodo = idx["por_d"][r["d"]]
            motivo = r.get("motivo", "")
            if r.get("interpretacion"):
                motivo = f"{r['interpretacion'].rstrip('.')}. {motivo}"
            fila = {**p, "ncm": nodo["c"], "descripcion_ncm": nodo["t"], "confianza": r.get("confianza", "baja"),
                    "motivo": motivo, "otro_ncm": ""}
        conteo[fila["ncm"]] += 1
        filas.append(fila)

    encontrados = [c for c, _ in conteo.most_common() if c != "SIN_RESPUESTA"]
    candidatos = []
    for c in encontrados:
        nodo = idx["por_d"][digitos(c)]
        candidatos.append({"ncm": c, "d": nodo["d"], "descripcion": nodo["t"], "anv": nodo.get("anv"),
                           "iva_t": nodo.get("iva_t")})
    resumen = {c: conteo[c] for c in encontrados}
    resumen.update({NINGUNO: 0, "SIN_RESPUESTA": conteo["SIN_RESPUESTA"]})
    consumo = _costo(usos)
    registrar_consumo("lista_automatica", consumo, True, idx)
    return {"modelo": MODELO, "modo": "automatico", "origen": origen, "candidatos": candidatos,
            "productos": filas, "resumen": resumen, "consumo": consumo}


def clasificar_lista(db: Session, candidatos_texto: str, texto: str = "", archivo: Optional[bytes] = None,
                     nombre_archivo: str = "") -> Dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path as _Path

    # Sin NCM posibles: la IA busca el NCM de cada producto en TODO el arancel (modo automático)
    modo_auto = not (candidatos_texto or "").strip()
    candidatos = [] if modo_auto else resolver_candidatos(db, candidatos_texto)
    idx = arancel_service.indice(db)
    if idx is None:
        raise IAError("El arancel todavía no está cargado en esta PC.", 503)
    cliente = _cliente()
    usos: List[Dict[str, int]] = []

    productos: List[Dict[str, Any]] = []
    origen = "texto"
    if archivo:
        ext = _Path(nombre_archivo or "").suffix.lower()
        if ext == ".pdf":
            productos, uso_pdf = productos_desde_factura_pdf(cliente, archivo)
            usos.append(uso_pdf)
            origen = "factura PDF"
        elif ext in (".xlsx", ".xls", ".csv"):
            productos = productos_desde_planilla(archivo, nombre_archivo)
            origen = "planilla"
        elif ext == ".txt":
            productos = productos_desde_texto(archivo.decode("utf-8", errors="replace"))
            origen = "archivo de texto"
        else:
            raise IAError("Formato no admitido: suba Excel (.xlsx/.xls), CSV, PDF o TXT.", 400)
    productos += productos_desde_texto(texto)
    if not productos:
        raise IAError("No se encontraron productos: escriba uno por línea o suba una lista o factura.", 400)
    if len(productos) > MAX_PRODUCTOS:
        raise IAError(f"La lista tiene {len(productos)} productos; el máximo por consulta es {MAX_PRODUCTOS}.", 400)
    for i, p in enumerate(productos, start=1):
        p["n"] = i

    if modo_auto:
        return _clasificar_lista_automatica(db, idx, cliente, productos, origen, usos)

    codigos = [c["c"] for c in candidatos]
    sistema = [
        {"type": "text", "text": _SISTEMA_BASE + "\n\nTarea: para cada producto de la lista, elegí cuál de los NCM "
                                                  "posibles le corresponde según las RGI, los textos y las notas "
                                                  f"legales. Si no corresponde a ninguno, respondé {NINGUNO} y en "
                                                  "otro_ncm el código que correspondería si lo sabés. Respondé un "
                                                  "resultado por cada número de producto, sin omitir ninguno."},
        {"type": "text", "text": _material_lista(db, idx, candidatos), "cache_control": {"type": "ephemeral"}},
    ]
    esquema = _esquema_lote(codigos)

    def lote(parte: List[Dict[str, Any]]):
        lineas = []
        for p in parte:
            extra = [f"marca: {p['marca']}" if p.get("marca") else "", f"código: {p['codigo']}" if p.get("codigo") else ""]
            lineas.append(f"{p['n']}. {p['descripcion']}" + "".join(f" | {x}" for x in extra if x))
        return _llamar(cliente, system=sistema, mensaje="PRODUCTOS:\n" + "\n".join(lineas), esquema=esquema,
                       effort="medium")

    partes = [productos[i:i + TAMANO_LOTE] for i in range(0, len(productos), TAMANO_LOTE)]
    resultados = [lote(partes[0])]  # el primero solo: deja el material en caché para los demás
    if len(partes) > 1:
        with ThreadPoolExecutor(max_workers=3) as ex:
            resultados += list(ex.map(lote, partes[1:]))

    por_n: Dict[int, Dict[str, Any]] = {}
    for datos, uso in resultados:
        usos.append(uso)
        for r in datos.get("resultados", []):
            if isinstance(r.get("n"), int) and r.get("ncm") in codigos + [NINGUNO]:
                por_n.setdefault(r["n"], r)

    por_codigo = {c["c"]: c for c in candidatos}
    filas = []
    resumen = {c: 0 for c in codigos}
    resumen.update({NINGUNO: 0, "SIN_RESPUESTA": 0})
    for p in productos:
        r = por_n.get(p["n"])
        if r is None:
            fila = {**p, "ncm": "SIN_RESPUESTA", "descripcion_ncm": "", "confianza": "",
                    "motivo": "La IA no devolvió este producto.", "otro_ncm": ""}
        else:
            otro = ""
            if r["ncm"] == NINGUNO and r.get("otro_ncm"):
                nodo = idx["por_d"].get(digitos(r["otro_ncm"]))
                otro = f"{nodo['c']} {nodo['t']}" if nodo and nodo["n"] in ("ncm", "apertura") else ""
            fila = {**p, "ncm": r["ncm"], "descripcion_ncm": por_codigo[r["ncm"]]["t"] if r["ncm"] in por_codigo else "",
                    "confianza": r["confianza"], "motivo": r["motivo"], "otro_ncm": otro}
        resumen[fila["ncm"]] += 1
        filas.append(fila)

    consumo = _costo(usos)
    registrar_consumo("lista_elegidos", consumo, False, idx)
    return {
        "modelo": MODELO,
        "modo": "elegidos",
        "origen": origen,
        "candidatos": [{"ncm": c["c"], "d": c["d"], "descripcion": c["t"], "anv": c.get("anv"), "iva_t": c.get("iva_t")}
                       for c in candidatos],
        "productos": filas,
        "resumen": resumen,
        "consumo": consumo,
    }


# ======================================================================================
# Exportar la clasificación a Excel: una hoja por NCM
# ======================================================================================

def _texto_seguro(celda, valor) -> None:
    """Escribe el valor como TEXTO: un texto que empieza con = + - @ no se ejecuta como fórmula en Excel."""
    celda.value = valor
    if isinstance(valor, str):
        celda.data_type = "s"


def exportar_lista_xlsx(resultado: Dict[str, Any]) -> bytes:
    import io
    from datetime import datetime as _dt
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    candidatos = resultado.get("candidatos") or []
    productos = resultado.get("productos") or []
    if not productos:
        raise IAError("No hay productos para exportar.", 400)
    # En modo automático puede haber más NCM encontrados que los 10 elegibles a mano
    if len(productos) > MAX_PRODUCTOS or len(candidatos) > 100:
        raise IAError("El resultado es demasiado grande para exportar.", 400)

    negrita, blanca = Font(bold=True), Font(bold=True, color="FFFFFF")
    relleno = PatternFill("solid", fgColor="1E40AF")
    envolver = Alignment(wrap_text=True, vertical="top")

    def hoja(wb, titulo, encabezados, filas, anchos):
        ws = wb.create_sheet(titulo[:31])
        for c, enc in enumerate(encabezados, start=1):
            celda = ws.cell(row=1, column=c)
            _texto_seguro(celda, enc)
            celda.font, celda.fill = blanca, relleno
        for r, fila in enumerate(filas, start=2):
            for c, v in enumerate(fila, start=1):
                celda = ws.cell(row=r, column=c)
                _texto_seguro(celda, v)
                celda.alignment = envolver
        for c, ancho in enumerate(anchos, start=1):
            ws.column_dimensions[get_column_letter(c)].width = ancho
        ws.freeze_panes = "A2"
        if filas:
            ws.auto_filter.ref = f"A1:{get_column_letter(len(encabezados))}{len(filas) + 1}"
        return ws

    def fila_producto(p, con_ncm=False, con_otro=False):
        fila = [p.get("n"), p.get("descripcion", ""), p.get("marca", ""), p.get("codigo", ""), p.get("cantidad", "")]
        if con_ncm:
            fila += [p.get("ncm", ""), p.get("descripcion_ncm", "")]
        fila += [p.get("confianza", ""), p.get("motivo", "")]
        if con_otro:
            fila.append(p.get("otro_ncm", ""))
        return fila

    base = ["N°", "Producto", "Marca", "Código", "Cantidad"]
    anchos_base = [6, 48, 16, 16, 10]

    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen"
    consumo = resultado.get("consumo") or {}
    lineas = [
        ["Clasificación de productos por NCM (asistente de IA)"],
        [f"Generado: {_dt.now():%d/%m/%Y %H:%M} · Origen: {resultado.get('origen', '')} · Modelo: {resultado.get('modelo', '')}"],
        ["La IA sugiere: la clasificación final es responsabilidad del despachante."],
        [],
        ["NCM", "Descripción oficial", "ANV %", "IVA", "Productos"],
    ]
    for c in candidatos:
        lineas.append([c.get("ncm", ""), c.get("descripcion", ""), c.get("anv"), c.get("iva_t") or "",
                       sum(1 for p in productos if p.get("ncm") == c.get("ncm"))])
    for clave, etiqueta in ((NINGUNO, "Ninguno de los NCM elegidos"), ("SIN_RESPUESTA", "Sin respuesta de la IA")):
        n = sum(1 for p in productos if p.get("ncm") == clave)
        if n:
            lineas.append([etiqueta, "", None, "", n])
    lineas += [["Total", "", None, "", len(productos)], [],
               [f"Costo estimado de la consulta: US$ {consumo.get('usd_estimado', 0)}"]]
    for r, fila in enumerate(lineas, start=1):
        for c, v in enumerate(fila, start=1):
            _texto_seguro(ws.cell(row=r, column=c), v)
    ws["A1"].font = Font(bold=True, size=13)
    for c in range(1, 6):
        ws.cell(row=5, column=c).font = negrita
    for c, ancho in enumerate([28, 60, 8, 8, 11], start=1):
        ws.column_dimensions[get_column_letter(c)].width = ancho

    hoja(wb, "Todos", base + ["NCM", "Descripción NCM", "Confianza", "Motivo", "NCM que correspondería"],
         [fila_producto(p, con_ncm=True, con_otro=True) for p in productos], anchos_base + [13, 34, 11, 50, 34])
    usados = set()
    for c in candidatos:
        nombre = re.sub(r"[\\/*?:\[\]]", "", c.get("ncm", ""))[:31] or "NCM"
        if nombre in usados:
            continue
        usados.add(nombre)
        ws_ncm = hoja(wb, nombre, base + ["Confianza", "Motivo"],
                      [fila_producto(p) for p in productos if p.get("ncm") == c.get("ncm")], anchos_base + [11, 60])
        ws_ncm.sheet_properties.tabColor = "1E40AF"
    ningunos = [p for p in productos if p.get("ncm") == NINGUNO]
    if ningunos:
        hoja(wb, "Ninguno", base + ["Confianza", "Motivo", "NCM que correspondería"],
             [fila_producto(p, con_otro=True) for p in ningunos], anchos_base + [11, 50, 34]).sheet_properties.tabColor = "DC2626"
    sin = [p for p in productos if p.get("ncm") == "SIN_RESPUESTA"]
    if sin:
        hoja(wb, "Sin respuesta", base + ["Confianza", "Motivo"], [fila_producto(p) for p in sin], anchos_base + [11, 50])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ======================================================================================
# Costo estimado ANTES de consultar (sin usar la IA) y consumo registrado en esta PC
# ======================================================================================

CARACTERES_POR_TOKEN = 2.3   # medido con el arancel real: 153.906 caracteres = 67.330 tokens
VIGENCIA_CACHE = timedelta(minutes=55)  # la caché dura 1 h desde su último uso (margen de 5 min)
_lock_consumo = threading.Lock()


def _ruta_consumo() -> Path:
    return Path(settings.DATA_DIR) / "ia_consumo.jsonl"


def registrar_consumo(tipo: str, consumo: Dict[str, Any], usa_cache_partidas: bool, idx: Dict[str, Any]) -> None:
    """Anota cada consulta (costo estimado y si usó la caché de partidas). Nunca interrumpe la consulta."""
    linea = {"fecha": datetime.now().isoformat(timespec="seconds"), "tipo": tipo,
             "usd": consumo.get("usd_estimado", 0), "cache_partidas": usa_cache_partidas,
             "version": idx["version"].get("nombre")}
    try:
        with _lock_consumo:
            ruta = _ruta_consumo()
            ruta.parent.mkdir(parents=True, exist_ok=True)
            with open(ruta, "a", encoding="utf-8") as f:
                f.write(json.dumps(linea, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("[IA] No se pudo registrar el consumo: %s", e)


def _leer_consumo() -> List[Dict[str, Any]]:
    ruta = _ruta_consumo()
    if not ruta.exists():
        return []
    filas = []
    with _lock_consumo:
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            try:
                filas.append(json.loads(linea))
            except ValueError:
                continue
    return filas


def resumen_consumo() -> Dict[str, Any]:
    ahora = datetime.now()
    hoy, mes = ahora.date().isoformat(), ahora.strftime("%Y-%m")
    filas = _leer_consumo()
    de_hoy = [f for f in filas if f.get("fecha", "").startswith(hoy)]
    return {"hoy_usd": round(sum(f.get("usd", 0) for f in de_hoy), 3), "consultas_hoy": len(de_hoy),
            "mes_usd": round(sum(f.get("usd", 0) for f in filas if f.get("fecha", "").startswith(mes)), 3)}


def _cache_partidas_activa(idx: Dict[str, Any]) -> bool:
    """¿Hubo en esta PC una consulta con la lista de partidas (misma versión del arancel) en la última hora?"""
    limite = datetime.now() - VIGENCIA_CACHE
    for f in reversed(_leer_consumo()):
        if f.get("cache_partidas") and f.get("version") == idx["version"].get("nombre"):
            try:
                return datetime.fromisoformat(f["fecha"]) >= limite
            except (KeyError, ValueError):
                return False
    return False


def _usd(entrada=0, salida=0, cache_lectura=0, cache_escritura_1h=0, cache_escritura=0, busquedas=0) -> float:
    return ((entrada * _PRECIO["entrada"] + salida * _PRECIO["salida"] + cache_lectura * _PRECIO["cache_lectura"]
             + cache_escritura_1h * _PRECIO["cache_escritura_1h"] + cache_escritura * _PRECIO["cache_escritura"]) / 1_000_000
            + busquedas * PRECIO_BUSQUEDA_WEB)


def _tokens(texto: str) -> int:
    return int(len(texto) / CARACTERES_POR_TOKEN) + 1


def _costo_cache_partidas(idx: Dict[str, Any], lotes: int = 1) -> Tuple[float, bool, int]:
    tokens = _tokens(_SISTEMA_BASE + _texto_partidas(idx))
    activa = _cache_partidas_activa(idx)
    primero = _usd(cache_lectura=tokens) if activa else _usd(cache_escritura_1h=tokens)
    return primero + (lotes - 1) * _usd(cache_lectura=tokens), activa, tokens


def _num(v: float, decimales: int = 2) -> str:
    """Número con el formato de Paraguay: 66.916 tokens, US$ 0,54."""
    return f"{v:,.{decimales}f}".translate(str.maketrans(",.", ".,"))


def _texto_cache(activa: bool, costo: float, tokens: int) -> str:
    if activa:
        return (f"Lista de partidas del arancel: ya está en caché por una consulta de la última hora en esta PC "
                f"(US$ {_num(costo, 3)}).")
    return (f"Primera consulta de la hora: se guarda en caché la lista de partidas del arancel "
            f"({_num(tokens, 0)} tokens, US$ {_num(costo)}). Durante la hora siguiente, las consultas cuestan mucho menos.")


def estimar_sugerir(db: Session, buscar_internet: bool = True) -> Dict[str, Any]:
    """Rango de costo de "Sugerir NCM" calibrado con consultas reales (sin llamar a la IA)."""
    idx = arancel_service.indice(db)
    if idx is None:
        raise IAError("El arancel todavía no está cargado en esta PC.", 503)
    cache, activa, tokens = _costo_cache_partidas(idx)
    analisis = (_usd(entrada=2000, salida=500) + _usd(entrada=6000, salida=1000),
                _usd(entrada=3500, salida=1000) + _usd(entrada=16000, salida=2200))
    web = ((_usd(entrada=35000, salida=2500, busquedas=1) + _usd(entrada=8000, salida=1000),
            _usd(entrada=65000, salida=5000, busquedas=MAX_BUSQUEDAS_WEB) + _usd(entrada=16000, salida=2000))
           if buscar_internet else (0.0, 0.0))
    detalle = [_texto_cache(activa, cache, tokens),
               f"Análisis del producto (2 pasos): US$ {_num(analisis[0])} a {_num(analisis[1])}."]
    if buscar_internet:
        detalle.append(f"Solo si la IA no está segura y busca en internet (hasta {MAX_BUSQUEDAS_WEB} búsquedas): "
                       f"US$ {_num(web[0])} a {_num(web[1])} más.")
    return {"usd_min": round(cache + analisis[0], 3), "usd_max": round(cache + analisis[1] + web[1], 3),
            "cache_activa": activa, "detalle": detalle, "consumo": resumen_consumo()}


def _rango_lote_auto(n: int) -> Tuple[float, float]:
    """Costo sin la caché de partidas de un lote de n productos en modo automático (2 pasos)."""
    minimo = _usd(entrada=1000 + 60 * n, salida=150 + 50 * n) + _usd(entrada=2500 + 100 * n, salida=200 + 70 * n)
    maximo = _usd(entrada=1500 + 80 * n, salida=600 + 120 * n) + _usd(entrada=25000 + 150 * n, salida=800 + 150 * n)
    return minimo, maximo


def estimar_lista(db: Session, candidatos_texto: str, texto: str = "", archivo: Optional[bytes] = None,
                  nombre_archivo: str = "") -> Dict[str, Any]:
    """
    Rango de costo de "Separar lista" sin llamar a la IA: cuenta los productos localmente (texto, Excel,
    CSV) o las páginas de la factura PDF, y valida los NCM elegidos antes de cobrar nada.
    """
    modo_auto = not (candidatos_texto or "").strip()
    candidatos = [] if modo_auto else resolver_candidatos(db, candidatos_texto)
    idx = arancel_service.indice(db)
    if idx is None:
        raise IAError("El arancel todavía no está cargado en esta PC.", 503)

    productos = productos_desde_texto(texto)
    paginas_pdf, costo_pdf = 0, (0.0, 0.0)
    if archivo:
        ext = Path(nombre_archivo or "").suffix.lower()
        if ext == ".pdf":
            import pymupdf
            try:
                with pymupdf.open(stream=archivo, filetype="pdf") as doc:
                    paginas_pdf = len(doc)
            except Exception as e:  # noqa: BLE001
                raise IAError(f"No se pudo abrir el PDF: {type(e).__name__}.", 400) from e
            # Lectura de la factura por la IA: ~1.600 a 3.000 tokens por página y 40 a 60 por producto
            costo_pdf = (_usd(entrada=300 + 1600 * paginas_pdf, salida=300 + 8 * 40 * paginas_pdf),
                         _usd(entrada=300 + 3000 * paginas_pdf, salida=600 + 35 * 60 * paginas_pdf))
        elif ext in (".xlsx", ".xls", ".csv"):
            productos = productos_desde_planilla(archivo, nombre_archivo) + productos
        elif ext == ".txt":
            productos = productos_desde_texto(archivo.decode("utf-8", errors="replace")) + productos
        else:
            raise IAError("Formato no admitido: suba Excel (.xlsx/.xls), CSV, PDF o TXT.", 400)

    if paginas_pdf:  # la cantidad real de productos se sabe recién al leer la factura
        n_min, n_max = len(productos) + 8 * paginas_pdf, len(productos) + 35 * paginas_pdf
    else:
        n_min = n_max = len(productos)
        if not productos:
            raise IAError("No se encontraron productos: escriba uno por línea o suba una lista o factura.", 400)
    if len(productos) > MAX_PRODUCTOS:  # solo los contados de verdad (lo de la factura es una estimación)
        raise IAError(f"La lista tiene {len(productos)} productos; el máximo por consulta es {MAX_PRODUCTOS}.", 400)
    n_min, n_max = min(n_min, MAX_PRODUCTOS), min(n_max, MAX_PRODUCTOS)

    def costo_productos(n: int, extremo: int) -> float:
        lotes = max(1, math.ceil(n / TAMANO_LOTE))
        if modo_auto:
            total = sum(_rango_lote_auto(min(TAMANO_LOTE, n - i * TAMANO_LOTE))[extremo] for i in range(lotes))
            return total + _costo_cache_partidas(idx, lotes)[0]
        material = _tokens(_SISTEMA_BASE + _material_lista(db, idx, candidatos))
        cache = _usd(cache_escritura=material) + (lotes - 1) * _usd(cache_lectura=material)
        por_lote = [(_usd(entrada=500 + 40 * m, salida=150 + 45 * m), _usd(entrada=800 + 60 * m, salida=400 + 90 * m))
                    for m in (min(TAMANO_LOTE, n - i * TAMANO_LOTE) for i in range(lotes))]
        return cache + sum(p[extremo] for p in por_lote)

    usd_min = costo_pdf[0] + costo_productos(n_min, 0)
    usd_max = costo_pdf[1] + costo_productos(n_max, 1)
    detalle = []
    if paginas_pdf:
        detalle.append(f"Factura PDF de {paginas_pdf} página(s): la IA la lee completa (US$ {_num(costo_pdf[0])} a "
                       f"{_num(costo_pdf[1])}); se estiman {n_min} a {n_max} productos.")
    else:
        lotes = max(1, math.ceil(n_min / TAMANO_LOTE))
        detalle.append(f"{n_min} producto(s), {lotes} lote(s) de hasta {TAMANO_LOTE}.")
    if modo_auto:
        cache, activa, tokens = _costo_cache_partidas(idx)
        detalle.insert(0, "Búsqueda automática del NCM de cada producto en todo el arancel (2 pasos por lote).")
        detalle.append(_texto_cache(activa, cache, tokens))
    else:
        activa = False
        detalle.insert(0, f"Clasificación entre {len(candidatos)} NCM elegido(s) (1 paso por lote).")
    return {"usd_min": round(usd_min, 3), "usd_max": round(usd_max, 3), "cache_activa": activa,
            "productos": [n_min, n_max], "detalle": detalle, "consumo": resumen_consumo()}
