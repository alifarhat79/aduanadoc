"""
Pruebas de los bugs corregidos después del módulo Arancel (base temporal de conftest, nunca la real):
1. La limpieza de descripciones perdía el modelo del producto (IPHONE15, SM58, SKIN1004).
2. El asistente de calidad reemplazaba EAN válidos por una referencia de la descripción.
3. Proveedores extranjeros se unificaban con clientes ("MEGA ELECTRONICS FZE" -> "MEGA").
4. Planillas de clientes: "1.234.567" se leía como 1.234.
5. Un PDF que fallaba se volvía a descargar y procesar cada 60 segundos para siempre.
6. El servidor y el vigilante en segundo plano podían importar el mismo PDF a la vez.
7. El escaneo de carpeta local ignoraba el dueño elegido.
8. Solo se guardaban los últimos 3 respaldos.
"""
import subprocess
import sys
import textwrap
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.database import SessionLocal


# --------------------------------------------------------------------------------------
# 1. Limpieza de descripciones
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, esperado_cod, esperado_desc", [
    ("IPHONE15 PRO MAX 256GB - REF. A2849", "A2849", "IPHONE15 PRO MAX 256GB"),
    ("SM58 MICROFONO DINAMICO 042406051636", "042406051636", "SM58 MICROFONO DINAMICO"),
    ("SKIN1004 MADAGASCAR CENTELLA CREAM 75ML 8809576261646", "8809576261646", "SKIN1004 MADAGASCAR CENTELLA CREAM 75ML"),
    ("CER-100 HAIR MUSCLE ESSENCE OIL 100ML 8809543253506", "8809543253506", "CER-100 HAIR MUSCLE ESSENCE OIL 100ML"),
    ("100ML PERFUME DE PRUEBA", "", "100ML PERFUME DE PRUEBA"),
    # Un código interno evidente se sigue separando (con o sin EAN)
    ("I0095135 PERFUME DE PRUEBA", "I0095135", "PERFUME DE PRUEBA"),
    ("I0095135 ARMAF ODYSSEY TYRANT (M)EDP SP 3.4OZ 6294015160734", "6294015160734", "ARMAF ODYSSEY TYRANT (M)EDP SP 3.4OZ"),
])
def test_limpieza_no_pierde_el_modelo(raw, esperado_cod, esperado_desc):
    from app.services.table_extractor import parse_observation_details
    cod, desc = parse_observation_details(raw, "ARMAF" if "ARMAF" in raw else "")
    assert (cod, desc) == (esperado_cod, esperado_desc)


# --------------------------------------------------------------------------------------
# 2. Asistente de calidad: nunca reemplaza un código existente
# --------------------------------------------------------------------------------------

def test_asistente_no_reemplaza_ean_existente():
    from app.models import Despacho, DespachoItem
    from app.services.item_auditor import batch_apply_high_confidence_fixes, diagnose_item

    diag = diagnose_item("PERFUME YARA 100ML REF. ABC123", "LATTAFA", "6291108735411")
    assert diag["suggested_codigo"] == "6291108735411"
    assert diag["confidence"] != "high"
    assert any("ABC123" in i for i in diag["issues"])

    # Sin código previo, la referencia sí se completa
    assert diagnose_item("PERFUME YARA 100ML REF. ABC123", "LATTAFA", None)["suggested_codigo"] == "ABC123"

    db = SessionLocal()
    try:
        d = Despacho(numero_despacho="BUGS-EAN-0001", archivo_pdf="x.pdf", nombre_archivo_original="x.pdf",
                     hash_archivo="hash_bugs_ean_0001", estado_procesamiento="PROCESADO")
        db.add(d)
        db.flush()
        it = DespachoItem(despacho_id=d.id, numero_item=1, descripcion="PERFUME YARA 100ML REF. ABC123",
                          marca="LATTAFA", codigo_producto="6291108735411")
        db.add(it)
        db.commit()
        item_id = it.id
        batch_apply_high_confidence_fixes(db)
        db.expire_all()
        it = db.query(DespachoItem).filter(DespachoItem.id == item_id).one()
        assert it.codigo_producto == "6291108735411"
        assert it.descripcion == "PERFUME YARA 100ML REF. ABC123"
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# 3. Nombres de empresas
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("nombre, esperado", [
    ("MEGA ELECTRONICS FZE", "MEGA ELECTRONICS FZE"),
    ("GAFA TRADING LLC", "GAFA TRADING LLC"),
    ("ERAS INTERNATIONAL CO", "ERAS INTERNATIONAL CO"),
    ("SERENA PERFUMES L.L.C", "SERENA PERFUMES L.L.C"),
    # Variantes de los propios clientes se siguen unificando (como en los despachos existentes)
    ("ERAS COMERCIAL SRL", "ERAS S.R.L."),
    ("MEGA DISTRIBUIDORA", "MEGA"),
    ("AGATRES S.A.", "AGATRES"),
    ('"HT" S.A.', "H.T. S.A."),
    ("Florace s.a", "FLORACE S.A."),
])
def test_proveedores_extranjeros_no_se_unifican_con_clientes(nombre, esperado):
    from app.services.normalizer import normalize_company_name
    assert normalize_company_name(nombre) == esperado


# --------------------------------------------------------------------------------------
# 4. Números de planillas de clientes
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("texto, esperado", [
    ("1.234.567", 1234567.0),
    ("$ 2.500.000", 2500000.0),
    ("1.234.567,89", 1234567.89),
    ("1,234,567.89", 1234567.89),
    ("1,5", 1.5),
    ("1.5", 1.5),
    ("12,345", 12345.0),
])
def test_planilla_numeros_con_separador_de_miles(texto, esperado):
    from app.services.client_sheet_importer import parse_numeric_value
    assert parse_numeric_value(texto) == esperado


