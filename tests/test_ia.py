"""
Pruebas del asistente de IA y de la clave de Anthropic en Configuración.
NUNCA llaman a Anthropic: el cliente se reemplaza por uno falso (sin costo ni red).
"""
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import ia_service

client = TestClient(app)


def _admin():
    c = TestClient(app)
    assert c.post("/configuracion/login", json={"password": os.environ["CONFIG_ADMIN_PASSWORD"]}).status_code == 200
    return c


def _respuesta(datos, stop="end_turn"):
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=json.dumps(datos))],
        usage=SimpleNamespace(input_tokens=1200, output_tokens=300, cache_creation_input_tokens=40000,
                              cache_read_input_tokens=0),
    )


class _ClienteFalso:
    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: self._crear("beta", kw)))
        self.messages = SimpleNamespace(create=lambda **kw: self._crear("normal", kw))

    def _crear(self, via, kw):
        self.llamadas.append((via, kw))
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _error(clase, status, mensaje="error"):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return clase(message=mensaje, response=httpx.Response(status, request=req), body=None)


@pytest.fixture
def con_clave(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-prueba-0000000000001234")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-api03-prueba-0000000000001234")
    monkeypatch.setattr(ia_service, "_fallbacks_disponible", True)


@pytest.fixture
def sin_clave(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")


PASO1 = {"resultados": [{"n": 1, "interpretacion": "Auriculares inalámbricos con estuche de carga.",
                         "partidas": ["85.18", "99.99"]}]}
PASO2 = {"sugerencias": [
    {"ncm": "8518.30.00", "confianza": "alta", "fundamento": "Texto de la subpartida 8518.30",
     "notas_aplicadas": "", "por_que_no_otras": ""},
    {"ncm": "8517.99.10", "confianza": "baja", "fundamento": "código que no existe", "notas_aplicadas": "",
     "por_que_no_otras": ""},
], "informacion_faltante": ["¿Incluye micrófono?"], "advertencia": ""}


def test_asistente_sugiere_solo_codigos_del_arancel(con_clave):
    falso = _ClienteFalso([_respuesta(PASO1), _respuesta(PASO2)])
    with patch.object(ia_service, "_cliente", return_value=falso):
        res = client.post("/arancel/api/ia/clasificar", json={"descripcion": "auriculares bluetooth TWS"})
    assert res.status_code == 200, res.text
    r = res.json()
    assert [s["ncm"] for s in r["sugerencias"]] == ["8518.30.00"]      # 8517.99.10 no existe: descartado
    assert r["descartadas"] == ["8517.99.10"]
    s = r["sugerencias"][0]
    assert s["descripcion"] and s["anv"] is not None and s["confianza"] == "alta"
    assert [p["codigo"] for p in r["partidas_revisadas"]][0] == "85.18"   # la partida inventada se ignora
    assert r["consumo"]["usd_estimado"] > 0

    (via1, k1), (via2, k2) = falso.llamadas
    assert via1 == via2 == "beta"
    assert k1["model"] == k2["model"] == "claude-opus-5-5"
    assert k1["betas"] == ["server-side-fallback-2026-07-01"] and k1["extra_body"] == {"fallbacks": "default"}
    cacheado = [b for b in k1["system"] if "cache_control" in b]
    assert cacheado[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}  # lista de partidas en caché 1 h
    assert "LISTA DE PARTIDAS" in cacheado[0]["text"]
    assert k1["output_config"]["format"]["type"] == "json_schema" and k1["output_config"]["effort"] == "medium"
    assert k2["output_config"]["effort"] == "high"
    assert "8518.30.00" in k2["messages"][0]["content"]                     # candidatos completos de la partida
    assert "thinking" not in k1 and "temperature" not in k1


def test_si_la_api_rechaza_fallbacks_reintenta_sin_ellos(con_clave):
    rechazo = _error(anthropic.BadRequestError, 400, "fallbacks: unknown parameter")
    falso = _ClienteFalso([rechazo, _respuesta(PASO1), _respuesta(PASO2)])
    with patch.object(ia_service, "_cliente", return_value=falso):
        res = client.post("/arancel/api/ia/clasificar", json={"descripcion": "auriculares"})
    assert res.status_code == 200
    assert [v for v, _ in falso.llamadas] == ["beta", "normal", "normal"]
    assert ia_service._fallbacks_disponible is False


@pytest.mark.parametrize("error, status, texto", [
    (lambda: _error(anthropic.AuthenticationError, 401), 400, "no es válida"),
    (lambda: _error(anthropic.RateLimitError, 429), 429, "límite"),
    (lambda: anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com")), 503, "conexión"),
])
def test_errores_de_anthropic_con_mensaje_claro(con_clave, error, status, texto):
    with patch.object(ia_service, "_cliente", return_value=_ClienteFalso([error()])):
        res = client.post("/arancel/api/ia/clasificar", json={"descripcion": "auriculares"})
    assert res.status_code == status and texto in res.json()["detail"]


def test_negativa_del_modelo(con_clave):
    with patch.object(ia_service, "_cliente", return_value=_ClienteFalso([_respuesta({}, stop="refusal")])):
        res = client.post("/arancel/api/ia/clasificar", json={"descripcion": "auriculares"})
    assert res.status_code == 422


def test_sin_clave_avisa_configurar(sin_clave):
    assert client.get("/arancel/api/ia/estado").json()["configurada"] is False
    res = client.post("/arancel/api/ia/clasificar", json={"descripcion": "auriculares"})
    assert res.status_code == 400 and "Configuración" in res.json()["detail"]
    assert client.post("/arancel/api/ia/clasificar", json={"descripcion": "a"}).status_code == 422  # muy corta
    assert "Asistente IA" in client.get("/arancel").text


def test_probar_clave_sin_gastar_tokens(con_clave):
    retrieve_ok = SimpleNamespace(with_options=lambda **kw: SimpleNamespace(
        models=SimpleNamespace(retrieve=lambda m: SimpleNamespace(display_name="Claude Opus 5.5"))))
    with patch.object(ia_service, "_cliente", return_value=retrieve_ok):
        assert ia_service.probar_clave()["ok"] is True

    def falla(m):
        raise _error(anthropic.AuthenticationError, 401)
    retrieve_mal = SimpleNamespace(with_options=lambda **kw: SimpleNamespace(models=SimpleNamespace(retrieve=falla)))
    with patch.object(ia_service, "_cliente", return_value=retrieve_mal):
        r = ia_service.probar_clave()
    assert r["ok"] is False and "no es válida" in r["mensaje"]


def test_configuracion_guarda_verifica_y_borra_la_clave(sin_clave):
    from app.routers import configuracion
    anon = TestClient(app)
    assert anon.get("/configuracion/api/ia").status_code == 401
    assert anon.post("/configuracion/api/ia/guardar", json={"api_key": "sk-ant-x"}).status_code == 401
    assert anon.delete("/configuracion/api/ia").status_code == 401

    admin = _admin()
    clave = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV-9876"
    with patch.object(ia_service, "probar_clave", return_value={"ok": False, "mensaje": "La API key de Anthropic no es válida"}):
        r = admin.post("/configuracion/api/ia/guardar", json={"api_key": clave})
    assert r.status_code == 400 and not ia_service.esta_configurada()       # inválida: no se guarda

    with patch.object(ia_service, "probar_clave", return_value={"ok": True, "mensaje": "Clave válida."}):
        r = admin.post("/configuracion/api/ia/guardar", json={"api_key": f"  {clave}  "})
    assert r.status_code == 200 and r.json()["verificada"] is True
    assert ia_service.clave_api() == clave
    assert clave in configuracion.ENV_PATH.read_text(encoding="utf-8")       # .env temporal de las pruebas
    estado = admin.get("/configuracion/api/ia").json()
    assert estado["configurada"] and estado["clave"].endswith("9876") and clave not in json.dumps(estado)

    pagina = admin.get("/configuracion").text
    assert "Inteligencia Artificial (Claude)" in pagina and clave not in pagina  # nunca se muestra completa

    assert admin.delete("/configuracion/api/ia").status_code == 200
    assert not ia_service.esta_configurada()
    assert "ANTHROPIC_API_KEY" not in configuracion.ENV_PATH.read_text(encoding="utf-8")


def test_la_clave_se_puede_distribuir_desde_drive():
    from app.services.secrets_sync import ALLOWED_KEYS
    assert "ANTHROPIC_API_KEY" in ALLOWED_KEYS


# --------------------------------------------------------------------------------------
# Separar una lista o factura entre NCM elegidos
# --------------------------------------------------------------------------------------

class _ClienteLista:
    """Responde según los números de producto del mensaje; soporta stream (factura PDF)."""

    def __init__(self, decidir, extraidos=None):
        self.decidir, self.extraidos, self.llamadas = decidir, extraidos, []
        api = SimpleNamespace(create=lambda **kw: self._crear(kw), stream=lambda **kw: self._stream(kw))
        self.beta = SimpleNamespace(messages=api)
        self.messages = api

    def _crear(self, kw):
        import re as _re
        self.llamadas.append(("create", kw))
        numeros = [int(n) for n in _re.findall(r"^(\d+)\. ", kw["messages"][0]["content"], _re.M)]
        return _respuesta({"resultados": [self.decidir(n) for n in numeros if self.decidir(n)]})

    def _stream(self, kw):
        self.llamadas.append(("stream", kw))
        final = _respuesta({"productos": self.extraidos or []})

        class _Ctx:
            def __enter__(s):
                return SimpleNamespace(get_final_message=lambda: final)

            def __exit__(s, *a):
                return False
        return _Ctx()


def _decide(n):
    if n == 3:
        return None  # la IA "olvida" el 3
    if n == 2:
        return {"n": 2, "ncm": "NINGUNO", "confianza": "media", "motivo": "Es un labial", "otro_ncm": "3304.10.00"}
    return {"n": n, "ncm": "3304.99.10" if n % 2 else "3304.99.90", "confianza": "alta", "motivo": "ok", "otro_ncm": ""}


def test_candidatos_inexistentes_sugieren_el_codigo_correcto():
    r = client.post("/arancel/api/ia/candidatos", data={"candidatos": "3304.99.10, 3301.99.90"})
    assert r.status_code == 400 and "3301.99.90 no existe" in r.json()["detail"] and "3304.99.90" in r.json()["detail"]
    ok = client.post("/arancel/api/ia/candidatos", data={"candidatos": "3304.99.10; 3304.99.90"}).json()
    assert [c["ncm"] for c in ok] == ["3304.99.10", "3304.99.90"]


def test_lista_escrita_se_separa_por_ncm(con_clave):
    falso = _ClienteLista(_decide)
    texto = "CREMA HIDRATANTE 50ML\n- LABIAL ROJO MATE\nSERUM VITAMINA C\n\nTONICO FACIAL 200ML"
    with patch.object(ia_service, "_cliente", return_value=falso):
        r = client.post("/arancel/api/ia/clasificar-lista", data={"candidatos": "3304.99.10, 3304.99.90", "texto": texto})
    assert r.status_code == 200, r.text
    d = r.json()
    assert [p["descripcion"] for p in d["productos"]][:2] == ["CREMA HIDRATANTE 50ML", "LABIAL ROJO MATE"]
    assert [p["ncm"] for p in d["productos"]] == ["3304.99.10", "NINGUNO", "SIN_RESPUESTA", "3304.99.90"]
    assert d["productos"][1]["otro_ncm"].startswith("3304.10.00")
    assert d["resumen"] == {"3304.99.10": 1, "3304.99.90": 1, "NINGUNO": 1, "SIN_RESPUESTA": 1}
    (_, kw), = falso.llamadas
    assert kw["output_config"]["format"]["schema"]["properties"]["resultados"]["items"]["properties"]["ncm"]["enum"] == \
        ["3304.99.10", "3304.99.90", "NINGUNO"]
    assert kw["system"][-1]["cache_control"] == {"type": "ephemeral"} and "3304.99.10" in kw["system"][-1]["text"]


def test_lista_larga_en_lotes(con_clave):
    falso = _ClienteLista(lambda n: {"n": n, "ncm": "3304.99.90", "confianza": "alta", "motivo": "ok", "otro_ncm": ""})
    texto = "\n".join(f"PRODUCTO COSMETICO NUMERO {i}" for i in range(1, 86))
    with patch.object(ia_service, "_cliente", return_value=falso):
        d = client.post("/arancel/api/ia/clasificar-lista", data={"candidatos": "3304.99.10, 3304.99.90", "texto": texto}).json()
    assert len(falso.llamadas) == 3 and d["resumen"]["3304.99.90"] == 85 and d["resumen"]["SIN_RESPUESTA"] == 0

    demasiados = "\n".join(f"PRODUCTO {i}" for i in range(301))
    r = client.post("/arancel/api/ia/clasificar-lista", data={"candidatos": "3304.99.10", "texto": demasiados})
    assert r.status_code == 400 and "máximo" in r.json()["detail"]


def test_planilla_excel_sin_precios_y_csv_sin_encabezado(con_clave):
    import io
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    for fila in (["Item", "Descripción", "Marca", "Cantidad"], [1, "CREMA DE MANOS 100ML", "NIVEA", 24],
                 [2, "SERUM FACIAL 30ML", "CERAVE", 12], [None, "TOTAL", None, 36]):
        ws.append(fila)
    buf = io.BytesIO()
    wb.save(buf)
    falso = _ClienteLista(lambda n: {"n": n, "ncm": "3304.99.10", "confianza": "alta", "motivo": "ok", "otro_ncm": ""})
    with patch.object(ia_service, "_cliente", return_value=falso):
        d = client.post("/arancel/api/ia/clasificar-lista", data={"candidatos": "3304.99.10, 3304.99.90"},
                        files={"archivo": ("lista.xlsx", buf.getvalue(), "application/octet-stream")}).json()
    assert [(p["descripcion"], p["marca"]) for p in d["productos"]] == [("CREMA DE MANOS 100ML", "NIVEA"), ("SERUM FACIAL 30ML", "CERAVE")]
    assert d["origen"] == "planilla"

    csv = "CREMA CORPORAL 400ML;12\nMASCARILLA FACIAL;5\n".encode("utf-8")
    with patch.object(ia_service, "_cliente", return_value=falso):
        d = client.post("/arancel/api/ia/clasificar-lista", data={"candidatos": "3304.99.10"},
                        files={"archivo": ("lista.csv", csv, "text/csv")}).json()
    assert [p["descripcion"] for p in d["productos"]] == ["CREMA CORPORAL 400ML", "MASCARILLA FACIAL"]


def test_factura_pdf_la_lee_claude(con_clave):
    extraidos = [{"descripcion": "LOCION TONICA 200ML", "marca": "X", "codigo": "123", "cantidad": "10"},
                 {"descripcion": "SUBTOTAL", "marca": "", "codigo": "", "cantidad": ""}]
    falso = _ClienteLista(lambda n: {"n": n, "ncm": "3304.99.10", "confianza": "alta", "motivo": "Loción tónica", "otro_ncm": ""},
                          extraidos=extraidos)
    with patch.object(ia_service, "_cliente", return_value=falso):
        d = client.post("/arancel/api/ia/clasificar-lista", data={"candidatos": "3304.99.10, 3304.99.90"},
                        files={"archivo": ("factura.pdf", b"%PDF-1.4 factura", "application/pdf")}).json()
    assert d["origen"] == "factura PDF" and [p["ncm"] for p in d["productos"]] == ["3304.99.10", "3304.99.10"]
    via, kw = falso.llamadas[0]
    assert via == "stream" and kw["max_tokens"] == 64000
    doc = kw["messages"][0]["content"][0]
    assert doc["type"] == "document" and doc["source"]["media_type"] == "application/pdf"

    r = client.post("/arancel/api/ia/clasificar-lista", data={"candidatos": "3304.99.10"},
                    files={"archivo": ("lista.docx", b"x", "application/octet-stream")})
    assert r.status_code == 400 and "Formato" in r.json()["detail"]


def test_excel_con_una_hoja_por_ncm():
    import io
    openpyxl = pytest.importorskip("openpyxl")

    def prod(n, desc, ncm, otro=""):
        return {"n": n, "descripcion": desc, "marca": "", "codigo": "", "cantidad": "", "ncm": ncm,
                "descripcion_ncm": "", "confianza": "alta", "motivo": "ok", "otro_ncm": otro}
    resultado = {
        "modelo": "claude-opus-5-5", "origen": "planilla",
        "candidatos": [{"ncm": "3304.99.10", "descripcion": "Cremas de belleza y cremas nutritivas; lociones tónicas",
                        "anv": 6.0, "iva_t": "1,5*"},
                       {"ncm": "3304.99.90", "descripcion": "Las demás", "anv": 6.0, "iva_t": "1,5*"}],
        "productos": [prod(1, "CREMA DE MANOS 100ML", "3304.99.10"),
                      prod(2, '=HYPERLINK("http://malicioso","clic")', "3304.99.90"),
                      prod(3, "SERUM VITAMINA C 30ML", "3304.99.90"),
                      prod(4, "LABIAL ROJO", "NINGUNO", "3304.10.00 Preparaciones para el maquillaje de los labios")],
        "consumo": {"usd_estimado": 0.05},
    }
    r = client.post("/arancel/api/ia/lista-xlsx", json=resultado)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert 'attachment; filename="clasificacion_ncm_' in r.headers["content-disposition"]

    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Resumen", "Todos", "3304.99.10", "3304.99.90", "Ninguno"]
    filas = lambda hoja: [f for f in wb[hoja].iter_rows(min_row=2, values_only=True)]
    assert [f[1] for f in filas("3304.99.10")] == ["CREMA DE MANOS 100ML"]
    assert [f[1] for f in filas("3304.99.90")] == ['=HYPERLINK("http://malicioso","clic")', "SERUM VITAMINA C 30ML"]
    assert wb["3304.99.90"]["B2"].data_type == "s"          # texto, no fórmula
    assert filas("Ninguno")[0][-1].startswith("3304.10.00")
    assert len(filas("Todos")) == 4
    resumen = {f[0]: f[4] for f in wb["Resumen"].iter_rows(min_row=6, values_only=True) if f and f[0]}
    assert resumen["3304.99.10"] == 1 and resumen["3304.99.90"] == 2 and resumen["Total"] == 4

    assert client.post("/arancel/api/ia/lista-xlsx", json={"productos": []}).status_code == 400


# --------------------------------------------------------------------------------------
# Modo automático: sin NCM elegidos, la IA busca el NCM de cada producto
# --------------------------------------------------------------------------------------

class _ClienteAuto:
    """Paso 1 (partidas) y paso 2 (NCM) se distinguen por el esquema de salida pedido."""

    def __init__(self, paso1, paso2):
        self.paso1, self.paso2, self.llamadas = paso1, paso2, []
        api = SimpleNamespace(create=lambda **kw: self._crear(kw))
        self.beta = SimpleNamespace(messages=api)
        self.messages = api

    def _crear(self, kw):
        import re as _re
        self.llamadas.append(kw)
        props = kw["output_config"]["format"]["schema"]["properties"]["resultados"]["items"]["properties"]
        texto = kw["messages"][0]["content"].split("POSICIONES CANDIDATAS")[0]  # solo la lista de productos
        numeros = [int(n) for n in _re.findall(r"^(\d+)\. ", texto, _re.M)]
        decidir = self.paso1 if "partidas" in props else self.paso2
        return _respuesta({"resultados": [decidir(n) for n in numeros]})


def test_modo_automatico_identifica_cada_producto(con_clave):
    paso1 = {1: ("Perfume masculino (eau de parfum)", ["33.03"]), 2: ("Crema facial", ["33.04"]),
             3: ("No identificado", ["99.99"])}
    paso2 = {1: "3303.00.10", 2: "3304.99.10", 3: "8517.99.10"}  # el 3 no existe en el arancel
    falso = _ClienteAuto(lambda n: {"n": n, "interpretacion": paso1[n][0], "partidas": paso1[n][1]},
                         lambda n: {"n": n, "ncm": paso2[n], "confianza": "alta", "motivo": "ok"})
    texto = "LATTAFA ASAD BOURBON EDP 100ML\nCREMA HIDRATANTE FACIAL 50ML\nMODELO DESCONOCIDO XZ"
    with patch.object(ia_service, "_cliente", return_value=falso):
        r = client.post("/arancel/api/ia/clasificar-lista", data={"candidatos": "", "texto": texto})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["modo"] == "automatico"
    assert [p["ncm"] for p in d["productos"]] == ["3303.00.10", "3304.99.10", "SIN_RESPUESTA"]
    assert d["productos"][0]["motivo"].startswith("Perfume masculino")
    assert "8517.99.10" in d["productos"][2]["motivo"]
    assert [c["ncm"] for c in d["candidatos"]] == ["3303.00.10", "3304.99.10"]
    assert d["resumen"]["SIN_RESPUESTA"] == 1

    paso1_kw, paso2_kw = falso.llamadas
    cacheado = [b for b in paso1_kw["system"] if "cache_control" in b]
    assert "LISTA DE PARTIDAS" in cacheado[0]["text"] and cacheado[0]["cache_control"]["ttl"] == "1h"
    mensaje2 = paso2_kw["messages"][0]["content"]
    assert "3303.00.10" in mensaje2 and "3304.99.10" in mensaje2           # posiciones completas de 33.03 y 33.04
    assert "Qué es (paso anterior): Perfume masculino" in mensaje2


def test_modo_automatico_usa_el_historial_del_modelo(con_clave):
    """Un producto que viene solo con el modelo se reconoce por cómo se declaró antes."""
    from datetime import date as _date
    from app.database import SessionLocal
    from app.models import Despacho, DespachoItem
    db = SessionLocal()
    try:
        d = Despacho(numero_despacho="IA-HIST-0001", fecha_despacho=_date(2026, 9, 1), archivo_pdf="h.pdf",
                     nombre_archivo_original="h.pdf", hash_archivo="hash_ia_hist_0001", estado_procesamiento="PROCESADO")
        db.add(d)
        db.flush()
        db.add(DespachoItem(despacho_id=d.id, numero_item=1, codigo_ncm="3303.00.10.000L",
                            descripcion="LATTAFA KHAMRAH QAHWA EDP 100ML", marca="LATTAFA", valor_total=10.0))
        db.commit()
    finally:
        db.close()

    falso = _ClienteAuto(lambda n: {"n": n, "interpretacion": "Desconocido", "partidas": []},  # la IA no sabe qué es
                         lambda n: {"n": n, "ncm": "3303.00.10", "confianza": "media", "motivo": "Por el historial"})
    with patch.object(ia_service, "_cliente", return_value=falso):
        d = client.post("/arancel/api/ia/clasificar-lista", data={"texto": "KHAMRAH QAHWA"}).json()
    assert d["productos"][0]["ncm"] == "3303.00.10"
    assert "En los despachos del usuario: 3303.00.10" in falso.llamadas[0]["messages"][0]["content"]
    assert "Partida 33.03" in falso.llamadas[1]["messages"][0]["content"]  # la partida del historial se revisa igual


def test_excel_con_muchos_ncm_encontrados():
    import io
    openpyxl = pytest.importorskip("openpyxl")
    codigos = [f"3304.99.{i:02d}" for i in range(12)]
    resultado = {"modelo": "claude-opus-5-5", "modo": "automatico", "origen": "texto",
                 "candidatos": [{"ncm": c, "descripcion": "x", "anv": 6.0, "iva_t": "10"} for c in codigos],
                 "productos": [{"n": i + 1, "descripcion": f"P{i}", "marca": "", "codigo": "", "cantidad": "", "ncm": c,
                                "descripcion_ncm": "x", "confianza": "alta", "motivo": "ok", "otro_ncm": ""}
                               for i, c in enumerate(codigos)],
                 "consumo": {}}
    r = client.post("/arancel/api/ia/lista-xlsx", json=resultado)
    assert r.status_code == 200
    assert len(openpyxl.load_workbook(io.BytesIO(r.content)).sheetnames) == 2 + len(codigos)


# --------------------------------------------------------------------------------------
# Sugerir NCM: búsqueda en internet cuando la IA no tiene una respuesta definitiva
# --------------------------------------------------------------------------------------

def _paso2(confianza):
    return {"sugerencias": [{"ncm": "8518.30.00", "confianza": confianza, "fundamento": "Auriculares",
                             "notas_aplicadas": "", "por_que_no_otras": ""}],
            "informacion_faltante": ["¿Tiene micrófono?"], "advertencia": ""}


def _respuesta_web(texto="Es un auricular inalámbrico TWS con micrófono incorporado.", stop="end_turn", busquedas=2):
    cita = SimpleNamespace(url="https://fabricante.example/auricular-tws", title="Ficha técnica del fabricante")
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="server_tool_use", name="web_search"),
                 SimpleNamespace(type="web_search_tool_result",
                                 content=[SimpleNamespace(url="https://tienda.example/tws", title="Tienda")]),
                 SimpleNamespace(type="text", text=texto, citations=[cita])],
        usage=SimpleNamespace(input_tokens=5000, output_tokens=400, cache_creation_input_tokens=0,
                              cache_read_input_tokens=0, server_tool_use=SimpleNamespace(web_search_requests=busquedas)),
    )


