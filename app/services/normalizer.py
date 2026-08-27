import re
from datetime import datetime, date
from typing import Optional

MONTH_MAP = {
    "jan": 1, "ene": 1, "january": 1, "enero": 1,
    "feb": 2, "febrero": 2, "february": 2,
    "mar": 3, "marzo": 3, "march": 3,
    "apr": 4, "abr": 4, "abril": 4, "april": 4,
    "may": 5, "mayo": 5,
    "jun": 6, "junio": 6, "june": 6,
    "jul": 7, "julio": 7, "july": 7,
    "aug": 8, "ago": 8, "agosto": 8, "august": 8,
    "sep": 9, "sept": 9, "set": 9, "septiembre": 9, "setiembre": 9, "september": 9,
    "oct": 10, "octubre": 10, "october": 10,
    "nov": 11, "noviembre": 11, "november": 11,
    "dec": 12, "dic": 12, "diciembre": 12, "december": 12
}

def clean_text(raw_text: Optional[str]) -> Optional[str]:
    """Limpia espacios en blanco duplicados, caracteres inválidos y artefactos OCR."""
    if not raw_text:
        return None
    # Eliminar asteriscos de relleno típicos de sistemas aduaneros (ej. **********)
    text = re.sub(r"\*{2,}", "", raw_text)
    # Normalizar espacios y saltos
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if text else None

def parse_date(raw_date: Optional[str]) -> Optional[date]:
    """
    Normaliza una fecha a un objeto date (YYYY-MM-DD).
    Soporta formatos:
    - 10/08/2026 o 10/08/26 (con o sin hora 17:13:10)
    - 10-08-2026 o 10-08-26
    - 2026-08-10
    - 10-AUG-26 o 10-AGO-2026
    """
    if not raw_date:
        return None
    text = clean_text(raw_date)
    if not text:
        return None

    # Extraer solo la porción de fecha si viene con hora
    match_with_time = re.search(r"(\d{1,4}[/-]\w+[/-]\d{1,4})", text)
    if match_with_time:
        text = match_with_time.group(1)

    # 1. Caso DD-MON-YY / DD-MON-YYYY (ej. 10-AUG-26 o 10-AGO-2026)
    match_mon = re.match(r"^(\d{1,2})[-/]([a-zA-Z]{3,})[-/](\d{2,4})$", text)
    if match_mon:
        day = int(match_mon.group(1))
        mon_str = match_mon.group(2).lower()
        year_str = match_mon.group(3)
        year = int(year_str)
        if year < 100:
            year += 2000 if year <= 50 else 1900
        month = MONTH_MAP.get(mon_str[:3])
        if month and 1 <= day <= 31:
            try:
                return date(year, month, day)
            except ValueError:
                pass

    # 2. Formatos numéricos estándar
    formats = [
        "%d/%m/%Y", "%d-%m-%Y",
        "%Y-%m-%d", "%Y/%m/%d",
        "%d/%m/%y", "%d-%m-%y"
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    return None

def parse_currency(raw_val: Optional[str]) -> Optional[float]:
    """
    Normaliza valores numéricos/monetarios a float.
    Maneja correctamente:
    - 1.234,56 (formato ES/PY/DE) -> 1234.56
    - 1,234.56 (formato US) -> 1234.56
    - USD 12,500.00 -> 12500.00
    - Gs. 75.000.000 -> 75000000.0
    - 668.385,25 -> 668385.25
    - 4.002.948.937 -> 4002948937.0
    """
    if raw_val is None:
        return None
    if isinstance(raw_val, (int, float)):
        return float(raw_val)

    text = str(raw_val).strip()
    if not text:
        return None

    # Quitar símbolos de moneda y texto accesorio
    text = re.sub(r"(?i)[a-z$€Gs|USS|DOL|USD|PYG|BRL|R\$]", "", text).strip()
    if not text:
        return None

    # Extraer solo secuencias numéricas con separadores
    match = re.search(r"[-+]?[0-9.,]+", text)
    if not match:
        return None
    cleaned = match.group(0)

    # Analizar separadores
    has_comma = "," in cleaned
    has_dot = "." in cleaned

    if has_comma and has_dot:
        last_comma = cleaned.rfind(",")
        last_dot = cleaned.rfind(".")
        if last_comma > last_dot:
            # Formato 1.234.567,89 (coma decimal)
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # Formato 1,234,567.89 (punto decimal)
            cleaned = cleaned.replace(",", "")
    elif has_comma:
        # Solo coma: ej "1234,56" o "1,234"
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2, 3):
            # Decimal: 1234,56 -> 1234.56
            cleaned = parts[0] + "." + parts[1]
        elif len(parts) > 2:
            # Separador de miles: 1,234,567 -> 1234567
            cleaned = "".join(parts)
        else:
            cleaned = cleaned.replace(",", ".")
    elif has_dot:
        # Solo punto: ej "1234.56" o "75.000.000" o "19.196,000" (ya sin coma)
        parts = cleaned.split(".")
        if len(parts) > 2:
            # Múltiples puntos -> separadores de miles (ej: 4.002.948.937)
            cleaned = "".join(parts)
        elif len(parts) == 2:
            # Un solo punto. Si tiene 3 dígitos al final y el valor antes del punto es mayor,
            # podría ser miles (ej. 3.000) o decimal. Si son 3 dígitos y es parte de un contexto de miles.
            # En formato internacional un solo punto suele ser decimal a menos que claramente sea miles.
            # Lo dejamos como punto decimal float.
            pass

    try:
        val = float(cleaned)
        return val
    except ValueError:
        return None

def normalize_document(raw_doc: Optional[str]) -> Optional[str]:
    """Normaliza RUC, CNPJ o número de documento eliminando espacios y caracteres no estándar."""
    if not raw_doc:
        return None
    cleaned = clean_text(raw_doc)
    if not cleaned:
        return None
    # Eliminar textos como "RUC/DOC:", "RUC:", "DOC:" si quedaron adheridos
    cleaned = re.sub(r"(?i)^(?:RUC[/]DOC|RUC|DOC|CNPJ|TAX\s*ID|CPF)[:\s-]*", "", cleaned).strip()
    return cleaned if cleaned else None

COMPANY_CANONICAL_MAP = {
    'florace': 'FLORACE S.A.',
    'gafa': 'GAFA S.A.',
    'eras': 'ERAS S.R.L.',
    'h.t': 'H.T. S.A.',
    'ht': 'H.T. S.A.',
    'karry': 'KARRY S.A.',
    'jordana': 'JORDANA',
    'serena': 'SERENA',
    'suntec': 'SUNTEC',
    'la gloria': 'LA GLORIA',
    'mega': 'MEGA',
    'agatres': 'AGATRES',
}

def normalize_company_name(name: Optional[str]) -> Optional[str]:
    """Normaliza y unifica variaciones tipográficas de nombres de importadores y empresas."""
    if not name:
        return None
    s = clean_text(name)
    if not s:
        return None
    
    s_lower = s.lower().replace('.', '').replace(',', '').strip()
    for k, v in COMPANY_CANONICAL_MAP.items():
        k_clean = k.replace('.', '').strip()
        if s_lower == k_clean or s_lower.startswith(k_clean + ' '):
            return v
    return s.strip().upper()

