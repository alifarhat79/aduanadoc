"""
Pruebas del módulo Arancel (usa la base temporal de conftest; nunca la base real).
"""
import io
import os
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import ArancelVersion, Despacho, DespachoItem
from app.services import arancel_service
from app.services.arancel_importer import (
    construir_dataset, hash_dataset, parsear_excel_aec, validar_dataset,
)

client = TestClient(app)


def _admin_client():
    c = TestClient(app)
    res = c.post("/configuracion/login", json={"password": os.environ["CONFIG_ADMIN_PASSWORD"]})
    assert res.status_code == 200
    return c


# --------------------------------------------------------------------------------------
# Importador (unidades)
# --------------------------------------------------------------------------------------

def _nodos_mef_minimos():
    return [
        {"c": "01.01", "d": "0101", "t": "Caballos, asnos y mulos, vivos.", "aec_t": "", "anv_t": "", "lista": "",
         "pag": 1, "aec": None, "anv": None, "anv_dec": None},
        {"c": "0101.21.00", "d": "01012100", "t": "- - Reproductores de raza pura", "aec_t": "0", "anv_t": "0",
         "lista": "", "pag": 1, "aec": 0.0, "anv": 0.0, "anv_dec": None},
        {"c": "0101.29.00", "d": "01012900", "t": "- - Los demás", "aec_t": "2", "anv_t": "1,8 DEC. 08/22",
         "lista": "DEC. 08/22", "pag": 1, "aec": 2.0, "anv": 1.8, "anv_dec": "DEC. 08/22"},
    ]


def _complemento_minimo():
    return {
        "secciones": [{"numero": "I", "titulo": "ANIMALES VIVOS", "notas": "1. Nota de sección."}],
        "capitulos": [{"numero": "01", "seccion": "I", "titulo": "Animales vivos", "notas": "1. Nota del capítulo."}],
        "leyendas": {"*": "Registro Especial"},
        "nodos": [
            {"c": "0101.29.00", "d": "01012900", "n": "ncm", "iva": 1.5, "iva_t": "1,5*", "renta_t": "0,4*",
             "renta": 0.4, "isc": 5.0, "isc_t": "5"},
            {"c": "0101.29.00.100", "d": "01012900100", "n": "apertura", "t": "Apertura nacional", "g": 0,
             "anv": 2.0, "anv_t": "2", "iva_t": "10", "iva": 10.0},
            {"c": "9999.99.99.100", "d": "99999999100", "n": "apertura", "t": "Sin padre vigente", "anv": 5.0},
        ],
    }


def test_construir_dataset_une_mef_con_complemento():
    ds = construir_dataset(_nodos_mef_minimos(), _complemento_minimo(),
                           meta={"nombre": "Prueba", "fecha_vigencia": "2030-01-01"})
    por_d = {p["d"]: p for p in ds["posiciones"]}

    # Del PDF: descripción sin guiones de sangría, ANV con su decreto
    assert por_d["01012900"]["t"] == "Los demás" and por_d["01012900"]["g"] == 2
    assert por_d["01012900"]["anv"] == 1.8 and por_d["01012900"]["anv_dec"] == "DEC. 08/22"
    # Del complemento: solo impuestos, con su fuente
    assert por_d["01012900"]["iva_t"] == "1,5*" and por_d["01012900"]["fi"] == "Excel AEC 2017"
    assert "iva_t" not in por_d["01012100"] and "fi" not in por_d["01012100"]
    # Aperturas: solo bajo un NCM vigente
    assert por_d["01012900100"]["n"] == "apertura" and "apertura nacional" in por_d["01012900100"]["fi"]
    assert "99999999100" not in por_d
    assert ds["capitulos"][0]["titulo"] == "Animales vivos"
    # Mismo contenido -> mismo hash (identidad de la versión)
    assert ds["meta"]["hash"] == hash_dataset(ds)
    # Un arancel tan chico no pasa la validación (PDF equivocado o formato cambiado)
    with pytest.raises(ValueError):
        validar_dataset(ds)


