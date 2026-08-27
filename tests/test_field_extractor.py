from datetime import date
from app.services.field_extractor import extract_all_fields
from app.services.table_extractor import extract_items_from_pages

# Texto extraído real de la muestra SOFIA Paraguay
SAMPLE_PAGE_1 = """
DESPACHO NUMERO: 26021ZF2I000919N HOJA 1 de 15
REGIMEN : ZF2I - IMPORTACION DE ZONA FRANCA
ADUANA : ZA FRCA GLOBAL FECHA OFIC: 10/08/2026 17:13:10
USUARIO: SUPERMERCADO LETICIA S.A. - 80040936-1 ** TRAMITADO DIGITALMENTE ** Fecha Imp. : 26/08/2026 12:03:33 Estado: CANC
IMP./EXP : FLORACE S.A AJUSTE A INCLUIR AJUSTE A DEDUCIR
0,00 0,00
RUC/DOC : 800409361 DEPENDENCIA : NO POSEE VALOR FACTURA DIVISA FACTURA
668.385,25 DOL
DIRECCION: AVENIDA, ITA YBATE C/AV. ADRIAN JARA SHOPPING
CATARATAS 1ER.PISO NRO. 103
FLETE : DIVISA FLETE :
3.000,00 DOL
DESP. GONZALEZ LOPEZ ALEJANDRO SELESTINO RUC: 8721530 SEGURO FACTURA DIVISA SEGURO
1.103,50 DOL
COND. VENTA CAMBIO FACTURA
VEND/COMP. IMPORTADORA AMERICAS S.A FOB 5.952,44
VALOR IMPONIBLE CAMBIO USS
4.002.948.937 5.952,44
KILO NETO 19.196,000
TOTAL GENERAL.. 573.559.833 0 0
CANAL ASIGNADO
CANAL VERDE
"""

SAMPLE_PAGE_2 = """
NRO ITEM: 1 POS.ARANC.:3303.00.10.000L SUB ITEM NRO: 1
CANTIDAD: 480.00 CANT.ESPEC.: 0 FOB: 9,460.80
MARCA: ARMAF OBSERVACION: 6295199818459 L-ARMAF ODYSSEY PINK POP 3.4 EDP SPR
SUBFIJO: AA(ARMAF)-SA01-SB01-SC02-ZA(000100)-ZB(000000)

NRO ITEM: 1 POS.ARANC.:3303.00.10.000L SUB ITEM NRO: 2
CANTIDAD: 480.00 CANT.ESPEC.: 0 FOB: 9,460.80
MARCA: ARMAF OBSERVACION: 6295199818862 M-ARMAF ODYSSEY LICHI LUSH 3.4 EDP SPR
"""

def test_field_extraction():
    pages_data = [
        {"page_num": 1, "text": SAMPLE_PAGE_1, "method": "DIGITAL_TEXT"},
        {"page_num": 2, "text": SAMPLE_PAGE_2, "method": "DIGITAL_TEXT"}
    ]

    fields = extract_all_fields(pages_data)

    assert fields["numero_despacho"] == "26021ZF2I000919N"
    assert fields["fecha_despacho"] == date(2026, 8, 10)
    assert fields["importador_nombre"] == "FLORACE S.A."
    assert fields["importador_documento"] == "800409361"
    assert fields["despachante_nombre"] == "GONZALEZ LOPEZ ALEJANDRO SELESTINO"
    assert fields["exportador_nombre"] == "IMPORTADORA AMERICAS S.A"
    assert fields["canal"] == "VERDE"
    assert fields["valor_flete"] == 3000.0
    assert fields["valor_seguro"] == 1103.50
    assert fields["moneda"] == "USD"
    assert fields["tipo_cambio"] == 5952.44
    assert fields["peso_neto"] == 19196.0
    assert fields["total_general"] == 573559833.0
    assert fields["confianza_promedio"] >= 0.85

def test_items_extraction():
    pages_data = [
        {"page_num": 2, "text": SAMPLE_PAGE_2, "method": "DIGITAL_TEXT"}
    ]

    items = extract_items_from_pages(pages_data)
    assert len(items) == 2
    assert items[0]["numero_item"] == 1
    assert items[0]["numero_subitem"] == 1
    assert items[0]["codigo_ncm"] == "3303.00.10.000L"
    assert items[0]["codigo_producto"] == "6295199818459"
    assert items[0]["marca"] == "ARMAF"
    assert items[0]["descripcion"] == "ODYSSEY PINK POP 3.4 EDP SPR"
    assert items[0]["cantidad"] == 480.0
    assert items[0]["valor_total"] == 9460.80

def test_trailing_ean_codes_separation():
    from app.services.table_extractor import parse_observation_details

    casos = [
        ("ODYSSEY TYRANT (M)EDP SP 3.4OZ 6294015160734", "", "6294015160734", "ODYSSEY TYRANT (M)EDP SP 3.4OZ"),
        ("POUR ELLE(W)EDP SP 3.38OZ 3614274347562", "", "3614274347562", "POUR ELLE(W)EDP SP 3.38OZ"),
        ("CURIOUS(W)EDP SP 3.3OZ BY BRITNEY SPEARS 719346034425", "", "719346034425", "CURIOUS(W)EDP SP 3.3OZ BY BRITNEY SPEARS"),
        ("212 HEROES FOREVER YOUNG(W)EDP SP 1.7OZ 8411061994702", "", "8411061994702", "212 HEROES FOREVER YOUNG(W)EDP SP 1.7OZ"),
        ("ACQUA DI GIO PROFONDO(M)PARFUM SP 3.3OZ 3614273953696", "", "3614273953696", "ACQUA DI GIO PROFONDO(M)PARFUM SP 3.3OZ"),
        ("CABOTINE TURQUOISE(W*)EDP SP 3.4OZ 7640473481116", "", "7640473481116", "CABOTINE TURQUOISE(W*)EDP SP 3.4OZ"),
        ("6295199818459 L-ARMAF ODYSSEY PINK POP 3.4 EDP SPR", "ARMAF", "6295199818459", "ODYSSEY PINK POP 3.4 EDP SPR"),
        ("I0095135 ARMAF ODYSSEY TYRANT (M)EDP SP 3.4OZ 6294015160734", "ARMAF", "6294015160734", "ODYSSEY TYRANT (M)EDP SP 3.4OZ"),
        ("THE VITA-A RETINOL SHOT TIGHTENING SERUM 30ML COD: ITEM NRO.1", "", "", "THE VITA-A RETINOL SHOT TIGHTENING SERUM 30ML"),
        ("THE VITA-A RETINAL SHOT TIGHTENING BOOSTER 15ML COD: ITEM NRO.2", "", "", "THE VITA-A RETINAL SHOT TIGHTENING BOOSTER 15ML"),
        ("RETINOL 0.3% + NIACIN RENEWING SERUM 30ML COD: ITEM NRO.5", "", "", "RETINOL 0.3% + NIACIN RENEWING SERUM 30ML"),
        ("PEACH 70 NIACIN SERUM 30ML COD: ITEM NRO.10", "", "", "PEACH 70 NIACIN SERUM 30ML")
    ]

    for raw, marca, exp_cod, exp_desc in casos:
        cod, desc = parse_observation_details(raw, marca)
        assert cod == exp_cod, f"Fallo en código para '{raw}': esperado '{exp_cod}', obtenido '{cod}'"
        assert desc == exp_desc, f"Fallo en descripción para '{raw}': esperado '{exp_desc}', obtenido '{desc}'"

