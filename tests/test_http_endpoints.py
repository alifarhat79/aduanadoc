from fastapi.testclient import TestClient
from datetime import date
from app.database import init_db, SessionLocal
from app.models import Despacho, DespachoItem
from app.main import app

init_db()
client = TestClient(app)

def create_sample_despacho():
    db = SessionLocal()
    existing = db.query(Despacho).filter(Despacho.numero_despacho == "26021ZF2I000919N").first()
    if existing:
        db.close()
        return existing.id

    d = Despacho(
        propietario="Cliente Test S.A.",
        numero_despacho="26021ZF2I000919N",
        fecha_despacho=date(2026, 8, 10),
        importador_nombre="FLORACE S.A",
        importador_documento="800409361",
        valor_fob=668385.25,
        valor_flete=3000.0,
        valor_seguro=1103.50,
        valor_cif=672488.75,
        moneda="USD",
        canal="VERDE",
        archivo_pdf="26021ZF2I000919N.pdf",
        nombre_archivo_original="26021ZF2I000919N.pdf",
        hash_archivo="abc123hash_sample",
        estado_procesamiento="PROCESADO",
        metadata_extraccion={
            "numero_despacho": {"valor": "26021ZF2I000919N", "confidence": 0.95, "pagina": 1, "metodo": "TEXT", "texto_origen": "DESPACHO NUMERO: 26021ZF2I000919N"},
            "valor_fob": {"valor": 668385.25, "confidence": 0.95, "pagina": 1, "metodo": "TEXT", "texto_origen": "VALOR FOB USD 668.385,25"}
        }
    )
    db.add(d)
    db.flush()
    it = DespachoItem(
        despacho_id=d.id,
        numero_item=1,
        numero_subitem=1,
        codigo_ncm="3303.00.10.000L",
        marca="ARMAF",
        descripcion="ODYSSEY PINK POP 3.4 EDP",
        cantidad=480.0,
        valor_total=9460.80
    )
    db.add(it)
    db.commit()
    res_id = d.id
    db.close()
    return res_id

def test_dashboard_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    assert "ADUANA" in response.text

def test_despachos_list_endpoint():
    response = client.get("/despachos")
    assert response.status_code == 200
    assert "Listado General de Despachos" in response.text

def test_despacho_detalle_view_endpoint():
    desp_id = create_sample_despacho()
    response = client.get(f"/despachos/{desp_id}")
    assert response.status_code == 200
    assert "Auditoría de Campos Extraídos" in response.text
    assert "FLORACE S.A" in response.text

def test_upload_view_endpoint():
    response = client.get("/upload")
    assert response.status_code == 200
    assert "Importación Masiva de Despachos Aduaneros" in response.text
    assert "Dueño / Cliente Propietario" in response.text

def test_revisar_list_endpoint():
    response = client.get("/revisar")
    assert response.status_code == 200
    assert "Despachos Pendientes de Revisión" in response.text

def test_export_csv_endpoint():
    response = client.get("/exportar/csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")

def test_export_excel_endpoint():
    response = client.get("/exportar/excel")
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]

def test_export_html_endpoint():
    response = client.get("/exportar/html")
    assert response.status_code == 200
    assert "Reporte Consolidado de Despachos" in response.text

def test_export_pdf_endpoint():
    response = client.get("/exportar/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"

def test_export_google_sheet_endpoint():
    response = client.get("/exportar/google-sheet")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")

def test_mercancias_list_endpoint():
    response = client.get("/mercancias")
    assert response.status_code == 200
    assert "Catálogo Consolidado de Mercancías" in response.text
    assert "Marcas Destacadas" in response.text

def test_mercancias_export_excel():
    response = client.get("/mercancias/exportar/excel")
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]

def test_mercancias_export_csv():
    response = client.get("/mercancias/exportar/csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")

def test_turso_config_endpoint():
    response = client.get("/turso/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "is_configured" in data

def test_despacho_info_api_endpoint():
    desp_id = create_sample_despacho()
    response = client.get(f"/despachos/api/{desp_id}/info")
    assert response.status_code == 200
    data = response.json()
    assert data["numero_despacho"] == "26021ZF2I000919N"
    assert len(data["campos"]) > 0

def test_configuracion_view_endpoint():
    # Sin autenticar -> pantalla de bloqueo
    response = client.get("/configuracion")
    assert response.status_code == 200
    assert "Acceso Restringido" in response.text

    # Autenticado -> pantalla de configuración
    client.post("/configuracion/login", json={"password": "Sohalia2012*@"})
    res_auth = client.get("/configuracion")
    assert res_auth.status_code == 200
    assert "Conexión con Turso Cloud Database" in res_auth.text
    assert "despachos-alifarhat" in res_auth.text

def test_configuracion_guardar_api():
    import os
    from unittest.mock import patch
    original_token = os.getenv("TURSO_AUTH_TOKEN", "")
    original_url = os.getenv("TURSO_DATABASE_URL", "")

    client.post("/configuracion/login", json={"password": "Sohalia2012*@"})
    payload = {
        "database_url": "libsql://despachos-alifarhat.aws-us-east-1.turso.io",
        "auth_token": original_token or "test_dummy_token"
    }
    with patch("dotenv.set_key"):
        response = client.post("/configuracion/api/guardar", json=payload)
        assert response.status_code == 200
        assert response.json()["success"] is True

    # Restaurar en memoria
    if original_token:
        os.environ["TURSO_AUTH_TOKEN"] = original_token
    if original_url:
        os.environ["TURSO_DATABASE_URL"] = original_url

def test_despacho_pdf_inline_endpoint():
    import os
    # Asegurar un archivo físico dummy para la prueba
    os.makedirs("./uploads/test", exist_ok=True)
    dummy_pdf_path = "./uploads/test/test_dummy.pdf"
    with open(dummy_pdf_path, "wb") as f:
        f.write(b"%PDF-1.4 sample dummy content")

    db = SessionLocal()
    d = db.query(Despacho).filter(Despacho.numero_despacho == "26021ZF2I000919N").first()
    if d:
        d.archivo_pdf = dummy_pdf_path
        db.commit()
        desp_id = d.id
    else:
        desp_id = create_sample_despacho()
    db.close()

    response = client.get(f"/despachos/{desp_id}/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "inline" in response.headers.get("content-disposition", "")

def test_pdf_viewer_assets_and_review_page():
    desp_id = create_sample_despacho()
    response = client.get(f"/revisar/{desp_id}")
    assert response.status_code == 200
    assert "Documento Original" in response.text
    assert f"/despachos/{desp_id}/pdf" in response.text