def test_parsear_pdf_mef_encabezado_bajo_las_notas_y_dec_en_el_borde(tmp_path):
    """
    En la página donde empieza un capítulo, el encabezado de la tabla está debajo del título y de
    las notas legales: antes se salteaba la primera página (01.01-01.03 faltaban) y en las demás se
    leían las notas como filas. La referencia "DEC." puede caer en el borde de ANV: va a LISTA.
    """
    pymupdf = pytest.importorskip("pymupdf")
    from app.services.arancel_importer import parsear_pdf_mef

    doc = pymupdf.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((95, 100), "Capitulo 1 - Animales vivos", fontsize=9)
    p1.insert_text((83, 140), "Nota 1. Este Capitulo comprende todos los animales vivos, excepto:", fontsize=8)
    p1.insert_text((83, 152), "03.01 los peces de la partida citada (no es una fila de la tabla)", fontsize=8)
    for x, t in ((95, "NCM"), (248, "DESCRIPCIÓN"), (433, "AEC"), (465, "ANV"), (511, "LISTA")):
        p1.insert_text((x, 385), t, fontsize=8)
    p1.insert_text((436, 394), "%", fontsize=8)
    p1.insert_text((468, 394), "%", fontsize=8)
    filas = [
        (410, [(83, "01.01"), (132, "Caballos, asnos y mulos, vivos.")]),
        (422, [(83, "0101.21.00"), (132, "- - Reproductores de raza pura"), (449, "0"), (479, "0")]),
        (434, [(83, "0101.29.00"), (132, "- - Los demás"), (449, "18"), (479, "16,2"), (497, "DEC."), (520, "08/22")]),
    ]
    for y, palabras in filas:
        for x, t in palabras:
            p1.insert_text((x, y), t, fontsize=8)
    p2 = doc.new_page(width=595, height=842)
    for x, t in ((95, "NCM"), (248, "DESCRIPCIÓN"), (433, "AEC"), (465, "ANV"), (511, "LISTA")):
        p2.insert_text((x, 99), t, fontsize=8)
    for x, t in ((83, "0101.30.00"), (132, "- Asnos"), (449, "2"), (479, "2"), (513, "LNE")):
        p2.insert_text((x, 125), t, fontsize=8)
    ruta = tmp_path / "mef.pdf"
    doc.save(ruta)

    nodos = {n["c"]: n for n in parsear_pdf_mef(ruta)}
    assert list(nodos) == ["01.01", "0101.21.00", "0101.29.00", "0101.30.00"]  # sin "03.01" de las notas
    assert nodos["0101.21.00"]["anv"] == 0.0 and nodos["0101.21.00"]["aec"] == 0.0
    assert nodos["0101.29.00"]["anv"] == 16.2 and nodos["0101.29.00"]["anv_t"] == "16,2"
    assert nodos["0101.29.00"]["lista"] == "DEC. 08/22"
    assert nodos["0101.30.00"]["lista"] == "LNE" and nodos["0101.30.00"]["pag"] == 2


def test_partidas_que_el_pdf_no_imprime_se_reconstruyen():
    """El MEF imprime "3303.00 Perfumes y aguas de tocador." sin la fila "33.03": la partida se crea."""
    nodos = [
        {"c": "3303.00", "d": "330300", "t": "Perfumes y aguas de tocador.", "aec_t": "", "anv_t": "", "lista": "",
         "pag": 168, "aec": None, "anv": None, "anv_dec": None},
        {"c": "3303.00.10", "d": "33030010", "t": "Perfumes (extractos)", "aec_t": "18", "anv_t": "6", "lista": "LNE",
         "pag": 168, "aec": 18.0, "anv": 6.0, "anv_dec": None},
        {"c": "0205.00.00", "d": "02050000", "t": "Carne de animales de la especie caballar.", "aec_t": "10",
         "anv_t": "10", "lista": "", "pag": 18, "aec": 10.0, "anv": 10.0, "anv_dec": None},
    ]
    ds = construir_dataset(nodos, None, meta={"nombre": "x"})
    por_d = {p["d"]: p for p in ds["posiciones"]}
    assert por_d["3303"]["n"] == "partida" and por_d["3303"]["c"] == "33.03"
    assert por_d["3303"]["t"] == "Perfumes y aguas de tocador."
    assert por_d["0205"]["t"] == "Carne de animales de la especie caballar."  # desde el NCM XXXX.00.00
    assert [p["d"] for p in ds["posiciones"]].index("3303") < [p["d"] for p in ds["posiciones"]].index("330300")


