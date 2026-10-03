import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.services.gdrive_watcher import GDriveWatcher
from app.services.gdrive_service import GoogleDriveService

client = TestClient(app)


@pytest.fixture(autouse=True)
def limpiar_pendientes():
    GDriveWatcher.get_instance().consume_pending()
    GoogleDriveService.failed_files.clear()
    yield
    GDriveWatcher.get_instance().consume_pending()
    GoogleDriveService.failed_files.clear()


def _resultado_scan(*numeros):
    return {
        "nuevos_procesados": len(numeros),
        "detalles": [
            {"estado": "PROCESADO_EXITOSO", "numero_despacho": n, "importador": "EMPRESA TEST"} for n in numeros
        ] + [{"estado": "OMITIDO", "archivo": "viejo.pdf"}],
    }


def test_watcher_registra_y_consume_pendientes_sin_duplicar():
    w = GDriveWatcher.get_instance()
    w.register_imports(_resultado_scan("26021ZF2I000001A", "26021ZF2I000002B"))
    w.register_imports(_resultado_scan("26021ZF2I000001A"))  # repetido
    assert [p["numero"] for p in w.peek_pending()] == ["26021ZF2I000001A", "26021ZF2I000002B"]
    assert len(w.consume_pending()) == 2
    assert w.peek_pending() == []


def test_check_new_sin_drive_reporta_pendientes_del_vigilante():
    GDriveWatcher.get_instance().register_imports(_resultado_scan("26021ZF2I000003C"))
    resp = client.get("/api/sync/check-new?drive=false")
    assert resp.status_code == 200
    data = resp.json()
    assert data["hay_nuevos"] is True
    assert data["pendientes"][0]["numero"] == "26021ZF2I000003C"
    assert data["nuevos_en_drive"] == []


def test_check_new_con_drive_detecta_archivos_nuevos():
    with patch.object(GoogleDriveService, "is_api_available", return_value=True), \
         patch.object(GoogleDriveService, "detect_new_files", return_value=["26021ZF2I000004D.pdf"]):
        data = client.get("/api/sync/check-new?drive=true").json()
    assert data["hay_nuevos"] is True
    assert data["nuevos_en_drive"] == ["26021ZF2I000004D.pdf"]


def test_check_new_sin_novedades():
    with patch.object(GoogleDriveService, "is_api_available", return_value=True), \
         patch.object(GoogleDriveService, "detect_new_files", return_value=[]):
        data = client.get("/api/sync/check-new?drive=true").json()
    assert data["hay_nuevos"] is False


def test_detect_new_files_excluye_existentes_y_fallidos():
    svc = GoogleDriveService()
    GoogleDriveService.failed_files["roto.pdf"] = "error"
    archivos = [{"name": "existe.pdf"}, {"name": "nuevo.pdf"}, {"name": "roto.pdf"}]
    with patch.object(GoogleDriveService, "get_drive_service", return_value=MagicMock()), \
         patch.object(GoogleDriveService, "_list_pdf_files", return_value=archivos), \
         patch.object(GoogleDriveService, "_find_existing_by_name",
                      side_effect=lambda db, n: object() if n == "existe.pdf" else None):
        assert svc.detect_new_files(db=MagicMock()) == ["nuevo.pdf"]


def test_stream_incluye_despachos_importados_por_el_vigilante():
    GDriveWatcher.get_instance().register_imports(_resultado_scan("26021ZF2I000005E"))
    with patch.object(GoogleDriveService, "is_api_available", return_value=True), \
         patch.object(GoogleDriveService, "scan_and_process_folder", return_value={"detalles": []}), \
         patch("app.services.turso_service.TursoService.is_configured", return_value=False), \
         patch("app.services.updater_service.UpdaterService.git_pull", return_value={}):
        with client.stream("GET", "/api/sync/stream") as resp:
            body = "".join(resp.iter_lines())
    assert "26021ZF2I000005E" in body
    assert GDriveWatcher.get_instance().peek_pending() == []
