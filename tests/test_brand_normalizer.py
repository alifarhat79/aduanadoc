import pytest
from datetime import date
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import Despacho, DespachoItem, MarcaRegla
from app.services.brand_normalizer import (
    unify_brands,
    rename_single_brand,
    detect_brand_clusters,
    get_normalized_brand,
    create_manual_rule,
    delete_rule,
    get_all_rules
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_brands():
    db = SessionLocal()

    # Despacho de prueba
    d = Despacho(
        numero_despacho="TESTBRAND202601",
        fecha_despacho=date(2026, 9, 1),
        importador_nombre="IMPORTADORA TEST BRANDS",
        canal="VERDE",
        archivo_pdf="dummy_brands.pdf",
        nombre_archivo_original="dummy_brands.pdf",
        hash_archivo="hash_dummy_brands_2026"
    )
    db.add(d)
    db.flush()

    # Ítems con variaciones de marca
    it1 = DespachoItem(despacho_id=d.id, numero_item=1, descripcion="POD DESECHABLE V1", marca="LIF POD", cantidad=10.0)
    it2 = DespachoItem(despacho_id=d.id, numero_item=2, descripcion="POD DESECHABLE V2", marca="LIFE POD", cantidad=20.0)
    it3 = DespachoItem(despacho_id=d.id, numero_item=3, descripcion="POD RECARGABLE V3", marca="LIFEPOD", cantidad=30.0)
    it4 = DespachoItem(despacho_id=d.id, numero_item=4, descripcion="SMARTPHONE NOTE 12", marca="XIOMI", cantidad=5.0)

    db.add_all([it1, it2, it3, it4])
    db.commit()

    despacho_id = d.id
    db.close()

    yield

    # Cleanup
    db = SessionLocal()
    db.query(DespachoItem).filter(DespachoItem.despacho_id == despacho_id).delete(synchronize_session=False)
    db.query(Despacho).filter(Despacho.id == despacho_id).delete(synchronize_session=False)
    db.query(MarcaRegla).filter(
        MarcaRegla.patron_origen.in_(["LIF POD", "LIFE POD", "XIOMI", "TEST PATRON"])
    ).delete(synchronize_session=False)
    db.commit()
    db.close()


def test_detect_brand_clusters():
    """Verifica que el detector agrupe 'LIF POD', 'LIFE POD' y 'LIFEPOD' por similitud."""
    db = SessionLocal()
    try:
        clusters = detect_brand_clusters(db)
        # Buscar el cluster que contiene las variantes de lifepod
        lifepod_cluster = None
        for cl in clusters:
            variantes = [v["marca"].upper() for v in cl["variantes"]]
            if "LIFEPOD" in variantes or "LIFE POD" in variantes or "LIF POD" in variantes:
                lifepod_cluster = cl
                break

        assert lifepod_cluster is not None
        marcas_en_cluster = [v["marca"].upper() for v in lifepod_cluster["variantes"]]
        assert "LIFEPOD" in marcas_en_cluster or "LIFE POD" in marcas_en_cluster
    finally:
        db.close()


def test_unify_brands_service():
    """Verifica que unify_brands consolide 'LIF POD' y 'LIFE POD' en 'LIFEPOD' y genere reglas."""
    db = SessionLocal()
    try:
        res = unify_brands(
            db=db,
            marcas_origen=["LIF POD", "LIFE POD"],
            marca_destino="LIFEPOD",
            crear_regla=True
        )
        assert res["success"] is True
        assert res["items_actualizados"] >= 2

        # Verificar que los ítems ahora tengan 'LIFEPOD'
        items = db.query(DespachoItem).filter(DespachoItem.marca == "LIFEPOD").all()
        assert len(items) >= 3

        # Verificar que no queden ítems con 'LIF POD' ni 'LIFE POD'
        assert db.query(DespachoItem).filter(DespachoItem.marca.in_(["LIF POD", "LIFE POD"])).count() == 0

        # Verificar reglas
        assert get_normalized_brand("LIF POD", db) == "LIFEPOD"
        assert get_normalized_brand("LIFE POD", db) == "LIFEPOD"
        # Marca sin regla devuelve original
        assert get_normalized_brand("OTRA MARCA CUALQUIERA", db) == "OTRA MARCA CUALQUIERA"
    finally:
        db.close()


def test_rename_single_brand():
    """Verifica renombrar 'XIOMI' a 'XIAOMI'."""
    db = SessionLocal()
    try:
        res = rename_single_brand(
            db=db,
            marca_actual="XIOMI",
            nueva_marca="XIAOMI",
            crear_regla=True
        )
        assert res["success"] is True
        assert res["items_actualizados"] == 1

        assert db.query(DespachoItem).filter(DespachoItem.marca == "XIAOMI").count() >= 1
        assert db.query(DespachoItem).filter(DespachoItem.marca == "XIOMI").count() == 0
        assert get_normalized_brand("XIOMI", db) == "XIAOMI"
    finally:
        db.close()


def test_marcas_http_endpoints():
    """Prueba los endpoints HTTP y APIs de /marcas."""
    # 1. Vista HTML
    resp_html = client.get("/marcas")
    assert resp_html.status_code == 200
    assert "Gestor y Corrección de Marcas" in resp_html.text
    assert "Unificar" in resp_html.text

    # 2. API lista de marcas
    resp_list = client.get("/marcas/api/marcas")
    assert resp_list.status_code == 200
    data = resp_list.json()
    assert isinstance(data, list)

    # 3. API Reglas manuales
    resp_rule = client.post("/marcas/api/reglas", json={"patron_origen": "TEST PATRON", "marca_destino": "TEST DESTINO"})
    assert resp_rule.status_code == 200
    rule_id = resp_rule.json()["id"]

    # 4. API Listar Reglas
    resp_get_rules = client.get("/marcas/api/reglas")
    assert resp_get_rules.status_code == 200
    assert any(r["id"] == rule_id for r in resp_get_rules.json())

    # 5. API Eliminar Regla
    resp_del = client.delete(f"/marcas/api/reglas/{rule_id}")
    assert resp_del.status_code == 200
    assert resp_del.json()["success"] is True