def test_parsear_pdf_codigo_y_descripcion_desalineados(tmp_path):
    """Código y descripción a 1 punto de altura de diferencia: antes quedaban en renglones distintos."""
    pymupdf = pytest.importorskip("pymupdf")
    from app.services.arancel_importer import parsear_pdf_mef
    doc = pymupdf.open()
    pag = doc.new_page(width=595, height=842)
    for x, t in ((95, "NCM"), (248, "DESCRIPCIÓN"), (433, "AEC"), (465, "ANV"), (511, "LISTA")):
        pag.insert_text((x, 99), t, fontsize=8)
    pag.insert_text((75, 112), "71.17", fontsize=8)
    pag.insert_text((124, 112), "Bisutería.", fontsize=8)              # descripción en x=124 (pág. 308 del MEF)
    pag.insert_text((83, 124), "8541.51.00", fontsize=8)
    for x, t in ((132, "- -"), (143, "Transductores basados en semiconductores")):  # 1 pt más abajo que el código
        pag.insert_text((x, 125.2), t, fontsize=8)
    pag.insert_text((449, 125.2), "5,4", fontsize=8)
    pag.insert_text((479, 125.2), "2", fontsize=8)
    pag.insert_text((513, 125.2), "BIT", fontsize=8)
    ruta = tmp_path / "mef.pdf"
    doc.save(ruta)
    nodos = {x["c"]: x for x in parsear_pdf_mef(ruta)}
    assert nodos["71.17"]["t"] == "Bisutería."
    n = nodos["8541.51.00"]
    assert "Transductores basados en semiconductores" in n["t"]
    assert n["anv"] == 2.0 and n["lista"] == "BIT"