def test_sugerir_ncm_busca_en_internet_si_no_esta_segura(con_clave):
    falso = _ClienteFalso([_respuesta(PASO1), _respuesta(_paso2("media")), _respuesta_web(), _respuesta(_paso2("alta"))])
    with patch.object(ia_service, "_cliente", return_value=falso):
        r = client.post("/arancel/api/ia/clasificar", json={"descripcion": "auriculares TWS modelo X9"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(falso.llamadas) == 4
    _, web = falso.llamadas[2]
    assert web["tools"] == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}]
    assert "format" not in web["output_config"]                          # texto libre (las citas no admiten JSON)
    assert "auriculares TWS modelo X9" in web["messages"][0]["content"]
    _, final = falso.llamadas[3]
    assert "INVESTIGACIÓN EN INTERNET" in final["messages"][0]["content"] and "8518.30.00" in final["messages"][0]["content"]
    assert d["sugerencias"][0]["ncm"] == "8518.30.00" and d["sugerencias"][0]["confianza"] == "alta"
    w = d["investigacion_web"]
    assert w["usada"] is True and w["busquedas"] == 2 and "TWS" in w["resumen"]
    assert {f["url"] for f in w["fuentes"]} == {"https://fabricante.example/auricular-tws", "https://tienda.example/tws"}
    assert d["consumo"]["busquedas_web"] == 2 and d["consumo"]["usd_estimado"] >= 0.02


