from fastapi.testclient import TestClient
from datetime import date
from app.database import init_db, SessionLocal
from app.models import Despacho, DespachoItem, PlanillaValoracion, PlanillaItem
from app.main import app

init_db()
client = TestClient(app)

def create_sample_catalogo_data():
    db = SessionLocal()
    d = db.query(Despacho).filter(Despacho.numero_despacho == "26021ZF2I000919N").first()
    if not d:
        d = Despacho(
            propietario="Cliente Test S.A.",
            numero_despacho="26021ZF2I000919N",
            fecha_despacho=date(2026, 8, 10),
            importador_nombre="FLORACE S.A",
            importador_documento="800409361",
            valor_fob=668385.25,
            archivo_pdf="26021ZF2I000919N.pdf",
            nombre_archivo_original="26021ZF2I000919N.pdf",
            hash_archivo="abc123hash_sample_planillas",
            estado_procesamiento="PROCESADO"
        )
        db.add(d)
        db.flush()

        it = DespachoItem(
            despacho_id=d.id,
            numero_item=1,
            numero_subitem=1,
            codigo_ncm="3303.00.10.000L",
            marca="LATTAFA",
            descripcion="YARA PINK EDP 100ML",
            cantidad=100.0,
            valor_total=1850.0
        )
        db.add(it)
        db.commit()
    db.close()


def test_planillas_list_view():
    response = client.get("/planillas")
    assert response.status_code == 200
    assert "Planillas de Valoración" in response.text


def test_nueva_planilla_view():
    response = client.get("/planillas/nueva")
    assert response.status_code == 200
    assert "PLANILLA DE VALORACION" in response.text
    assert "FACTURA COMERCIAL NRO" in response.text


def test_buscar_catalogo_api():
    create_sample_catalogo_data()
    response = client.get("/api/planillas/buscar-catalogo?q=LATTAFA")
    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert len(data["results"]) >= 1
    assert "LATTAFA" in data["results"][0]["marca"] or "YARA" in data["results"][0]["mercaderia"]


def test_guardar_y_editar_planilla_ciclo_completo():
    # 1. Guardar Borrador
    payload = {
        "titulo": "PLANILLA DE VALORACION",
        "despacho_numero": "26021ZF2I000919N",
        "factura_comercial": "FC-998822",
        "importador": "GAFA S.A.",
        "propietario": "Ali Farhat",
        "fecha_emision": "2026-08-27",
        "estado": "BORRADOR",
        "observaciones": "Planilla de prueba de valoracion automatizada",
        "items": [
            {
                "orden": 1,
                "cantidad": 50.0,
                "mercaderia": "LATTAFA - YARA MOI EDP 100ML",
                "marca": "LATTAFA",
                "codigo_producto": "LAT-YAR-01",
                "precio_factura": 15.50,
                "precio_normal": 18.50,
                "precio_total": 925.0,
                "observacion": ""
            },
            {
                "orden": 2,
                "cantidad": 100.0,
                "mercaderia": "ARMAF - CLUB DE NUIT INTENSE MAN",
                "marca": "ARMAF",
                "codigo_producto": "ARM-CDNI-02",
                "precio_factura": 22.00,
                "precio_normal": 25.00,
                "precio_total": 2500.0,
                "observacion": ""
            }
        ]
    }

    res_save = client.post("/api/planillas", json=payload)
    assert res_save.status_code == 200
    data_save = res_save.json()
    assert data_save["success"] is True
    planilla_id = data_save["id"]
    assert data_save["estado"] == "BORRADOR"

    # 2. Ver en el Editor
    res_view = client.get(f"/planillas/{planilla_id}")
    assert res_view.status_code == 200
    assert "FC-998822" in res_view.text
    assert "GAFA S.A." in res_view.text

    # 3. Ver en Vista de Impresión
    res_print = client.get(f"/planillas/{planilla_id}/imprimir")
    assert res_print.status_code == 200
    assert "PLANILLA DE VALORACION" in res_print.text
    assert "FC-998822" in res_print.text

    # 4. Exportar a Excel
    res_excel = client.get(f"/planillas/{planilla_id}/excel")
    assert res_excel.status_code == 200
    assert "spreadsheetml" in res_excel.headers["content-type"]

    # 5. Exportar a PDF
    res_pdf = client.get(f"/planillas/{planilla_id}/pdf")
    assert res_pdf.status_code == 200
    assert res_pdf.headers["content-type"] == "application/pdf"

    # 6. Finiquitar Planilla
    payload["id"] = planilla_id
    payload["estado"] = "FINIQUITADO"
    res_fini = client.post("/api/planillas", json=payload)
    assert res_fini.status_code == 200
    assert res_fini.json()["estado"] == "FINIQUITADO"

    # 7. Eliminar Planilla
    res_del = client.delete(f"/api/planillas/{planilla_id}")
    assert res_del.status_code == 200
    assert res_del.json()["success"] is True
