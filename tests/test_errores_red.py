"""
Cortes de internet: mensajes claros, reintentos seguros y, sobre todo, que un corte NUNCA provoque
subir todos los despachos a Turso pisando los datos de otras PCs. No se conecta a Turso ni a Google.
"""
import asyncio
import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.errores_red import describir_error, es_error_de_conexion
from app.services.turso_service import TursoService


def test_mensajes_nunca_vacios():
    assert describir_error(TimeoutError()) == "tiempo de espera agotado"
    assert describir_error(httpx.ConnectError("All connection attempts failed")) == \
        "ConnectError: All connection attempts failed"
    assert describir_error(ValueError()) == "ValueError"
    assert es_error_de_conexion(TimeoutError("_ssl.c:1063: The handshake operation timed out"))
    assert es_error_de_conexion(httpx.ConnectError("x"))
    assert not es_error_de_conexion(ValueError("x"))
    httplib2 = pytest.importorskip("httplib2")
    assert es_error_de_conexion(httplib2.ServerNotFoundError("sin DNS"))


def _turso():
    return TursoService(db_url="libsql://prueba.turso.io", auth_token="token-de-prueba")


def test_si_no_se_puede_leer_turso_no_se_sube_nada():
    """Antes: lista vacía -> todos los despachos 'faltaban' -> se subían todos pisando la nube."""
    from app.database import SessionLocal
    t = _turso()
    db = SessionLocal()
    try:
        with patch.object(TursoService, "init_turso_schema", new=AsyncMock()), \
             patch.object(TursoService, "execute_raw", new=AsyncMock(side_effect=httpx.ConnectError("All connection attempts failed"))), \
             patch.object(TursoService, "push_despacho_to_turso", new=AsyncMock()) as subir:
            res = asyncio.run(t.push_delta_to_turso(db))
    finally:
        db.close()
    assert res["success"] is False and "ConnectError" in res["error"]
    subir.assert_not_called()


class _Resp:
    status_code = 200

    def json(self):
        return {"results": []}


def test_reintenta_si_la_conexion_no_se_pudo_abrir():
    llamadas = []

    async def post(self, *a, **kw):
        llamadas.append(1)
        if len(llamadas) < 3:
            raise httpx.ConnectError("All connection attempts failed")
        return _Resp()

    with patch("httpx.AsyncClient.post", new=post), patch("app.services.turso_service.asyncio.sleep", new=AsyncMock()):
        assert asyncio.run(_turso().execute_raw([{"sql": "SELECT 1"}])) == {"results": []}
    assert len(llamadas) == 3


def test_no_reintenta_si_la_peticion_ya_se_envio():
    """Un tiempo agotado DESPUÉS de enviar no se reintenta: la escritura pudo haberse aplicado."""
    llamadas = []

    async def post(self, *a, **kw):
        llamadas.append(1)
        raise httpx.ReadTimeout("sin respuesta")

    with patch("httpx.AsyncClient.post", new=post), patch("app.services.turso_service.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(httpx.ReadTimeout):
            asyncio.run(_turso().execute_raw([{"sql": "INSERT ..."}]))
    assert len(llamadas) == 1


def test_sync_de_arranque_reintenta_ante_un_corte():
    from app.services import secrets_sync
    subidas = [{"success": False, "error": "No se pudo consultar Turso: ConnectError: x"},
               {"success": True, "despachos_subidos": 2}]
    with patch.object(TursoService, "is_configured", return_value=True), \
         patch.object(TursoService, "push_delta_to_turso", new=AsyncMock(side_effect=subidas)), \
         patch.object(TursoService, "pull_despachos_quick", new=AsyncMock(return_value={"despachos_nuevos": 1})), \
         patch.object(secrets_sync.time, "sleep") as esperar:
        res = secrets_sync._sincronizar_turso()
    assert res == {"subidos": 2, "bajados": 1}
    esperar.assert_called_once_with(15)


def test_vigilante_avisa_sin_conexion_en_lugar_de_error(caplog):
    from app.services.gdrive_watcher import GDriveWatcher
    w = GDriveWatcher()
    w.is_running = True

    async def dormir(segundos):
        if segundos != 1:  # tras el primer ciclo se detiene
            w.is_running = False

    async def correr():
        with patch("app.services.gdrive_watcher.asyncio.sleep", new=dormir), \
             patch.object(w, "_perform_scan", new=AsyncMock(side_effect=TimeoutError("_ssl.c:1063: The handshake operation timed out"))):
            await w._watch_loop()

    with caplog.at_level(logging.WARNING, logger="app.services.gdrive_watcher"):
        asyncio.run(correr())
    mensajes = [r.getMessage() for r in caplog.records]
    assert any("Sin conexión con Google Drive" in m and "se reintentará" in m for m in mensajes)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_check_new_sin_mensaje_vacio():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services.gdrive_service import GoogleDriveService
    with patch.object(GoogleDriveService, "is_api_available", return_value=True), \
         patch.object(GoogleDriveService, "detect_new_files", side_effect=TimeoutError()):
        r = TestClient(app).get("/api/sync/check-new", params={"drive": "true"})
    assert r.status_code == 200 and r.json()["error"]
