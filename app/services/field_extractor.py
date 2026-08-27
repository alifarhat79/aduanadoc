import re
from typing import Dict, Any, List, Optional
from app.services.normalizer import clean_text, parse_date, parse_currency, normalize_document

# Catálogo ampliable de Alias Multilingües (ES / EN / PT)
FIELD_ALIASES = {
    "numero_despacho": [
        r"DESPACHO\s+NUMERO\s*:\s*([A-Z0-9]+)",
        r"N[UÚ]MERO\s+DE\s+DESPACHO\s*[:.\s]+([A-Z0-9]+)",
        r"N[ºo°]\s*DESPACHO\s*[:.\s]+([A-Z0-9]+)",
        r"DESPACHO\s*[:.\s]+([A-Z0-9]+)",
        r"DECLARACI[OÓ]N\s*N[ºo°]?\s*[:.\s]+([A-Z0-9]+)",
        r"DUA\s*N[ºo°]?\s*[:.\s]+([A-Z0-9]+)",
        r"CUSTOMS\s+DECLARATION\s+NO\.?\s*[:.\s]+([A-Z0-9]+)"
    ],
    "fecha_despacho": [
        r"FECHA\s+OFIC\s*:\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}(?:\s+[0-9]{1,2}:[0-9]{1,2}:[0-9]{1,2})?)",
        r"FECHA\s+DE\s+DESPACHO\s*[:.\s]+([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
        r"FECHA\s+REGISTRO\s*[:.\s]+([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
        r"FECHA\s+OFICIALIZACI[OÓ]N\s*[:.\s]+([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
        r"DATE\s*[:.\s]+([0-9]{1,4}[/-][0-9]{1,2}[/-][0-9]{1,4})"
    ],
    "importador_nombre": [
        r"IMP\./EXP\s*:\s*([^\n\r]+?)(?=\s+AJUSTE|\s+RUC|\s+DEPENDENCIA|\s*[\n\r]|$)",
        r"IMPORTADOR\s*[:.\s]+([^\n\r]+?)(?=\s+RUC|\s+DIR|\s*[\n\r]|$)",
        r"IMPORTER\s*[:.\s]+([^\n\r]+?)(?=\s+TAX|\s+DIR|\s*[\n\r]|$)",
        r"USUARIO\s*:\s*([^\n\r]+?)(?=\s*-\s*\d+|\s*\*\*|\s*[\n\r]|$)"
    ],
    "importador_documento": [
        r"RUC/DOC\s*:\s*([0-9A-Za-z-]+)",
        r"RUC\s*:\s*([0-9A-Za-z-]+)",
        r"CNPJ\s*:\s*([0-9A-Za-z./-]+)",
        r"TAX\s+ID\s*:\s*([0-9A-Za-z-]+)"
    ],
    "importador_direccion": [
        r"DIRECCION\s*:\s*([^\n\r]+?)(?=\s+FLETE|\s+DESP|\s*[\n\r]|$)",
        r"DIRECCI[OÓ]N\s*:\s*([^\n\r]+?)(?=\s+FLETE|\s+DESP|\s*[\n\r]|$)",
        r"ADDRESS\s*:\s*([^\n\r]+?)(?=\s+PHONE|\s+CITY|\s*[\n\r]|$)"
    ],
    "despachante_nombre": [
        r"DESP\.\s+([^\n\r]+?)(?=\s+RUC|\s*[\n\r]|$)",
        r"DESPACHANTE\s*[:.\s]+([^\n\r]+?)(?=\s+RUC|\s*[\n\r]|$)",
        r"CUSTOMS\s+BROKER\s*[:.\s]+([^\n\r]+?)(?=\s*[\n\r]|$)"
    ],
    "exportador_nombre": [
        r"VEND/COMP\.\s*([^\n\r]+?)(?=\s+FOB|\s+COND|\s*[\n\r]|$)",
        r"EXPORTADOR\s*[:.\s]+([^\n\r]+?)(?=\s+PAIS|\s*[\n\r]|$)",
        r"EXPORTER\s*[:.\s]+([^\n\r]+?)(?=\s+COUNTRY|\s*[\n\r]|$)",
        r"PROVEEDOR\s*[:.\s]+([^\n\r]+?)(?=\s+PAIS|\s*[\n\r]|$)",
        r"SUPPLIER\s*[:.\s]+([^\n\r]+?)(?=\s+COUNTRY|\s*[\n\r]|$)"
    ],
    "exportador_pais": [
        r"PAIS\s+ORIGEN/DEPARTAMENTO\s+([A-Z\s]+?)(?=\s+PAIS|\s+ESTADOS|\s*[\n\r]|$)",
        r"PA[IÍ]S\s+DE\s+ORIGEN\s*[:.\s]+([A-Za-z\s]+?)(?=\s*[\n\r]|$)",
        r"COUNTRY\s+OF\s+ORIGIN\s*[:.\s]+([A-Za-z\s]+?)(?=\s*[\n\r]|$)"
    ],
    "aduana": [
        r"ADUANA\s*:\s*([^\n\r]+?)(?=\s+FECHA|\s*[\n\r]|$)",
        r"CUSTOMS\s+OFFICE\s*:\s*([^\n\r]+?)(?=\s*[\n\r]|$)"
    ],
    "regimen": [
        r"REGIMEN\s*:\s*([^\n\r]+?)(?=\s+ADUANA|\s*[\n\r]|$)",
        r"R[EÉ]GIMEN\s*:\s*([^\n\r]+?)(?=\s+ADUANA|\s*[\n\r]|$)"
    ],
    "canal": [
        r"CANAL\s+ASIGNADO[\s\n\r]*CANAL\s+([A-Z]+)",
        r"CANAL\s*[:.\s]+(VERDE|NARANJA|ROJO|GREEN|YELLOW|RED)",
        r"CHANNEL\s*[:.\s]+(GREEN|YELLOW|RED)"
    ],
    "valor_fob": [
        r"VALOR\s+FOB\s+(?:USD|DOL|USS)?\s*([0-9.,]+)",
        r"VALOR\s+FACTURA(?:[^\n\r0-9]+)?[\n\r\s]*([0-9.,]+)",
        r"FOB\s+FACTURA[\s\n\r]*([0-9.,]+)",
        r"FOB\s*[:.\s]+(?:USD|DOL|USS)?\s*([0-9.,]+)",
        r"VALOR\s+F\.?O\.?B\.?\s*[:.\s]+([0-9.,]+)"
    ],
    "valor_flete": [
        r"FLETE\s*:(?:[^\n\r0-9]+)?[\s\n\r]*([0-9.,]+)",
        r"VALOR\s+FLETE\s*[:.\s]+([0-9.,]+)",
        r"FREIGHT\s*[:.\s]+([0-9.,]+)"
    ],
    "valor_seguro": [
        r"SEGURO\s+FACTURA(?:[^\n\r0-9]+)?[\s\n\r]*([0-9.,]+)",
        r"SEGURO\s*:(?:[^\n\r0-9]+)?[\s\n\r]*([0-9.,]+)",
        r"INSURANCE\s*[:.\s]+([0-9.,]+)"
    ],
    "valor_cif": [
        r"VALOR\s+CIF\s*[:.\s]+(?:USD|DOL|USS)?\s*([0-9.,]+)",
        r"CIF\s*[:.\s]+(?:USD|DOL|USS)?\s*([0-9.,]+)",
        r"VALOR\s+IMPONIBLE(?:[^\n\r0-9]+)?[\s\n\r]*([0-9.,]+)",
        r"VALOR\s+ADUANERO\s*[:.\s]+([0-9.,]+)"
    ],
    "valor_imponible": [
        r"VALOR\s+IMPONIBLE\s*[\s\n\r]*([0-9.,]+)"
    ],
    "moneda": [
        r"DIVISA\s+FACTURA(?:[^\n\r]+)?[\n\r\s]*(?:[0-9.,]+\s+)?([A-Z]{3,4})",
        r"DIVISA\s+FLETE\s*:(?:[^\n\r]+)?[\n\r\s]*(?:[0-9.,]+\s+)?([A-Z]{3,4})",
        r"DIVISA\s*[:.\s]+([A-Z]{3,4})",
        r"MONEDA\s*[:.\s]+([A-Z]{3,4})",
        r"CURRENCY\s*[:.\s]+([A-Z]{3,4})"
    ],
    "tipo_cambio": [
        r"CAMBIO\s+FACTURA(?:[^\n\r]+)?[\n\r\s]*(?:[^\n\r0-9]+)?([0-9.,]+)",
        r"CAMBIO\s+USS(?:[^\n\r]+)?[\n\r\s]*(?:[0-9.,]+\s+)?([0-9.,]+)",
        r"TIPO\s+DE\s+CAMBIO\s*[:.\s]+([0-9.,]+)",
        r"EXCHANGE\s+RATE\s*[:.\s]+([0-9.,]+)"
    ],
    "peso_neto": [
        r"KILO\s+NETO\s*[\s\n\r]*([0-9.,]+)",
        r"PESO\s+NETO\s*[:.\s]+([0-9.,]+)",
        r"NET\s+WEIGHT\s*[:.\s]+([0-9.,]+)"
    ],
    "peso_bruto": [
        r"KILO\.BRUTO\s+Decla\.\s*([0-9.,]+)",
        r"KILO\s+BRUTO\s*[\s\n\r]*([0-9.,]+)",
        r"PESO\s+BRUTO\s*[:.\s]+([0-9.,]+)",
        r"GROSS\s+WEIGHT\s*[:.\s]+([0-9.,]+)"
    ],
    "cantidad_bultos": [
        r"CANT\.BULTOS\s+Decla\.\s*([0-9.,]+)",
        r"BULTOS\s*[:.\s]+([0-9]+)",
        r"PACKAGES\s*[:.\s]+([0-9]+)"
    ],
    "total_general": [
        r"TOTAL\s+GENERAL\.\.\s*([0-9.,]+)",
        r"TOTAL\s+A\s+PAGAR\s*[:.\s]+([0-9.,]+)",
        r"TOTAL\s+TRIBUTOS\s*[:.\s]+([0-9.,]+)"
    ],
    "bl": [
        r"BL\s*N[ºo°]?\s*[:.\s]+([A-Z0-9]+)",
        r"B/L\s*N[ºo°]?\s*[:.\s]+([A-Z0-9]+)",
        r"BILL\s+OF\s+LADING\s*[:.\s]+([A-Z0-9]+)"
    ],
    "awb": [
        r"AWB\s*N[ºo°]?\s*[:.\s]+([0-9A-Z-]+)",
        r"GUIA\s+AEREA\s*[:.\s]+([0-9A-Z-]+)"
    ],
    "contenedor": [
        r"CONTENEDOR\s*[:.\s]+([A-Z]{4}[0-9]{7})",
        r"CONTAINER\s*[:.\s]+([A-Z]{4}[0-9]{7})"
    ]
}

