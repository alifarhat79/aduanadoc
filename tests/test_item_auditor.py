import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Despacho, DespachoItem
from app.services.item_auditor import (
    diagnose_item,
    audit_catalog_summary,
    apply_single_correction,
    batch_apply_high_confidence_fixes
)

def test_diagnose_item_clean():
    desc = "PERFUME LATTAFA ASAD EDP 100ML SPRAY"
    marca = "LATTAFA"
    diag = diagnose_item(desc, marca, codigo_producto=None)

    assert diag["is_clean"] is True
    assert len(diag["issues"]) == 0
    assert diag["suggested_desc"] == desc

def test_diagnose_item_page_break_cutoff():
    desc = "Subítem 31 - Posición 8517.13.00.000Y"
    marca = "Sin Marca"
    diag = diagnose_item(desc, marca, codigo_producto=None)

    assert diag["needs_pdf"] is True
    assert any("salto" in i.lower() or "corte" in i.lower() for i in diag["issues"])
    assert diag["confidence"] == "cutoff"

def test_diagnose_item_sku_extraction():
    desc = "SMARTPHONE GALAXY S24 ULTRA 512GB - REF. SM-S928B"
    marca = "SAMSUNG"
    diag = diagnose_item(desc, marca, codigo_producto=None)

    assert diag["suggested_codigo"] == "SM-S928B"
    assert "REF." not in diag["suggested_desc"]
    assert "SM-S928B" not in diag["suggested_desc"]
    assert diag["suggested_desc"] == "SMARTPHONE GALAXY S24 ULTRA 512GB"
    assert any("sku" in i.lower() or "código" in i.lower() for i in diag["issues"])

def test_diagnose_item_header_leak():
    desc = "HOJA 4 de 12 FECHA OFIC: 10/08/2026 AURICULARES INALAMBRICOS PRO"
    marca = "XIAOMI"
    diag = diagnose_item(desc, marca, codigo_producto=None)

    assert "HOJA" not in diag["suggested_desc"]
    assert "FECHA OFIC" not in diag["suggested_desc"]
    assert "AURICULARES INALAMBRICOS PRO" in diag["suggested_desc"]
    assert any("encabezado" in i.lower() for i in diag["issues"])

def test_diagnose_item_punctuation_cleanup():
    desc = "TELEVISOR SMART TV 55 PULGADAS 4K UHD - ."
    marca = "LG"
    diag = diagnose_item(desc, marca, codigo_producto=None)

    assert not diag["suggested_desc"].endswith("- .")
    assert diag["suggested_desc"] == "TELEVISOR SMART TV 55 PULGADAS 4K UHD"

def test_apply_single_correction_in_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    despacho = Despacho(
        numero_despacho="26021ZF2I000999A",
        despachante_nombre="TEST DESPACHANTE",
        importador_nombre="TEST IMPORTADOR",
        importador_documento="80000000-1",
        archivo_pdf="uploads/test/dummy_despacho.pdf",
        nombre_archivo_original="dummy_despacho.pdf",
        hash_archivo="hash_dummy_1"
    )
    db.add(despacho)
    db.commit()

    item = DespachoItem(
        despacho_id=despacho.id,
        numero_item=1,
        numero_subitem=1,
        marca="ARMAF",
        descripcion="PERFUME ARMAF CLUB DE NUIT REF. 998811",
        codigo_producto=None
    )
    db.add(item)
    db.commit()

    # Aplicar corrección individual
    res = apply_single_correction(
        db,
        item_id=item.id,
        new_desc="PERFUME ARMAF CLUB DE NUIT",
        new_codigo="998811",
        new_marca="ARMAF"
    )

    assert res["success"] is True
    updated_item = db.query(DespachoItem).filter_by(id=item.id).first()
    assert updated_item.descripcion == "PERFUME ARMAF CLUB DE NUIT"
    assert updated_item.codigo_producto == "998811"

    db.close()

def test_batch_apply_high_confidence_fixes():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    despacho = Despacho(
        numero_despacho="26021ZF2I000999B",
        despachante_nombre="TEST DESPACHANTE",
        importador_nombre="TEST IMPORTADOR",
        importador_documento="80000000-1",
        archivo_pdf="uploads/test/dummy_despacho_2.pdf",
        nombre_archivo_original="dummy_despacho_2.pdf",
        hash_archivo="hash_dummy_2"
    )
    db.add(despacho)
    db.commit()

    item1 = DespachoItem(
        despacho_id=despacho.id,
        numero_item=1,
        numero_subitem=1,
        marca="ARMAF",
        descripcion="PERFUME ARMAF CLUB DE NUIT REF. 12345",
        codigo_producto=None
    )
    item2 = DespachoItem(
        despacho_id=despacho.id,
        numero_item=1,
        numero_subitem=2,
        marca="ARMAF",
        descripcion="PERFUME CLEAN AND VALID 100ML",
        codigo_producto="CLN01"
    )
    db.add_all([item1, item2])
    db.commit()

    summary_before = audit_catalog_summary(db)
    assert summary_before["total_items"] == 2
    assert summary_before["skus_count"] >= 1

    batch_res = batch_apply_high_confidence_fixes(db)
    assert batch_res["items_actualizados"] == 1

    db.refresh(item1)
    db.refresh(item2)

    assert item1.descripcion == "PERFUME ARMAF CLUB DE NUIT"
    assert item1.codigo_producto == "12345"
    assert item2.descripcion == "PERFUME CLEAN AND VALID 100ML"

    summary_after = audit_catalog_summary(db)
    assert summary_after["skus_count"] == 0
    assert summary_after["clean_items"] == 2

    db.close()