# --------------------------------------------------------------------------------------
# 5 y 6. Vigilante de Google Drive
# --------------------------------------------------------------------------------------

class _DescargaFalsa:
    def __init__(self, fh, request):
        self.fh = fh

    def next_chunk(self):
        self.fh.write(b"%PDF-1.4 archivo de prueba que no es un despacho")
        return None, True


def _drive_falso(archivos):
    ds = MagicMock()
    ds.files.return_value.list.return_value.execute.return_value = {"files": archivos}
    return ds


@pytest.fixture
def sin_fallos_previos():
    from app.services.gdrive_service import GoogleDriveService
    GoogleDriveService.failed_files.clear()
    yield
    GoogleDriveService.failed_files.clear()


def test_pdf_que_falla_no_se_reintenta_en_cada_ciclo(sin_fallos_previos):
    from app.services.gdrive_service import GoogleDriveService
    svc = GoogleDriveService()
    archivo = {"id": "1", "name": "roto_bugs.pdf", "md5Checksum": "aaa"}

    def escanear(**kw):
        db = SessionLocal()
        try:
            return svc.scan_and_process_folder(db=db, **kw)
        finally:
            db.close()

    with patch.object(GoogleDriveService, "get_drive_service", side_effect=lambda: _drive_falso([dict(archivo)])), \
         patch("googleapiclient.http.MediaIoBaseDownload", _DescargaFalsa), \
         patch("app.services.gdrive_service.process_pdf_file", side_effect=ValueError("PDF ilegible")) as proc:
        r1 = escanear()
        assert r1["errores"] == 1 and proc.call_count == 1
        assert GoogleDriveService.failed_files["roto_bugs.pdf"]["intentos"] == 1

        # Ciclo siguiente: no se vuelve a descargar ni procesar
        r2 = escanear()
        assert proc.call_count == 1
        assert r2["detalles"][0]["estado"] == "OMITIDO_ERROR_PREVIO"

        # Si el archivo cambia en Drive, se reintenta enseguida
        archivo["md5Checksum"] = "bbb"
        escanear()
        assert proc.call_count == 2

        # La sincronización manual siempre reintenta
        escanear(reintentar_fallidos=True)
        assert proc.call_count == 3

    # Detección rápida (modal de importación) sigue excluyéndolo
    with patch.object(GoogleDriveService, "get_drive_service", side_effect=lambda: _drive_falso([dict(archivo)])):
        db = SessionLocal()
        try:
            assert svc.detect_new_files(db) == []
        finally:
            db.close()


def test_otro_proceso_importando_no_se_procesa_en_paralelo(sin_fallos_previos):
    """Un segundo PROCESO tiene el candado: el escaneo automático no toca Drive y avisa."""
    from app.services.candado_procesos import ruta_candado
    from app.services.gdrive_service import GoogleDriveService

    ruta = str(ruta_candado("escaneo_pdfs"))
    codigo = textwrap.dedent(f"""
        import os, sys
        f = open({ruta!r}, "a+b")
        if os.name == "nt":
            import msvcrt; f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl; fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        print("bloqueado", flush=True)
        sys.stdin.readline()
    """)
    otro = subprocess.Popen([sys.executable, "-c", codigo], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert otro.stdout.readline().strip() == "bloqueado"
        svc = GoogleDriveService()
        with patch.object(GoogleDriveService, "get_drive_service") as drive:
            db = SessionLocal()
            try:
                r = svc.scan_and_process_folder(db=db)                     # automático: no espera
                r_local = svc.scan_local_folder(db=db, local_path=".", espera_candado=0.5)
            finally:
                db.close()
            assert "Otro proceso" in r["aviso"] and r["nuevos_procesados"] == 0
            assert "Otro proceso" in r_local["aviso"]
            drive.assert_not_called()
    finally:
        otro.stdin.write("fin\n")
        otro.stdin.flush()
        otro.wait(timeout=10)

    # Liberado el candado, el escaneo vuelve a funcionar
    with patch.object(GoogleDriveService, "get_drive_service", side_effect=lambda: _drive_falso([])) as drive:
        db = SessionLocal()
        try:
            r = GoogleDriveService().scan_and_process_folder(db=db)
        finally:
            db.close()
        assert "aviso" not in r and drive.call_count == 1


def test_carpeta_local_respeta_el_dueno_elegido(tmp_path):
    from app.services.gdrive_service import GoogleDriveService
    (tmp_path / "nuevo_bugs.pdf").write_bytes(b"%PDF-1.4 x")
    falso = SimpleNamespace(id=999999, numero_despacho="BUGS-DUENO", importador_nombre="X", propietario="Cliente Elegido",
                            canal=None, valor_fob=0, valor_cif=0, items=[], fecha_despacho=date(2026, 1, 1))
    with patch("app.services.gdrive_service.process_pdf_file", return_value=(falso, None)) as proc, \
         patch("app.services.notification_service.NotificationService.notify_new_despacho"), \
         patch("app.services.backup_service.BackupService.create_system_backup", return_value={}):
        db = SessionLocal()
        try:
            GoogleDriveService().scan_local_folder(db=db, local_path=str(tmp_path), propietario="Cliente Elegido")
        finally:
            db.close()
    assert proc.call_args.kwargs["propietario"] == "Cliente Elegido"


# --------------------------------------------------------------------------------------
# 8. Respaldos
# --------------------------------------------------------------------------------------

def test_se_guardan_los_ultimos_20_respaldos():
    from app.config import Settings
    assert Settings.model_fields["BACKUP_MAX_KEEP"].default == 20