def extract_field_from_text(pages_data: List[Dict[str, Any]], field_key: str) -> Optional[Dict[str, Any]]:
    """Busca un campo específico a través de todas las páginas utilizando los patrones definidos."""
    patterns = FIELD_ALIASES.get(field_key, [])
    if not patterns:
        return None

    for page_info in pages_data:
        text = page_info["text"]
        page_num = page_info["page_num"]
        metodo = page_info.get("method", "TEXT")

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                raw_extracted = match.group(1).strip() if match.groups() else match.group(0).strip()
                texto_origen = match.group(0).strip()
                
                # Normalización según tipo de campo
                if "fecha" in field_key:
                    val = parse_date(raw_extracted)
                    conf = 0.95 if val else 0.50
                elif "valor" in field_key or "peso" in field_key or "cantidad" in field_key or "total" in field_key or "tipo_cambio" in field_key:
                    val = parse_currency(raw_extracted)
                    conf = 0.95 if val is not None else 0.50
                elif "documento" in field_key:
                    val = normalize_document(raw_extracted)
                    conf = 0.95 if val else 0.60
                elif "moneda" in field_key:
                    val = clean_text(raw_extracted)
                    if val:
                        val = val.upper()
                        if val in ("DOL", "USS"):
                            val = "USD"
                        elif val in ("GS", "GS.", "GUARANIES"):
                            val = "PYG"
                    conf = 0.95 if val else 0.60
                elif "canal" in field_key:
                    val = clean_text(raw_extracted).upper() if raw_extracted else None
                    conf = 0.98 if val in ("VERDE", "NARANJA", "ROJO") else 0.70
                else:
                    val = clean_text(raw_extracted)
                    conf = 0.92 if val else 0.40

                if val is not None:
                    return {
                        "valor": val,
                        "valor_original": raw_extracted,
                        "confidence": conf,
                        "pagina": page_num,
                        "texto_origen": texto_origen,
                        "metodo": metodo
                    }

    return None