def test_parsear_excel_aec_formato_xlsx(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    filas = [
        ["NCM", "DESCRIPCIÓN", "AEC", "ANV"],
        [None, "Sección I"],
        [None, "ANIMALES VIVOS Y PRODUCTOS DEL REINO ANIMAL"],
        [None, "Notas."],
        [None, "1. Nota de la sección."],
        [None, "Capítulo 1"],
        [None, "Animales vivos"],
        [None, "Nota."],
        [None, "1. Este Capítulo comprende todos los animales vivos."],
        ["01.01", "Caballos, asnos, mulos y burdéganos, vivos."],
        ["0101.21.00", "- - Reproductores de raza pura", 0, 0, None, None, None, None, 5, None, None, "V y A"],
        ["0101.29.00", "- - Los demás", 2, 2, None, "LNE", None, None, "1,5*", "0,4*", "5", "Dto. 1.234/20"],
        ["0101.29.00.100", "Ponis", 2, 2, None, None, None, None, 10],
        ["(*) Se aplicarán a firmas inscriptas en el Registro Especial"],
    ]
    for f in filas:
        ws.append(f)
    ruta = tmp_path / "aec.xlsx"
    wb.save(ruta)

    r = parsear_excel_aec(ruta)
    assert r["secciones"][0]["numero"] == "I"
    assert r["secciones"][0]["titulo"].startswith("ANIMALES VIVOS")
    assert "Nota de la sección" in r["secciones"][0]["notas"]
    assert r["capitulos"][0]["numero"] == "01" and r["capitulos"][0]["titulo"] == "Animales vivos"
    por_c = {n["c"]: n for n in r["nodos"]}
    assert por_c["01.01"]["n"] == "partida"
    assert por_c["0101.29.00"]["t"] == "Los demás" and por_c["0101.29.00"]["g"] == 2
    assert por_c["0101.29.00"]["iva_t"] == "1,5*" and por_c["0101.29.00"]["iva"] == 1.5
    assert por_c["0101.29.00"]["lista"] == "LNE" and por_c["0101.29.00"]["obs"] == ["Dto. 1.234/20"]
    assert por_c["0101.21.00"]["obs"] == ["V y A"]
    assert por_c["0101.29.00.100"]["n"] == "apertura"
    assert r["leyendas"]["*"].startswith("Se aplicarán")


# --------------------------------------------------------------------------------------
# Arancel incluido con el sistema
# --------------------------------------------------------------------------------------

def test_arancel_incluido_se_carga_y_responde():
    res = client.get("/arancel/api/resumen")
    assert res.status_code == 200
    data = res.json()
    assert data["version"]["activa"] is True
    assert "octubre 2025" in data["version"]["nombre"]
    assert data["kpi"]["ncm"] > 10000
    assert any(c["numero"] == "85" for c in data["capitulos"])
    assert any(l["lista"] == "BIT" and l["descripcion"] for l in data["listas"])

    # Página y enlace en el menú
    page = client.get("/arancel")
    assert page.status_code == 200
    assert "Arancel de Aduanas" in page.text and 'href="/arancel"' in page.text


def test_buscador_por_texto_sin_tildes_y_por_codigo():
    res = client.get("/arancel/api/buscar", params={"q": "telefonos inteligentes"})
    assert res.status_code == 200
    assert res.json()["resultados"][0]["c"] == "8517.13.00"

    res = client.get("/arancel/api/buscar", params={"q": "3303.00"})
    codigos = [r["c"] for r in res.json()["resultados"]]
    assert codigos == ["3303.00.10", "3303.00.20"]

    res = client.get("/arancel/api/buscar", params={"capitulo": "33", "lista": "LNE"})
    assert all(r["c"].startswith("33") and r["lista"] == "LNE" for r in res.json()["resultados"])


def test_ficha_ncm_con_impuestos_y_leyendas():
    res = client.get("/arancel/api/ncm/3303.00.10.000L")  # formato de los PDFs de despacho
    assert res.status_code == 200
    f = res.json()
    assert f["c"] == "3303.00.10" and f["t"] == "Perfumes (extractos)"
    assert f["anv"] == 6.0 and f["aec"] == 18.0 and f["lista"] == "LNE"
    assert f["iva_t"] == "1,5*" and f["renta_t"] == "0,4*" and f["isc_t"] == "5"
    assert "*" in f["leyendas"]
    # La partida 33.03 no figura como fila en el PDF del MEF (pasa directo a "3303.00"): se reconstruye
    assert [a["c"] for a in f["ancestros"]] == ["33.03", "3303.00"]
    assert any(h["c"] == "3303.00.20" for h in f["hermanos"])
    assert f["capitulo"]["numero"] == "33" and f["capitulo"]["notas"]

    assert client.get("/arancel/api/ncm/8517.99.10").status_code == 404  # no existe
    assert client.get("/arancel/api/capitulo/95").json()["capitulo"]["titulo"]  # título de respaldo
    assert client.get("/arancel/api/capitulo/xx").status_code == 400


# --------------------------------------------------------------------------------------
# Validación de despachos (solo lectura)
# --------------------------------------------------------------------------------------

def _crear_despacho_con_items():
    db = SessionLocal()
    try:
        d = Despacho(numero_despacho="ARANCEL-TEST-0001", fecha_despacho=date(2026, 9, 1),
                     importador_nombre="Importadora Prueba", valor_fob=1000.0, moneda="USD",
                     archivo_pdf="arancel_test.pdf", nombre_archivo_original="arancel_test.pdf",
                     hash_archivo="hash_arancel_test_0001", estado_procesamiento="PROCESADO", tipo_cambio=7000.0)
        db.add(d)
        db.flush()
        items = [
            ("3303.00.10.000L", 0.06, "PERFUME A", 400.0),   # correcto
            ("3303.00.10.000L", 0.18, "PERFUME B", 300.0),   # tasa distinta del ANV (6%)
            ("8517.99.10.000T", 0.06, "AMPLIFICADOR", 200.0),  # NCM inexistente
            (None, None, "SIN CODIGO", 100.0),               # sin NCM
        ]
        for i, (ncm, tasa, desc, valor) in enumerate(items, start=1):
            db.add(DespachoItem(despacho_id=d.id, numero_item=i, codigo_ncm=ncm, tasa_arancel=tasa,
                                descripcion=desc, marca="MARCA X", valor_total=valor))
        db.commit()
        return d.id
    finally:
        db.close()


def _foto(despacho_id):
    db = SessionLocal()
    try:
        d = db.query(Despacho).filter(Despacho.id == despacho_id).one()
        fila_d = {c.name: getattr(d, c.name) for c in Despacho.__table__.columns}
        filas_i = [{c.name: getattr(it, c.name) for c in DespachoItem.__table__.columns}
                   for it in db.query(DespachoItem).filter(DespachoItem.despacho_id == despacho_id).order_by(DespachoItem.id)]
        return fila_d, filas_i
    finally:
        db.close()


def test_validacion_en_detalle_y_global_sin_modificar_datos():
    despacho_id = _crear_despacho_con_items()
    antes = _foto(despacho_id)

    db = SessionLocal()
    try:
        idx = arancel_service.indice(db)
        assert arancel_service.evaluar(idx, "3303.00.10.000L", 0.06)["estado"] == "ok"
        assert arancel_service.evaluar(idx, "3303.00.10.000L", 0.18)["estado"] == "tasa_distinta"
        assert arancel_service.evaluar(idx, "8517.99.10.000T", 0.06)["estado"] == "no_existe"
        assert arancel_service.evaluar(idx, "3303.00", 0.06)["estado"] == "no_existe"  # incompleto
        assert arancel_service.evaluar(idx, "", None)["estado"] == "sin_ncm"
    finally:
        db.close()

    res = client.get(f"/despachos/{despacho_id}")
    assert res.status_code == 200
    html = res.text
    assert 'id="resumenArancel"' in html
    assert "bi-x-octagon-fill" in html and "bi-exclamation-triangle-fill" in html and "bi-check-circle-fill" in html
    assert "/arancel?ncm=3303.00.10.000L" in html

    val = client.get("/arancel/api/validacion").json()
    no_enc = {x["codigo"]: x for x in val["no_encontrados"]}
    assert "8517.99.10.000T" in no_enc
    assert any(e["id"] == despacho_id for e in no_enc["8517.99.10.000T"]["ejemplos"])
    assert any(x["c"] == "3303.00.10" and any(t == 18.0 for t, _ in x["tasas_pdf"]) for x in val["tasa_distinta"])

    mios = client.get("/arancel/api/mios").json()
    assert any(m["c"] == "3303.00.10" and m["uso"]["items"] >= 2 for m in mios)

    # Nada de lo que vino del despacho cambió
    assert _foto(despacho_id) == antes


# --------------------------------------------------------------------------------------
# Versiones: importar / activar / eliminar
# --------------------------------------------------------------------------------------

def test_endpoints_de_versiones_exigen_programador():
    anon = TestClient(app)
    pdf = ("a.pdf", b"%PDF-1.4", "application/pdf")
    assert anon.post("/arancel/api/importar", files={"pdf": pdf},
                     data={"nombre": "x", "fecha_vigencia": "2030-01-01"}).status_code == 401
    assert anon.post("/arancel/api/versiones/1/activar").status_code == 401
    assert anon.delete("/arancel/api/versiones/1").status_code == 401
    assert anon.get("/arancel/api/versiones").status_code == 200  # ver es libre


def test_importar_rechaza_archivos_que_no_son_un_arancel():
    pymupdf = pytest.importorskip("pymupdf")
    admin = _admin_client()
    db = SessionLocal()
    try:
        antes = db.query(ArancelVersion).count()
    finally:
        db.close()

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "NCM DESCRIPCION AEC ANV LISTA - documento cualquiera")
    buf = io.BytesIO(doc.tobytes())
    res = admin.post("/arancel/api/importar", files={"pdf": ("otro.pdf", buf, "application/pdf")},
                     data={"nombre": "PDF cualquiera", "fecha_vigencia": "2030-01-01"})
    assert res.status_code == 422
    assert "no parece un Arancel" in res.json()["detail"]

    res = admin.post("/arancel/api/importar", files={"pdf": ("arancel.txt", b"hola", "text/plain")},
                     data={"nombre": "x", "fecha_vigencia": "2030-01-01"})
    assert res.status_code == 400

    db = SessionLocal()
    try:
        assert db.query(ArancelVersion).count() == antes
    finally:
        db.close()


