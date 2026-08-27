from datetime import date
from app.services.normalizer import parse_date, parse_currency, normalize_document, clean_text, normalize_company_name

def test_parse_date():
    assert parse_date("10/08/2026 17:13:10") == date(2026, 8, 10)
    assert parse_date("10/08/2026") == date(2026, 8, 10)
    assert parse_date("10-08-2026") == date(2026, 8, 10)
    assert parse_date("2026-08-10") == date(2026, 8, 10)
    assert parse_date("10-AUG-26") == date(2026, 8, 10)
    assert parse_date("10-AGO-2026") == date(2026, 8, 10)
    assert parse_date(None) is None
    assert parse_date("invalido") is None

def test_parse_currency():
    # Formatos con coma decimal
    assert parse_currency("1.234,56") == 1234.56
    assert parse_currency("668.385,25") == 668385.25
    assert parse_currency("3.000,00") == 3000.0
    assert parse_currency("1.103,50") == 1103.50
    assert parse_currency("19.196,000") == 19196.0

    # Formatos con punto decimal
    assert parse_currency("1,234.56") == 1234.56
    assert parse_currency("USD 12,500.00") == 12500.00
    assert parse_currency("9,460.80") == 9460.80

    # Guaraníes / números enteros con puntos de miles
    assert parse_currency("4.002.948.937") == 4002948937.0
    assert parse_currency("573.559.833") == 573559833.0

    # Nulos y vacíos
    assert parse_currency(None) is None
    assert parse_currency("") is None

def test_normalize_document():
    assert normalize_document("RUC: 80040936-1") == "80040936-1"
    assert normalize_document("RUC/DOC : 800409361") == "800409361"
    assert normalize_document("CNPJ : 12.345.678/0001-90") == "12.345.678/0001-90"
    assert normalize_document(None) is None

def test_clean_text():
    assert clean_text("  FLORACE   S.A  ") == "FLORACE S.A"
    assert clean_text("VALOR ********** IMPONIBLE") == "VALOR IMPONIBLE"
    assert clean_text(None) is None

def test_normalize_company_name():
    assert normalize_company_name("Florace s.a") == "FLORACE S.A."
    assert normalize_company_name("FLORACE") == "FLORACE S.A."
    assert normalize_company_name("Gafa s.a") == "GAFA S.A."
    assert normalize_company_name("GAFA") == "GAFA S.A."
    assert normalize_company_name("H.T s.a") == "H.T. S.A."
    assert normalize_company_name("HT S.A") == "H.T. S.A."
    assert normalize_company_name("Eras s.r.l") == "ERAS S.R.L."
    assert normalize_company_name(None) is None
