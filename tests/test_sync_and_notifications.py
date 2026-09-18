import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.services.windows_notification_service import WindowsNotificationService
from app.services.updater_service import UpdaterService

client = TestClient(app)

def test_windows_notification_methods():
    # Probar que las funciones de notificación se ejecutan limpiamente
    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0)
        WindowsNotificationService.notify_new_despacho("26021ZF2I000100A", "EMPRESA TEST")
        WindowsNotificationService.notify_batch_despachos(2, ["26021ZF2I000100A", "26021ZF2I000101B"])

def test_api_sync_immediate_gdrive():
    with patch("app.services.gdrive_service.GoogleDriveService.is_api_available", return_value=True), \
         patch("app.services.gdrive_service.GoogleDriveService.scan_and_process_folder", return_value={"total_encontrados": 5, "nuevos_procesados": 0, "detalles": []}):
        resp = client.get("/api/sync/immediate-gdrive")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["nuevos_procesados"] == 0

def test_api_sync_stream():
    with patch("app.services.gdrive_service.GoogleDriveService.is_api_available", return_value=False), \
         patch("app.services.turso_service.TursoService.is_configured", return_value=False):
        with client.stream("GET", "/api/sync/stream") as resp:
            assert resp.status_code == 200
            events = []
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    events.append(line)
            assert len(events) >= 3

def test_updater_git_push_safe():
    updater = UpdaterService()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        res = updater.git_push("test commit")
        assert "success" in res
