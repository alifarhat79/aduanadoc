import os
import tempfile
import pymupdf
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.services.pipeline import process_pdf_file
from app.models import Despacho, DespachoItem

def test_pipeline_end_to_end():
    # Base de datos en memoria para el test
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    db = TestingSessionLocal()

    # Generar un PDF sintético con texto aduanero usando PyMuPDF
    doc = pymupdf.open()
    page = doc.new_page()
    pdf_text = """
    DESPACHO NUMERO: 26021ZF2I000919N
    FECHA OFIC: 10/08/2026 17:13:10
    ADUANA : ZA FRCA GLOBAL
    REGIMEN : ZF2I - IMPORTACION
    IMP./EXP : FLORACE S.A
    RUC/DOC : 800409361
    VEND/COMP. IMPORTADORA AMERICAS S.A
    DESP. GONZALEZ LOPEZ ALEJANDRO SELESTINO
    VALOR FOB USD 668.385,25
    FLETE : 3.000,00 DOL
    SEGURO FACTURA 1.103,50 DOL
    VALOR CIF : 672.488,75
    KILO NETO 19.196,000
    CANAL ASIGNADO CANAL VERDE
    TOTAL GENERAL.. 573.559.833
    """
    page.insert_text((50, 50), pdf_text, fontsize=10)

    # Página 2 con ítems
    page2 = doc.new_page()
    items_text = """
    NRO ITEM: 1 POS.ARANC.:3303.00.10.000L SUB ITEM NRO: 1
    CANTIDAD: 480.00 CANT.ESPEC.: 0 FOB: 9,460.80
    MARCA: ARMAF OBSERVACION: PERFUME DE PRUEBA
    """
    page2.insert_text((50, 50), items_text, fontsize=10)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp_path = tmp.name
        tmp.close()

    doc.save(tmp_path)
    doc.close()

    try:
        despacho, log = process_pdf_file(
            db=db,
            file_path=tmp_path,
            original_filename="test_despacho.pdf",
            propietario="Empresa Cliente S.A."
        )

        assert despacho.id is not None
        assert despacho.propietario == "Empresa Cliente S.A."
        assert despacho.numero_despacho == "26021ZF2I000919N"
        assert despacho.importador_nombre == "FLORACE S.A"
        assert despacho.importador_documento == "800409361"
        assert despacho.valor_fob == 668385.25
        assert despacho.valor_flete == 3000.0
        assert despacho.valor_seguro == 1103.50
        assert despacho.canal == "VERDE"
        assert len(despacho.items) == 1
        assert despacho.items[0].codigo_ncm == "3303.00.10.000L"
        assert despacho.items[0].marca == "ARMAF"

        # Verificar log
        assert log.campos_identificados > 5
        assert log.confianza_promedio > 0.80
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        db.close()