def test_no_busca_si_ya_esta_segura_o_si_se_desactiva(con_clave):
    falso = _ClienteFalso([_respuesta(PASO1), _respuesta(_paso2("alta"))])
    with patch.object(ia_service, "_cliente", return_value=falso):
        d = client.post("/arancel/api/ia/clasificar", json={"descripcion": "auriculares"}).json()
    assert len(falso.llamadas) == 2 and d["investigacion_web"] is None

    falso = _ClienteFalso([_respuesta(PASO1), _respuesta(_paso2("baja"))])
    with patch.object(ia_service, "_cliente", return_value=falso):
        d = client.post("/arancel/api/ia/clasificar", json={"descripcion": "auriculares", "buscar_internet": False}).json()
    assert len(falso.llamadas) == 2 and d["investigacion_web"] is None


def test_si_falla_la_busqueda_queda_la_respuesta_anterior(con_clave):
    falso = _ClienteFalso([_respuesta(PASO1), _respuesta(_paso2("media")),
                           _error(anthropic.BadRequestError, 400, "web search is not enabled for this organization")])
    with patch.object(ia_service, "_cliente", return_value=falso):
        r = client.post("/arancel/api/ia/clasificar", json={"descripcion": "auriculares"})
    assert r.status_code == 200
    d = r.json()
    assert d["sugerencias"][0]["ncm"] == "8518.30.00" and d["sugerencias"][0]["confianza"] == "media"
    assert "No se pudo buscar en internet" in d["investigacion_web"]["error"]