def extract_all_fields(pages_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Ejecuta la extracción determinística de todos los campos sobre el documento.
    Retorna un diccionario con los valores normalizados y un sub-diccionario 'metadata' con trazabilidad.
    """
    result: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}
    
    total_conf = 0.0
    found_count = 0
    total_fields = len(FIELD_ALIASES)

    for field_key in FIELD_ALIASES.keys():
        extracted = extract_field_from_text(pages_data, field_key)
        if extracted and extracted["valor"] is not None:
            result[field_key] = extracted["valor"]
            metadata[field_key] = {
                "valor_original": extracted["valor_original"],
                "confidence": extracted["confidence"],
                "pagina": extracted["pagina"],
                "texto_origen": extracted["texto_origen"],
                "metodo": extracted["metodo"]
            }
            total_conf += extracted["confidence"]
            found_count += 1
        else:
            result[field_key] = None
            metadata[field_key] = {
                "valor_original": None,
                "confidence": 0.0,
                "pagina": 1,
                "texto_origen": None,
                "metodo": "NONE"
            }

    # Asignaciones especiales y ajustes cruzados para SOFIA
    # Si valor_cif no fue encontrado pero tenemos VALOR FACTURA en la página 1
    if not result.get("valor_fob") and result.get("valor_imponible"):
        # En muchos despachos SOFIA, VALOR FACTURA = FOB
        pass

    avg_conf = (total_conf / found_count) if found_count > 0 else 0.0
    result["confianza_promedio"] = round(avg_conf, 2)
    result["metadata_extraccion"] = metadata

    return result