def test_activar_y_eliminar_versiones():
    admin = _admin_client()
    db = SessionLocal()
    try:
        original = arancel_service.asegurar_arancel(db)
        ds = construir_dataset(_nodos_mef_minimos(), _complemento_minimo(),
                               meta={"nombre": "Versión de prueba", "fecha_vigencia": "2030-01-01"})
        prueba = arancel_service.cargar_dataset(db, ds, origen="importado", activar=False)
        original_id, prueba_id = original.id, prueba.id
        dif = arancel_service.diferencias(db, prueba_id, original_id)
        assert dif["nuevos"] == 0 and dif["eliminados"] > 10000
    finally:
        db.close()

    try:
        assert admin.post(f"/arancel/api/versiones/{prueba_id}/activar").status_code == 200
        assert client.get("/arancel/api/resumen").json()["version"]["nombre"] == "Versión de prueba"
        assert admin.delete(f"/arancel/api/versiones/{prueba_id}").status_code == 409  # activa
    finally:
        assert admin.post(f"/arancel/api/versiones/{original_id}/activar").status_code == 200

    assert "octubre 2025" in client.get("/arancel/api/resumen").json()["version"]["nombre"]
    assert admin.delete(f"/arancel/api/versiones/{prueba_id}").status_code == 200
    assert all(v["id"] != prueba_id for v in client.get("/arancel/api/versiones").json())
    assert admin.delete("/arancel/api/versiones/999999").status_code == 404