def test_busqueda_pausada_se_reanuda(con_clave):
    falso = _ClienteFalso([_respuesta(PASO1), _respuesta(_paso2("baja")),
                           _respuesta_web(texto="", stop="pause_turn", busquedas=1), _respuesta_web(busquedas=1),
                           _respuesta(_paso2("alta"))])
    with patch.object(ia_service, "_cliente", return_value=falso):
        d = client.post("/arancel/api/ia/clasificar", json={"descripcion": "auriculares"}).json()
    _, reanudar = falso.llamadas[3]
    assert [m["role"] for m in reanudar["messages"]] == ["user", "assistant"]  # sin un "continuar" extra
    assert d["investigacion_web"]["busquedas"] == 2


def test_sugerir_y_lista_comparten_la_cache_de_partidas(con_clave):
    """Mismo prefijo (instrucciones base + lista de partidas) en los dos modos: una sola escritura de caché."""
    uno = _ClienteFalso([_respuesta(PASO1), _respuesta(_paso2("alta"))])
    with patch.object(ia_service, "_cliente", return_value=uno):
        client.post("/arancel/api/ia/clasificar", json={"descripcion": "auriculares"})
    lista = _ClienteAuto(lambda n: {"n": n, "interpretacion": "x", "partidas": ["85.18"]},
                         lambda n: {"n": n, "ncm": "8518.30.00", "confianza": "alta", "motivo": "ok"})
    with patch.object(ia_service, "_cliente", return_value=lista):
        client.post("/arancel/api/ia/clasificar-lista", data={"texto": "AURICULARES TWS"})
    prefijo = lambda sistema: [b["text"] for b in sistema[:2]]
    assert prefijo(uno.llamadas[0][1]["system"]) == prefijo(lista.llamadas[0]["system"])
    # el esquema de salida y el esfuerzo también forman parte del prefijo en caché
    assert uno.llamadas[0][1]["output_config"] == lista.llamadas[0]["output_config"]


