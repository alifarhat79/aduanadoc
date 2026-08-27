import pytest
from datetime import date, datetime
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models import Despacho, DespachoItem
from app.services.notification_service import NotificationService
from app.services.updater_service import UpdaterService

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    db = SessionLocal()

    # Crear despacho 1 de prueba (Fecha: 2026-08-10)
    d1 = Despacho(
        numero_despacho="TEST26001IMPORT01",
        fecha_despacho=date(2026, 8, 10),
        importador_nombre="IMPORTADORA TEST ALFA SA",
        propietario="CLIENTE TEST ALFA",
        canal="VERDE",
        valor_fob=15000.0,
        valor_cif=16200.0,
        archivo_pdf="dummy_test_1.pdf",
        nombre_archivo_original="dummy_test_1.pdf",
        hash_archivo="hash_dummy_test_1"
    )
    db.add(d1)
    db.flush()

    item1 = DespachoItem(
        despacho_id=d1.id,
        numero_item=1,
        codigo_ncm="8517.13.00",
        codigo_producto="7891234567890",
        descripcion="SMARTPHONE PRO MAX 256GB TEST",
        marca="SAMSUNG",
        cantidad=10.0,
        valor_unitario=1500.0,
        valor_total=15000.0
    )
    db.add(item1)

    # Crear despacho 2 de prueba (Fecha: 2026-08-25)
    d2 = Despacho(
        numero_despacho="TEST26002IMPORT02",
        fecha_despacho=date(2026, 8, 25),
        importador_nombre="IMPORTADORA TEST BETA SRL",
        propietario="CLIENTE TEST BETA",
        canal="ROJO",
        valor_fob=8000.0,
        valor_cif=8900.0,
        archivo_pdf="dummy_test_2.pdf",
        nombre_archivo_original="dummy_test_2.pdf",
        hash_archivo="hash_dummy_test_2"
    )
    db.add(d2)
    db.flush()

    item2 = DespachoItem(
        despacho_id=d2.id,
        numero_item=1,
        codigo_ncm="8471.30.12",
        codigo_producto="12345678",
        descripcion="NOTEBOOK SLIM I7 TEST",
        marca="DELL",
        cantidad=8.0,
        valor_unitario=1000.0,
        valor_total=8000.0
    )
    db.add(item2)
    db.commit()

    d1_id = d1.id
    d2_id = d2.id
    db.close()

    yield

    # Cleanup específico solo de los registros de prueba
    db = SessionLocal()
    db.query(DespachoItem).filter(DespachoItem.despacho_id.in_([d1_id, d2_id])).delete(synchronize_session=False)
    db.query(Despacho).filter(Despacho.id.in_([d1_id, d2_id])).delete(synchronize_session=False)
    db.commit()
    db.close()


def test_mercancias_date_filter_html():
    """Prueba que el filtro por fechas restrinja los ítems en /mercancias."""
    # Filtrar solo el rango que incluye d1 (2026-08-01 a 2026-08-15)
    resp1 = client.get("/mercancias?fecha_desde=2026-08-01&fecha_hasta=2026-08-15&q=TEST")
    assert resp1.status_code == 200
    assert "SMARTPHONE" in resp1.text
    assert "NOTEBOOK" not in resp1.text

    # Filtrar solo el rango que incluye d2 (2026-08-20 a 2026-08-30)
    resp2 = client.get("/mercancias?fecha_desde=2026-08-20&fecha_hasta=2026-08-30&q=TEST")
    assert resp2.status_code == 200
    assert "SMARTPHONE" not in resp2.text
    assert "NOTEBOOK" in resp2.text


def test_mercancias_date_filter_export_excel_and_csv():
    """Prueba que las exportaciones a Excel y CSV respeten el rango de fechas."""
    # Export Excel con filtro
    resp_excel = client.get("/mercancias/exportar/excel?fecha_desde=2026-08-01&fecha_hasta=2026-08-15")
    assert resp_excel.status_code == 200
    assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in resp_excel.headers["content-type"]
    assert len(resp_excel.content) > 100

    # Export CSV con filtro
    resp_csv = client.get("/mercancias/exportar/csv?fecha_desde=2026-08-20&fecha_hasta=2026-08-30")
    assert resp_csv.status_code == 200
    csv_text = resp_csv.content.decode("utf-8-sig")
    assert "NOTEBOOK" in csv_text
    assert "SMARTPHONE" not in csv_text


def test_notification_service_formatting_and_telegram_mock():
    """Prueba la construcción del mensaje y el envío simulado a Telegram y Webhook."""
    service = NotificationService(
        telegram_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        telegram_chat_id="-1001234567890",
        webhook_url="https://example.com/webhook"
    )

    despacho_sample = {
        "numero_despacho": "26001IMPORT01",
        "importador_nombre": "EMPRESA PRUEBA SA",
        "propietario": "CLIENTE VIP",
        "canal": "VERDE",
        "valor_fob": 15000.0,
        "valor_cif": 16200.0,
        "nombre_archivo_original": "despacho_prueba.pdf"
    }

    with patch("httpx.Client.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Probar notificación de nuevo despacho
        res = service.notify_new_despacho(despacho_sample, items_count=5, source="Auto-Vigilante")
        assert res["success"] is True
        assert res["results"]["telegram"]["success"] is True
        assert res["results"]["webhook"]["success"] is True

        # Probar notificación de prueba
        test_res = service.send_test_notification()
        assert test_res["success"] is True


def test_notification_api_endpoints():
    """Prueba los endpoints de guardar y probar configuración de notificaciones."""
    client.post("/configuracion/login", json={"password": "Sohalia2012*@"})
    # Guardar configuración
    payload = {
        "telegram_bot_token": "987654:TESTTOKEN",
        "telegram_chat_id": "12345678",
        "webhook_url": "https://httpbin.org/post",
        "notifications_enabled": True
    }
    resp_save = client.post("/configuracion/api/notificaciones/guardar", json=payload)
    assert resp_save.status_code == 200
    assert resp_save.json()["success"] is True

    # Test con simulación de NotificationService.send_test_notification
    with patch.object(NotificationService, "send_test_notification") as mock_test:
        mock_test.return_value = {
            "success": True,
            "message": "Notificación de prueba enviada con éxito.",
            "results": {"telegram": {"success": True}}
        }

        resp_test = client.post("/configuracion/api/notificaciones/test", json=payload)
        assert resp_test.status_code == 200
        assert resp_test.json()["success"] is True


def test_git_status_and_pull_endpoints():
    """Prueba los endpoints de estado de Git y Git Pull."""
    # 1. Status
    resp_status = client.get("/api/updates/git/status")
    assert resp_status.status_code == 200
    data = resp_status.json()
    assert "has_git" in data

    # 2. Git Pull (con simulación para no modificar el entorno de trabajo real)
    with patch.object(UpdaterService, "git_pull") as mock_pull:
        mock_pull.return_value = {
            "success": True,
            "output": "Already up to date.",
            "message": "Sincronización Git exitosa: Already up to date."
        }
        resp_pull = client.post("/api/updates/git/pull")
        assert resp_pull.status_code == 200
        assert resp_pull.json()["success"] is True
