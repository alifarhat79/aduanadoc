"""
Pruebas de regresión de las correcciones de la revisión de noviembre 2026.
Todas corren sobre el entorno aislado de conftest.py (base y carpetas temporales).
"""
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.config import settings
from app.database import Base
from app.models import Despacho
from app.services.table_extractor import extract_items_from_pages
from app.services.normalizer import parse_currency

client = TestClient(app)


# ---------------------------------------------------------------------------------------
# 1. Extracción: despacho que MEZCLA ítems con subítems e ítems solo en grilla
#    (estructura real de 26021ZF2I001208F: FOB declarado 62.083,60)
# ---------------------------------------------------------------------------------------
PAGINA_MIXTA = "\n".join([
    "SUBITEMS/ SUFIJOS",
    " NRO ITEM: 1 POS.ARANC.:9503.00.10.000B SUB ITEM NRO: 1",
    " CANTIDAD: 1,000.00   CANT.ESPEC.: 0   FOB: 8,000.00",
    " MARCA: SM  OBSERVACION: SCOOTER 200B (PATINETE INFANTIL)",
    " SUBFIJO:",
    " NRO ITEM: 1 POS.ARANC.:9503.00.10.000B SUB ITEM NRO: 2",
    " CANTIDAD: 2,937.00   CANT.ESPEC.: 0   FOB: 16,153.60",
    " MARCA: SM  OBSERVACION: SKATE BOARD KIDS MIX COLOR 0130-HG",
    " SUBFIJO:",
    "OBSERVACION VISTA",
    "NRO DE PEDIDO",
    "2/3",
    "N",
    "8543.70.99.000W",
    "2",
    "FOB FACTURA",
    "9.000,00",
    "DESCRIPCION EN TERMINOS COMERCIALES",
    "MARCA MODELO Y",
    "VERSION",
    "CANTIDAD",
    "UNIDAD",
    "FOB U$S",
    "MAQUINA DE HUMO PORTATIL",
    "SM",
    "300,000",
    "UNIDAD",
    "3.750,00",
    "AJUSTE (GASTOS)",
    "NRO DE PEDIDO",
    "3/3",
    "N",
    "2404.12.00.000F",
    "12.6",
    "FOB FACTURA",
    "28.930,00",
    "DESCRIPCION EN TERMINOS COMERCIALES",
    "FOB U$S",
    "ESENCIA LIFE POD 18ML",
    "LIFEPOD",
    "131.500,000",
    "UNIDAD",
    "28.930,00",
    "AJUSTE (GASTOS)",
])


def test_extractor_formato_mixto_no_pierde_items_sin_subitems():
    items = extract_items_from_pages([{"page_num": 1, "text": PAGINA_MIXTA}])
    claves = sorted((it["numero_item"], it["numero_subitem"]) for it in items)
    assert claves == [(1, 1), (1, 2), (2, None), (3, None)]
    # La suma debe cuadrar con el FOB declarado del despacho (antes solo 24.153,60)
    assert round(sum(it["valor_total"] for it in items), 2) == 62083.60
    item2 = next(it for it in items if it["numero_item"] == 2)
    assert item2["valor_total"] == 9000.0  # FOB FACTURA (total del ítem), no FOB U$S de la 1ª línea


def test_extractor_fecha_no_crea_item_fantasma():
    texto = "\n".join(["10/08/2026 17:13:10", "573.559.833", "FOB FACTURA", "1.000,00"])
    items = extract_items_from_pages([{"page_num": 1, "text": texto}])
    assert not any(it["numero_item"] == 10 for it in items)


def test_extractor_kilo_neto_al_final_del_bloque_no_rompe():
    texto = "\n".join(["1/1", "N", "3303.00.10.000L", "FOB FACTURA", "500,00", "KILO NETO", "PARAGUAY", "EMIRATOS ARABES"])
    items = extract_items_from_pages([{"page_num": 1, "text": texto}])  # antes: IndexError
    assert items and items[0]["valor_total"] == 500.0


def test_parse_currency_con_prefijo_guaranies():
    assert parse_currency("Gs. 75.000.000") == 75000000.0
    assert parse_currency("668.385,25") == 668385.25  # sin cambios en el formato habitual


# ---------------------------------------------------------------------------------------
# 2. Carga manual: un duplicado se informa como DUPLICADO (no como "procesado")
# ---------------------------------------------------------------------------------------
def _pdf_minimo(texto: str) -> bytes:
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), texto)
    data = doc.tobytes()
    doc.close()
    return data


def test_upload_duplicado_se_informa_como_duplicado():
    contenido = _pdf_minimo("DESPACHO DE PRUEBA DUPLICADO 9F3A")
    files = [("files", ("dup_prueba.pdf", contenido, "application/pdf"))]
    r1 = client.post("/upload/process-batch", files=files).json()["results"][0]
    assert r1["status"] not in ("ERROR", "DUPLICADO"), r1
    with patch("app.services.notification_service.NotificationService.notify_new_despacho") as noti:
        r2 = client.post("/upload/process-batch", files=files).json()["results"][0]
    assert r2["status"] == "DUPLICADO"
    assert r2["despacho_id"] == r1["despacho_id"]
    noti.assert_not_called()  # antes se notificaba por Telegram como despacho NUEVO