# --------------------------------------------------------------------------------------
# Costo estimado ANTES de consultar (ventana de confirmación) y consumo registrado
# --------------------------------------------------------------------------------------

@pytest.fixture
def consumo_aislado(tmp_path, monkeypatch):
    ruta = tmp_path / "ia_consumo.jsonl"
    monkeypatch.setattr(ia_service, "_ruta_consumo", lambda: ruta)
    return ruta


def _sin_ia(*a, **kw):
    raise AssertionError("La estimación no debe llamar a la IA")


def test_estimar_sugerir_no_usa_la_ia_ni_la_clave(sin_clave, consumo_aislado):
    with patch.object(ia_service, "_cliente", side_effect=_sin_ia):
        con_web = client.post("/arancel/api/ia/estimar", json={"descripcion": "auriculares TWS"})
        sin_web = client.post("/arancel/api/ia/estimar", json={"descripcion": "auriculares TWS", "buscar_internet": False})
    assert con_web.status_code == 200 and sin_web.status_code == 200, con_web.text
    a, b = con_web.json(), sin_web.json()
    assert a["cache_activa"] is False and "Primera consulta de la hora" in a["detalle"][0]
    # calibrado con consultas reales del 26-11: US$ 0,63 sin internet y US$ 1,01 con internet (caché fría)
    assert b["usd_min"] <= 0.63 <= b["usd_max"]
    assert a["usd_min"] <= 1.01 <= a["usd_max"]
    assert a["usd_max"] > b["usd_max"] and a["usd_min"] == b["usd_min"]  # internet solo suma si se usa
    assert any("internet" in d for d in a["detalle"]) and not any("internet" in d for d in b["detalle"])
    assert a["consumo"] == {"hoy_usd": 0, "consultas_hoy": 0, "mes_usd": 0}
    assert client.post("/arancel/api/ia/estimar", json={"descripcion": "x"}).status_code == 422
    assert not consumo_aislado.exists()  # estimar no registra consumo


