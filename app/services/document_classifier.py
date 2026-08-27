import re
from typing import List, Dict, Any

def classify_document(pages_data: List[Dict[str, Any]]) -> str:
    """
    Identifica el tipo de documento aduanero según palabras clave y encabezados encontrados.
    """
    if not pages_data:
        return "UNKNOWN"

    first_page_text = pages_data[0]["text"].upper()

    # Sistema SOFIA - Paraguay
    # Indicadores: 'DESPACHO NUMERO:', 'REGIMEN : ZF', 'ADUANA : ZA', 'TRAMITADO DIGITALMENTE', 'FECHA OFIC:', 'IMP./EXP :'
    if "DESPACHO NUMERO:" in first_page_text or ("SISTEMA SOFIA" in first_page_text or "FECHA OFIC:" in first_page_text and "ADUANA :" in first_page_text):
        return "PARAGUAY_SOFIA"

    # DUA / Declaración Única de Aduanas / Mercosur
    if "DECLARACION UNICA DE ADUANAS" in first_page_text or "DUA" in first_page_text:
        return "DUA_CUSTOMS"

    # Bill of Lading / Conocimiento de Embarque
    if "BILL OF LADING" in first_page_text or "OCEAN BILL OF LADING" in first_page_text:
        return "BILL_OF_LADING"

    # Factura Comercial / Commercial Invoice
    if "COMMERCIAL INVOICE" in first_page_text or "FACTURA COMERCIAL" in first_page_text:
        return "COMMERCIAL_INVOICE"

    return "GENERIC_CUSTOMS"
