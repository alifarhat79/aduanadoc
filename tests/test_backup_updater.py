import os
import json
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.services.backup_service import BackupService
from app.services.updater_service import UpdaterService
from app.config import settings

client = TestClient(app)

def test_backup_service_create_and_list():
    service = BackupService()
    result = service.create_system_backup(reason="TEST_UNIT")
    assert result["success"] is True
    assert result["filename"].startswith("backup_aduanadoc_")
    assert result["filename"].endswith(".zip")
    assert os.path.exists(result["path"])
    assert result["size_mb"] >= 0

    # Listar backups
    backups = service.list_backups()
    assert len(backups) > 0
    assert any(b["filename"] == result["filename"] for b in backups)

    # Path seguro
    path = service.get_backup_file_path(result["filename"])
    assert path is not None
    assert path.exists()

def test_backup_api_endpoints():
    # 1. Crear backup por API
    res = client.post("/api/backup/crear", json={"reason": "TEST_API"})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    filename = data["filename"]

    # 2. Listar backups por API
    res_list = client.get("/api/backup/listar")
    assert res_list.status_code == 200
    assert len(res_list.json()["backups"]) > 0

    # 3. Descargar backup por API
    res_down = client.get(f"/api/backup/download/{filename}")
    assert res_down.status_code == 200
    assert res_down.headers["content-type"] == "application/zip"
    assert len(res_down.content) > 0

    # 4. Eliminar backup por API
    res_del = client.delete(f"/api/backup/{filename}")
    assert res_del.status_code == 200
    assert res_del.json()["success"] is True

def test_updater_service_publish_check_apply():
    updater = UpdaterService()
    
    # 1. Publicar versión nueva
    pub_res = updater.publish_update(new_version="2.0.0-test", changelog="Prueba de actualización")
    assert pub_res["success"] is True
    assert pub_res["version"] == "2.0.0-test"
    assert updater.version_file.exists()
    assert updater.update_zip_file.exists()

    # 2. Comprobar actualización
    settings.APP_VERSION = "1.0.0" # Simular PC cliente con versión anterior
    check_res = updater.check_for_updates()
    assert check_res["has_update"] is True
    assert check_res["latest_version"] == "2.0.0-test"

    # 3. Aplicar actualización
    apply_res = updater.apply_update()
    assert apply_res["success"] is True
    assert apply_res["new_version"] == "2.0.0-test"
    assert settings.APP_VERSION == "2.0.0-test"

    # Restaurar versión
    settings.APP_VERSION = "1.0.0"

def test_updater_api_endpoints():
    # 1. Publicar vía API
    res_pub = client.post("/api/updates/publish", json={"version": "1.0.9-test", "changelog": "API Test"})
    assert res_pub.status_code == 200
    assert res_pub.json()["success"] is True

    # 2. Comprobar vía API
    settings.APP_VERSION = "1.0.0"
    res_check = client.get("/api/updates/check")
    assert res_check.status_code == 200
    assert res_check.json()["has_update"] is True

    # 3. Aplicar vía API
    res_apply = client.post("/api/updates/apply")
    assert res_apply.status_code == 200
    assert res_apply.json()["success"] is True

    # Restaurar
    settings.APP_VERSION = "1.0.0"