def test_con_la_cache_activa_la_estimacion_baja(con_clave, consumo_aislado):
    fria = client.post("/arancel/api/ia/estimar", json={"descripcion": "auriculares", "buscar_internet": False}).json()
    falso = _ClienteFalso([_respuesta(PASO1), _respuesta(_paso2("alta"))])
    with patch.object(ia_service, "_cliente", return_value=falso):
        d = client.post("/arancel/api/ia/clasificar", json={"descripcion": "auriculares"}).json()
    linea, = [json.loads(x) for x in consumo_aislado.read_text(encoding="utf-8").splitlines()]
    assert linea["tipo"] == "sugerir" and linea["cache_partidas"] is True and linea["usd"] == d["consumo"]["usd_estimado"]

    tibia = client.post("/arancel/api/ia/estimar", json={"descripcion": "auriculares", "buscar_internet": False}).json()
    assert tibia["cache_activa"] is True and "ya está en caché" in tibia["detalle"][0]
    assert fria["usd_min"] - tibia["usd_min"] > 0.4             # la lista de partidas ya no se paga completa
    assert tibia["usd_min"] <= 0.09 <= tibia["usd_max"] < 0.2    # real: 0,63 - 0,54 de la escritura en caché
    assert tibia["consumo"]["consultas_hoy"] == 1 and tibia["consumo"]["hoy_usd"] == round(linea["usd"], 3)
    estado = client.get("/arancel/api/ia/estado").json()
    assert estado["consumo"]["consultas_hoy"] == 1

    # Pasada la hora, la caché ya no cuenta como activa
    vieja = {**linea, "fecha": "2026-01-01T08:00:00"}
    consumo_aislado.write_text(json.dumps(vieja) + "\n", encoding="utf-8")
    assert client.post("/arancel/api/ia/estimar", json={"descripcion": "auriculares"}).json()["cache_activa"] is False
    # De otra versión del arancel tampoco (el prefijo en caché sería distinto)
    consumo_aislado.write_text(json.dumps({**linea, "version": "otra edición"}) + "\nlinea rota\n", encoding="utf-8")
    assert client.post("/arancel/api/ia/estimar", json={"descripcion": "auriculares"}).json()["cache_activa"] is False


