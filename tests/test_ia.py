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


PASO1 = {"interpretacion": "Auriculares inalámbricos con estuche de carga.",
         "partidas": [{"codigo": "85.18", "motivo": "Auriculares"}, {"codigo": "99.99", "motivo": "inventada"}]}
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
    assert k1["system"][-1]["cache_control"] == {"type": "ephemeral"}       # lista de partidas en caché
    assert "LISTA DE PARTIDAS" in k1["system"][-1]["text"]
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
