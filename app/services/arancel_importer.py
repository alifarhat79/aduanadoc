"""
Importador del Arancel de Aduanas.

Fuentes:
- PDF oficial "Arancel Nacional Vigente" del Ministerio de Economía (MEF), NCM 2022: códigos,
  descripciones, AEC, ANV, listas (BK, BIT, LNE, DEC. 08/22...) y decretos. Es la fuente PRINCIPAL.
- Excel "AEC 2017" del usuario: aporta lo que el PDF no trae (IVA, Renta, ISC, OMC, autopartes,
  observaciones), los títulos y notas legales de secciones y capítulos, las leyendas de símbolos
  y las aperturas nacionales (más de 8 dígitos).

Nada se inventa: cada tasa se guarda con su texto original y, si un dato no existe en la fuente,
queda vacío.
"""
import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

FORMATO_DATASET = 1
FUENTE_EXCEL = "Excel AEC 2017"


def _num(txt: Optional[str]) -> Optional[float]:
    """Número al inicio de una tasa ("14", "1,5*", "14,4 DEC. 08/22") o None si es texto (Exenta...)."""
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)", txt or "")
    return float(m.group(1).replace(",", ".")) if m else None


def _limpiar_descripcion(texto: str):
    """Quita los guiones de sangría ("- - Los demás") y devuelve (texto, cantidad_de_guiones)."""
    texto = texto or ""
    guiones = len(re.match(r"^\s*((?:-\s*)*)", texto).group(1).replace(" ", ""))
    limpio = re.sub(r"^\s*(?:-\s*)*", "", texto).strip()
    return re.sub(r"\s{2,}", " ", limpio), guiones


# ======================================================================================
# 1. Excel AEC (formato del archivo "AEC 2017 (13-04-2020)")
# ======================================================================================

_ROMANO = re.compile(r"(?i)^secci[oó]n\s+([IVXL]+)\s*$")
_CAPITULO = re.compile(r"(?i)^cap[ií]tulo\s+(\d+)\s*$")
_NIVELES_EXCEL = [
    (re.compile(r"^\d{2}\.\d{2}$"), "partida"),
    (re.compile(r"^\d{4}\.\d{2}$"), "subpartida"),
    (re.compile(r"^\d{4}\.\d{2}\.\d$"), "agrupacion"),
    (re.compile(r"^\d{4}\.\d{2}\.\d{2}$"), "ncm"),
    (re.compile(r"^\d{4}\.\d{2}\.\d{2}\.\d{1,3}$"), "apertura"),
    (re.compile(r"^\d{4}\.\d{2}\.\d{3}$"), "ncm"),  # 4112.00.000 (errata del archivo)
]