def test_si_no_se_puede_registrar_el_consumo_la_consulta_sigue(con_clave, tmp_path, monkeypatch):
    monkeypatch.setattr(ia_service, "_ruta_consumo", lambda: tmp_path)  # una carpeta: no se puede escribir
    falso = _ClienteFalso([_respuesta(PASO1), _respuesta(_paso2("alta"))])
    with patch.object(ia_service, "_cliente", return_value=falso):
        r = client.post("/arancel/api/ia/clasificar", json={"descripcion": "auriculares"})
    assert r.status_code == 200 and r.json()["sugerencias"][0]["ncm"] == "8518.30.00"


def test_estimar_lista_cuenta_los_productos_sin_la_ia(sin_clave, consumo_aislado):
    import io
    texto = "\n".join(f"PRODUCTO COSMETICO NUMERO {i}" for i in range(1, 86))
    with patch.object(ia_service, "_cliente", side_effect=_sin_ia):
        elegidos = client.post("/arancel/api/ia/estimar-lista", data={"candidatos": "3304.99.10, 3304.99.90", "texto": texto})
        auto = client.post("/arancel/api/ia/estimar-lista", data={"candidatos": "", "texto": texto})
    assert elegidos.status_code == 200 and auto.status_code == 200, elegidos.text
    e, a = elegidos.json(), auto.json()
    assert e["productos"] == [85, 85] and any("3 lote(s)" in d for d in e["detalle"])
    assert "2 NCM elegido(s)" in e["detalle"][0] and 0 < e["usd_min"] < e["usd_max"] < 1
    assert a["productos"] == [85, 85] and "Búsqueda automática" in a["detalle"][0]
    assert a["usd_min"] > e["usd_min"] + 0.4                     # incluye guardar la lista de partidas en caché
    assert a["cache_activa"] is False and any("Primera consulta" in d for d in a["detalle"])

    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    for fila in (["Descripción", "Marca"], ["CREMA DE MANOS 100ML", "NIVEA"], ["SERUM FACIAL 30ML", "CERAVE"]):
        wb.active.append(fila)
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/arancel/api/ia/estimar-lista", data={"candidatos": "3304.99.10"},
                    files={"archivo": ("lista.xlsx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 200 and r.json()["productos"] == [2, 2]

    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page().insert_text((72, 72), "FACTURA - CREMA FACIAL 50ML x 12")
    pdf = doc.tobytes()
    r = client.post("/arancel/api/ia/estimar-lista", data={"candidatos": ""},
                    files={"archivo": ("factura.pdf", pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    f = r.json()
    assert f["productos"] == [16, 70] and "2 página(s)" in f["detalle"][1] and f["usd_max"] > f["usd_min"]

    grande = pymupdf.open()
    for _ in range(40):
        grande.new_page()
    r = client.post("/arancel/api/ia/estimar-lista", data={"candidatos": "3304.99.10"},
                    files={"archivo": ("factura.pdf", grande.tobytes(), "application/pdf")})
    assert r.status_code == 200 and r.json()["productos"] == [300, 300]  # se estima, no se rechaza

    errores = [
        ({"candidatos": "3304.99.10, 3301.99.90", "texto": "CREMA"}, None, "3301.99.90 no existe"),
        ({"candidatos": "3304.99.10", "texto": "\n".join(f"P {i}" for i in range(301))}, None, "máximo"),
        ({"candidatos": "3304.99.10", "texto": "  \n "}, None, "No se encontraron productos"),
        ({"candidatos": ""}, ("factura.pdf", b"no es un pdf", "application/pdf"), "No se pudo abrir el PDF"),
        ({"candidatos": ""}, ("lista.docx", b"x", "application/octet-stream"), "Formato"),
    ]
    for datos, archivo, texto_error in errores:
        r = client.post("/arancel/api/ia/estimar-lista", data=datos, files={"archivo": archivo} if archivo else None)
        assert r.status_code == 400 and texto_error in r.json()["detail"], (datos, r.text)
    assert not consumo_aislado.exists()


def test_cada_lista_registra_su_consumo(con_clave, consumo_aislado):
    falso = _ClienteLista(lambda n: {"n": n, "ncm": "3304.99.90", "confianza": "alta", "motivo": "ok", "otro_ncm": ""})
    with patch.object(ia_service, "_cliente", return_value=falso):
        client.post("/arancel/api/ia/clasificar-lista", data={"candidatos": "3304.99.10, 3304.99.90", "texto": "SERUM"})
    falso = _ClienteAuto(lambda n: {"n": n, "interpretacion": "Crema facial", "partidas": ["33.04"]},
                         lambda n: {"n": n, "ncm": "3304.99.10", "confianza": "alta", "motivo": "ok"})
    with patch.object(ia_service, "_cliente", return_value=falso):
        client.post("/arancel/api/ia/clasificar-lista", data={"candidatos": "", "texto": "CREMA FACIAL"})
    lineas = [json.loads(x) for x in consumo_aislado.read_text(encoding="utf-8").splitlines()]
    assert [(x["tipo"], x["cache_partidas"]) for x in lineas] == [("lista_elegidos", False), ("lista_automatica", True)]
    assert all(x["usd"] > 0 for x in lineas)
    assert ia_service.resumen_consumo()["consultas_hoy"] == 2


def test_la_pagina_pide_confirmar_el_costo():
    html = client.get("/arancel").text
    assert 'id="modalCosto"' in html and "Aceptar y consultar" in html
    assert "/arancel/api/ia/estimar'" in html and "/arancel/api/ia/estimar-lista'" in html