# ---------------------------------------------------------------------------------------
# 3. Seguridad
# ---------------------------------------------------------------------------------------
def test_csrf_bloquea_post_desde_otro_sitio():
    r = client.post("/api/backup/crear", headers={"Origin": "https://sitio-malicioso.example"})
    assert r.status_code == 403
    r_null = client.post("/api/updates/git/pull", headers={"Origin": "null"})
    assert r_null.status_code == 403


def test_csrf_permite_mismo_origen():
    r = client.get("/api/updates/git/status", headers={"Origin": "https://sitio-malicioso.example"})
    assert r.status_code == 200  # GET no se bloquea
    r2 = client.post("/api/backup/crear", headers={"Origin": "http://testserver"})
    assert r2.status_code == 200


def test_turso_con_credenciales_propias_exige_programador():
    anon = TestClient(app)
    payload = {"database_url": "libsql://base-ajena.example.io", "auth_token": "x"}
    for ruta in ("/turso/api/test", "/turso/api/push", "/turso/api/pull"):
        assert anon.post(ruta, json=payload).status_code == 401, ruta


def test_telegram_escapa_html_de_datos_del_pdf():
    from app.services.notification_service import NotificationService
    svc = NotificationService(telegram_token="1:x", telegram_chat_id="1")
    with patch.object(NotificationService, "send_telegram_message", return_value={"success": True}) as send:
        svc.notify_new_despacho({
            "numero_despacho": "26021ZF2I000001A",
            "importador_nombre": "PEREZ & CIA <b>",
            "nombre_archivo_original": "<a href='x'>a.pdf",
        })
    texto = send.call_args[0][0]
    assert "PEREZ &amp; CIA &lt;b&gt;" in texto
    assert "<a href" not in texto


# ---------------------------------------------------------------------------------------
# 4. Google Drive: deduplicación por nombre exacto (sin comodines LIKE)
# ---------------------------------------------------------------------------------------
def test_drive_nombre_existente_es_exacto():
    from app.services.gdrive_service import GoogleDriveService
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Despacho(archivo_pdf="./uploads/2026/10/1700000000_abcd1234_DESP-001.pdf",
                    nombre_archivo_original="DESP-001.pdf", hash_archivo="h1"))
    db.add(Despacho(archivo_pdf="./uploads/2026/10/1700000001_abcd1235_11.pdf",
                    nombre_archivo_original="11.pdf", hash_archivo="h2"))
    db.commit()
    buscar = GoogleDriveService._find_existing_by_name
    assert buscar(db, "DESP-001.pdf") is not None
    assert buscar(db, "DESP_001.pdf") is None   # "_" ya no es comodín
    assert buscar(db, "1.pdf") is None          # ya no coincide como sufijo de "11.pdf"
    db.close()


def test_drive_carpeta_local_no_duplica_pdfs_en_windows(tmp_path):
    from app.services.gdrive_service import GoogleDriveService
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 a")
    (tmp_path / "B.PDF").write_bytes(b"%PDF-1.4 b")
    svc = GoogleDriveService()
    with patch("app.services.gdrive_service.process_pdf_file", side_effect=RuntimeError("no procesar")):
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            res = svc.scan_local_folder(db=db, local_path=str(tmp_path))
        finally:
            db.close()
    assert res["total_encontrados"] == 2  # antes 4 en Windows (*.pdf + *.PDF)


# ---------------------------------------------------------------------------------------
# 5. Claves desde Drive: solo se aceptan archivos de un dueño de confianza
# ---------------------------------------------------------------------------------------
class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _FakeDrive:
    def __init__(self, files, folder_owner, content):
        self._files, self._owner, self._content = files, folder_owner, content

    def files(self):
        return self

    def list(self, **kw):
        return _Exec({"files": self._files})

    def get(self, fileId, **kw):
        return _Exec({"owners": [{"emailAddress": self._owner}]})

    def get_media(self, fileId):
        return _Exec(self._content)


@pytest.mark.parametrize("dueno_archivo, estado_esperado, webhook_esperado", [
    ("atacante@gmail.com", "dueno_no_confiable", ""),
    ("dueno@empresa.com", "ok", "https://hooks.example/ok"),
])
def test_claves_drive_solo_de_dueno_confiable(monkeypatch, tmp_path, dueno_archivo, estado_esperado, webhook_esperado):
    from app.services import secrets_sync
    from app.services.gdrive_service import GoogleDriveService

    cred = tmp_path / "sa.json"
    cred.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(secrets_sync, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(secrets_sync, "_is_test_run", lambda: False)
    monkeypatch.setattr(secrets_sync, "_credentials_path", lambda: cred)
    monkeypatch.setenv("WEBHOOK_URL", "")
    monkeypatch.setattr(settings, "WEBHOOK_URL", "")
    monkeypatch.delenv("SECRETS_TRUSTED_OWNERS", raising=False)
    drive = _FakeDrive(
        files=[{"id": "f1", "name": secrets_sync.SECRETS_FILE_NAME, "modifiedTime": "2026-11-25T00:00:00Z",
                "owners": [{"emailAddress": dueno_archivo}]}],
        folder_owner="dueno@empresa.com",
        content=b"WEBHOOK_URL=https://hooks.example/ok\n",
    )
    monkeypatch.setattr(GoogleDriveService, "is_api_available", staticmethod(lambda: True))
    monkeypatch.setattr(GoogleDriveService, "get_drive_service", lambda self: drive)

    res = secrets_sync.sync_secrets_from_drive()
    assert res["estado"] == estado_esperado
    assert os.environ.get("WEBHOOK_URL", "") == webhook_esperado