def _celda_texto(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return "%g" % v  # 14.0 -> "14", 1.5 -> "1.5"
    if isinstance(v, int) and not isinstance(v, bool):
        return str(v)
    return str(v).strip()


def _filas_excel(ruta: Path) -> List[List[str]]:
    """Lee la primera hoja de un .xls (xlrd) o .xlsx (openpyxl) como filas de texto."""
    if ruta.suffix.lower() == ".xls":
        try:
            import xlrd
        except ImportError as e:  # pragma: no cover - depende de la instalación
            raise RuntimeError("Para leer archivos .xls hace falta el paquete 'xlrd' (pip install xlrd).") from e
        hoja = xlrd.open_workbook(str(ruta)).sheet_by_index(0)
        return [[_celda_texto(hoja.cell_value(r, c)) for c in range(hoja.ncols)] for r in range(hoja.nrows)]
    import openpyxl
    libro = openpyxl.load_workbook(str(ruta), read_only=True, data_only=True)
    try:
        hoja = libro.worksheets[0]
        return [[_celda_texto(v) for v in fila] for fila in hoja.iter_rows(values_only=True)]
    finally:
        libro.close()


def _unir_notas(lineas: List[str]) -> str:
    """Une renglones cortados del Excel en párrafos (nuevo párrafo en '1.', 'a)', '-' ...)."""
    pars: List[str] = []
    for linea in lineas:
        if not pars or re.match(r"^(\d+\.|[a-zñ]\)|\d+\)|[-•]|Notas? de subpartida)", linea):
            pars.append(linea)
        else:
            pars[-1] = (pars[-1][:-1] + linea) if pars[-1].endswith("-") else (pars[-1] + " " + linea)
    return "\n".join(pars)


def parsear_excel_aec(ruta) -> Dict[str, Any]:
    """
    Convierte el Excel del AEC en: secciones, capítulos (con notas legales), nodos y leyendas.
    Columnas esperadas: NCM, DESCRIPCIÓN, AEC, ANV, OMC, LISTA, AUTOPARTES (intra/extra), IVA,
    RENTA, ISC y hasta 5 columnas de decretos/observaciones.
    """
    filas = _filas_excel(Path(ruta))

    def celda(fila: List[str], c: int) -> str:
        return fila[c] if c < len(fila) else ""

    secciones: List[Dict[str, Any]] = []
    capitulos: List[Dict[str, Any]] = []
    nodos: List[Dict[str, Any]] = []
    leyendas: Dict[str, str] = {}
    estado: Dict[str, Any] = {"sec": None, "cap": None, "modo": None, "titulo": [], "notas": []}

    def cerrar_buffers():
        cap, sec = estado["cap"], estado["sec"]
        destino = cap if cap is not None and capitulos and capitulos[-1] is cap else sec
        if estado["titulo"] and destino is not None and not destino.get("titulo"):
            destino["titulo"] = " ".join(estado["titulo"]).strip()
        if estado["notas"] and destino is not None:
            previo = destino.get("notas") or ""
            destino["notas"] = previo + ("\n" if previo else "") + _unir_notas(estado["notas"])
        estado["titulo"], estado["notas"] = [], []

    ultimo_era_codigo = False
    for fila in filas:
        a, b = celda(fila, 0), celda(fila, 1)
        if not a and not b:
            ultimo_era_codigo = False
            continue
        if a == "NCM":
            continue
        if a.startswith("(") and not b:  # leyendas de símbolos: (*) (a) (@) (AZ*)
            m = re.match(r"^\(([^)]+)\)\s*(.*)$", a)
            if m:
                leyendas[m.group(1)] = m.group(2).strip()
            continue
        if not a:
            if _ROMANO.match(b):
                cerrar_buffers()
                estado["sec"] = {"numero": _ROMANO.match(b).group(1).upper(), "titulo": "", "notas": ""}
                secciones.append(estado["sec"])
                estado["cap"], estado["modo"] = None, "titulo"
                continue
            if _CAPITULO.match(b):
                cerrar_buffers()
                n = int(_CAPITULO.match(b).group(1))
                if capitulos and capitulos[-1]["numero"] == f"{n:02d}":
                    continue  # marcador duplicado
                sec = estado["sec"]
                estado["cap"] = {"numero": f"{n:02d}", "seccion": sec["numero"] if sec else None, "titulo": "", "notas": ""}
                capitulos.append(estado["cap"])
                estado["modo"] = "titulo"
                continue
            if re.match(r"(?i)^cap\d+$", b):
                continue
            if re.match(r"(?i)^notas?\.?$", b) or re.match(r"(?i)^notas? de subpartida", b):
                if estado["titulo"]:
                    cerrar_buffers()
                estado["modo"] = "notas"
                if re.match(r"(?i)^notas? de subpartida", b):
                    estado["notas"].append(b)
                continue
            if estado["modo"] == "titulo":
                estado["titulo"].append(b)
            elif estado["modo"] == "notas":
                estado["notas"].append(b)
            elif nodos and ultimo_era_codigo:
                # renglón de continuación de la descripción del código anterior
                nodos[-1]["t"] = (nodos[-1]["t"] + " " + re.sub(r"\s{2,}", " ", b)).strip()
                continue
            ultimo_era_codigo = False
            continue

        # Fila con código
        codigo = a.rstrip(".").strip()
        nivel = next((lv for rx, lv in _NIVELES_EXCEL if rx.match(codigo)), None)
        if nivel is None:
            continue
        if estado["titulo"] or estado["notas"]:
            cerrar_buffers()
        estado["modo"] = None
        digitos = re.sub(r"\D", "", codigo)
        desc, guiones = _limpiar_descripcion(b)
        sec = estado["sec"]
        nodo: Dict[str, Any] = {"c": codigo, "d": digitos, "n": nivel, "t": desc, "g": guiones,
                                "cap": digitos[:2], "sec": sec["numero"] if sec else None}
        if nivel in ("ncm", "apertura"):
            crudo = {k: celda(fila, c) for k, c in (("aec", 2), ("anv", 3), ("omc", 4), ("lista", 5), ("ai", 6),
                                                    ("ae", 7), ("iva", 8), ("renta", 9), ("isc", 10))}
            extra = [celda(fila, c) for c in range(11, 16) if celda(fila, c)]
            nodo.update({
                "aec": _num(crudo["aec"]), "aec_t": crudo["aec"] or None,
                "anv": _num(crudo["anv"]), "anv_t": crudo["anv"] or None,
                "omc": crudo["omc"] or None,
                "lista": re.sub(r"(?i)^dec\.?\s*37/07$", "DEC.37/07", crudo["lista"]) or None,
                "ai": _num(crudo["ai"]), "ae": _num(crudo["ae"]),
                "iva_t": crudo["iva"] or None, "iva": _num(crudo["iva"]),
                "renta_t": crudo["renta"] or None, "renta": _num(crudo["renta"]),
                "isc_t": crudo["isc"] or None, "isc": _num(crudo["isc"]),
                "obs": [x for x in extra if x.lower().startswith("dto")] + [x for x in extra if not x.lower().startswith("dto")],
            })
        nodos.append(nodo)
        ultimo_era_codigo = True
    cerrar_buffers()

    # Marcadores de capítulo erróneos (ej. "Capítulo 87" dentro del 84): se unen por número
    por_numero: Dict[str, Dict[str, Any]] = {}
    for c in capitulos:
        previo = por_numero.get(c["numero"])
        if previo is None or (not previo["titulo"] and c["titulo"]):
            if previo is not None and previo.get("notas") and not c.get("notas"):
                c["notas"] = previo["notas"]
            por_numero[c["numero"]] = c
    capitulos = sorted(por_numero.values(), key=lambda c: c["numero"])

    return {"secciones": secciones, "capitulos": capitulos, "nodos": nodos, "leyendas": leyendas}


# ======================================================================================
# 2. PDF oficial del MEF ("Arancel Nacional Vigente")
# ======================================================================================

_CODIGO_MEF = re.compile(r"^\d{2}\.\d{2}$|^\d{4}\.\d{2}(\.\d{1,2})?$|^\d{4}\.\d{2}\.\d{2}\.\d{1,3}$")
_X_DESCRIPCION = 110  # A la izquierda: columna NCM (los códigos empiezan antes de x=100; algunas páginas tienen la descripción en x=124)


def _encabezado_tabla(palabras):
    """
    Busca el renglón de encabezado de la tabla (NCM ... AEC ANV LISTA) en cualquier altura de la
    página: en las páginas donde empieza un capítulo, el encabezado está DEBAJO del título y de las
    notas legales. Devuelve (posiciones x, y donde empiezan las filas) o None.
    """
    for anv in sorted((w for w in palabras if w[4] == "ANV"), key=lambda w: w[1]):
        cerca = {w[4]: w for w in palabras
                 if w[4] in ("NCM", "DESCRIPCIÓN", "AEC", "LISTA") and abs(w[1] - anv[1]) <= 10}
        if all(k in cerca for k in ("NCM", "AEC", "LISTA")) and cerca["AEC"][0] < anv[0] < cerca["LISTA"][0]:
            fila = list(cerca.values()) + [anv]
            pos = {"aec": cerca["AEC"][0] - 8, "anv": anv[0] - 8, "lista": cerca["LISTA"][0] - 8}
            return pos, max(w[3] for w in fila) - 1
    return None


def parsear_pdf_mef(ruta) -> List[Dict[str, Any]]:
    """
    Lee el PDF por posición de columnas (NCM | DESCRIPCIÓN | AEC | ANV | LISTA), página por página.
    Devuelve una lista de nodos {c, d, t, aec_t, anv_t, lista, pag, aec, anv, anv_dec}.
    """
    import pymupdf

    nodos: List[Dict[str, Any]] = []
    columnas = None
    with pymupdf.open(str(ruta)) as doc:
        for pno in range(len(doc)):
            palabras = doc[pno].get_text("words")  # x0, y0, x1, y1, texto, bloque, línea, nº
            encabezado = _encabezado_tabla(palabras)
            if encabezado is not None:
                # Las filas empiezan debajo del encabezado de la tabla de ESTA página
                columnas, y_min = encabezado
            elif columnas is None or not any(_CODIGO_MEF.match(w[4]) for w in palabras):
                continue
            else:
                y_min = 94  # página de tabla sin encabezado: se usan las columnas de la anterior

            # Renglones por cercanía vertical (no por redondeo): el código y su descripción pueden
            # diferir 1 punto de altura y el redondeo los separaba en dos renglones distintos.
            renglones: Dict[float, List] = {}
            y_actual = None
            for x0, y0, x1, y1, t, *_ in sorted((w for w in palabras if w[1] >= y_min), key=lambda w: (w[1], w[0])):
                if y_actual is None or y0 - y_actual > 2.5:
                    y_actual = y0
                renglones.setdefault(y_actual, []).append((x0, x1, t))

            y_previo = None
            for clave in sorted(renglones):
                ws = sorted(renglones[clave])
                y = clave
                codigo = ws[0][2] if ws[0][0] < _X_DESCRIPCION and _CODIGO_MEF.match(ws[0][2]) else None
                desc = " ".join(t for x0, x1, t in ws if _X_DESCRIPCION <= x0 < columnas["aec"] - 6 and not (codigo and t == codigo))
                aec = " ".join(t for x0, x1, t in ws if columnas["aec"] - 6 <= x0 < columnas["anv"] - 2)
                anv = " ".join(t for x0, x1, t in ws if columnas["anv"] - 2 <= x0 < columnas["lista"] - 2)
                lista = " ".join(t for x0, x1, t in ws if x0 >= columnas["lista"] - 2)
                # La referencia "DEC. 08/22" / "DEC.37/07" es de la columna LISTA, pero empieza justo en
                # su borde y en algunas páginas cae dentro de ANV ("14,4 DEC." + "08/22"): se normaliza
                # siempre a LISTA y ANV queda solo con la tasa.
                if anv.endswith("DEC.") and re.match(r"^\d+/\d+", lista):
                    partes = lista.split()
                    anv, lista = anv[:-4].strip(), " ".join(["DEC. " + partes[0]] + partes[1:])
                m_dec = re.match(r"^(.*?)\s*(DEC\.\s?\d+/\d+)$", anv)
                if m_dec and not lista:
                    anv, lista = m_dec.group(1).strip(), m_dec.group(2)
                # Descripciones muy largas: una palabra suelta ("y", "a") puede invadir la columna AEC
                if aec and not re.match(r"^\d", aec):
                    desc, aec = (desc + " " + aec).strip(), ""
                if codigo:
                    nodos.append({"c": codigo, "d": re.sub(r"\D", "", codigo), "t": desc, "aec_t": aec,
                                  "anv_t": anv, "lista": lista, "pag": pno + 1})
                elif nodos and nodos[-1]["pag"] == pno + 1 and y_previo is not None and y - y_previo <= 13:
                    if desc and not desc.startswith("-"):
                        nodos[-1]["t"] = (nodos[-1]["t"] + " " + desc).strip()  # renglón de continuación
                    if (aec or anv) and not (nodos[-1]["aec_t"] or nodos[-1]["anv_t"]):
                        nodos[-1]["aec_t"], nodos[-1]["anv_t"] = aec, anv
                        nodos[-1]["lista"] = nodos[-1]["lista"] or lista
                y_previo = y if (codigo or (desc and not desc.startswith("-"))) else None

    for n in nodos:
        n["aec"], n["anv"] = _num(n["aec_t"]), _num(n["anv_t"])
        m = re.search(r"DEC\.?\s*([\d./]+)", n["anv_t"] or "")
        n["anv_dec"] = ("DEC. " + m.group(1)) if m else None
    return nodos


# ======================================================================================
# 3. Unión de fuentes -> dataset listo para cargar en la base
# ======================================================================================

_NIVEL_POR_LARGO = {4: "partida", 6: "subpartida", 7: "agrupacion", 8: "ncm"}
_CAMPOS_IMPUESTOS = ("iva", "iva_t", "renta", "renta_t", "isc", "isc_t", "omc", "ai", "ae", "obs")


def _impuestos(nodo: Dict[str, Any], fuente: str) -> Dict[str, Any]:
    datos = {k: nodo.get(k) for k in _CAMPOS_IMPUESTOS if nodo.get(k) not in (None, "", [])}
    if datos:
        datos["fi"] = nodo.get("fi") or fuente
    return datos


def construir_dataset(nodos_mef: List[Dict[str, Any]], complemento: Optional[Dict[str, Any]] = None,
                      meta: Optional[Dict[str, Any]] = None,
                      fuente_complemento: str = FUENTE_EXCEL) -> Dict[str, Any]:
    """
    Une el PDF del MEF (fuente principal) con un complemento: el Excel AEC parseado, o los datos
    de la versión anterior del arancel (mismo formato de nodos). Del complemento se toman solo
    IVA/Renta/ISC/OMC/autopartes/observaciones, secciones, capítulos, leyendas y aperturas
    nacionales cuyo NCM padre exista en el PDF.
    """
    comp = complemento or {"secciones": [], "capitulos": [], "nodos": [], "leyendas": {}}
    comp_por_d = {n["d"]: n for n in comp["nodos"]}

    posiciones: List[Dict[str, Any]] = []
    vistos = set()
    for n in nodos_mef:
        d = n["d"]
        nivel = _NIVEL_POR_LARGO.get(len(d))
        if not nivel or d in vistos:
            continue
        vistos.add(d)
        texto, guiones = _limpiar_descripcion(n["t"])
        p: Dict[str, Any] = {"c": n["c"], "d": d, "n": nivel, "t": texto, "g": guiones, "pag": n.get("pag")}
        if nivel == "ncm":
            p.update({"aec": n.get("aec"), "aec_t": n.get("aec_t") or None, "anv": n.get("anv"),
                      "anv_t": n.get("anv_t") or None, "anv_dec": n.get("anv_dec"), "lista": n.get("lista") or None})
            x = comp_por_d.get(d)
            if x and x.get("n") == "ncm":
                p.update(_impuestos(x, fuente_complemento))
        posiciones.append({k: v for k, v in p.items() if v not in (None, "", [])})

    # Partidas que el PDF no imprime: cuando una partida tiene una sola subpartida, el MEF pasa
    # directo a "3303.00 Perfumes y aguas de tocador." sin la fila "33.03". Sin ella el árbol queda
    # incompleto y el asistente de IA no puede elegir la partida. Se toma el texto de esa
    # subpartida "XXXX.00" (o el de la partida del Excel si existiera).
    partidas = {p["d"] for p in posiciones if p["n"] == "partida"}
    por_d_mef = {p["d"]: p for p in posiciones}
    for d4 in sorted({p["d"][:4] for p in posiciones if p["n"] != "partida"} - partidas):
        # El texto de la partida es el de su única subpartida "XXXX.00" o, si el PDF pasa directo
        # al NCM, el de "XXXX.00.00"; si no, el de la partida en el Excel.
        sub00 = por_d_mef.get(d4 + "00") or por_d_mef.get(d4 + "0000")
        excel = comp_por_d.get(d4)
        texto = (sub00 or {}).get("t") or ((excel or {}).get("t") if (excel or {}).get("n") == "partida" else "") or ""
        posiciones.append({k: v for k, v in {"c": f"{d4[:2]}.{d4[2:]}", "d": d4, "n": "partida", "t": texto, "g": 0,
                                             "pag": (sub00 or {}).get("pag")}.items() if v not in (None, "")})
        vistos.add(d4)

    # Aperturas nacionales (más de 8 dígitos) del complemento, solo bajo un NCM vigente
    ncm_vigentes = {p["d"] for p in posiciones if p["n"] == "ncm"}
    for x in comp["nodos"]:
        if x.get("n") == "apertura" and x["d"][:8] in ncm_vigentes and x["d"] not in vistos:
            vistos.add(x["d"])
            p = {"c": x["c"], "d": x["d"], "n": "apertura", "t": x.get("t"), "g": x.get("g", 0),
                 "aec": x.get("aec"), "aec_t": x.get("aec_t"), "anv": x.get("anv"), "anv_t": x.get("anv_t"),
                 "lista": x.get("lista")}
            p.update(_impuestos(x, fuente_complemento))
            p["fi"] = x.get("fi") or f"{fuente_complemento} (apertura nacional)"
            posiciones.append({k: v for k, v in p.items() if v not in (None, "", [])})
    posiciones.sort(key=lambda p: (p["d"][:8], len(p["d"]), p["d"]))

    capitulos_usados = sorted({p["d"][:2] for p in posiciones})
    caps_comp = {c["numero"]: c for c in comp["capitulos"]}
    capitulos = [caps_comp.get(num) or {"numero": num, "seccion": None, "titulo": "", "notas": ""}
                 for num in capitulos_usados]

    ncm = [p for p in posiciones if p["n"] == "ncm"]
    resumen = {
        "ncm": len(ncm),
        "ncm_con_anv": sum(1 for p in ncm if p.get("anv") is not None),
        "ncm_con_impuestos": sum(1 for p in ncm if "iva_t" in p or "isc_t" in p or "renta_t" in p),
        "aperturas": sum(1 for p in posiciones if p["n"] == "apertura"),
        "posiciones": len(posiciones),
        "listas": dict(Counter(p.get("lista") for p in ncm if p.get("lista")).most_common()),
    }
    dataset = {
        "formato": FORMATO_DATASET,
        "meta": dict(meta or {}),
        "secciones": comp["secciones"],
        "capitulos": capitulos,
        "leyendas": comp.get("leyendas") or {},
        "posiciones": posiciones,
        "resumen": resumen,
    }
    dataset["meta"]["hash"] = hash_dataset(dataset)
    return dataset


def hash_dataset(dataset: Dict[str, Any]) -> str:
    contenido = {k: dataset.get(k) for k in ("secciones", "capitulos", "leyendas", "posiciones")}
    return hashlib.sha256(json.dumps(contenido, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def validar_dataset(dataset: Dict[str, Any]) -> None:
    """Rechaza archivos que no parecen un arancel completo (PDF equivocado o formato cambiado)."""
    r = dataset.get("resumen") or {}
    if r.get("ncm", 0) < 5000:
        raise ValueError(f"Solo se reconocieron {r.get('ncm', 0)} códigos NCM: el archivo no parece un "
                         "Arancel Nacional Vigente completo del MEF (o cambió su formato).")
    if r.get("ncm_con_anv", 0) < 0.9 * r["ncm"]:
        raise ValueError(f"Solo {r.get('ncm_con_anv', 0)} de {r['ncm']} NCM tienen tasa ANV legible: "
                         "el formato del PDF no coincide con el esperado.")


def guardar_dataset(dataset: Dict[str, Any], ruta) -> None:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    datos = json.dumps(dataset, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # mtime=0: el mismo contenido produce exactamente el mismo archivo (sin cambios falsos en git)
    with open(ruta, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(datos)


def leer_dataset(ruta) -> Dict[str, Any]:
    with gzip.open(str(ruta), "rb") as f:
        dataset = json.loads(f.read().decode("utf-8"))
    if dataset.get("formato") != FORMATO_DATASET:
        raise ValueError(f"Formato de arancel no soportado: {dataset.get('formato')}")
    return dataset
