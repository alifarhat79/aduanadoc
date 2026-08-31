"""
Servicio de Importación Inteligente de Planillas de Clientes.
Soporta Excel (.xlsx, .xls), CSV, Word (.docx), PDF y Texto.
Utiliza mapeo semántico difuso y MarkItDown para extraer ítems, cantidades,
precios y marcas sin importar el formato o nombres de columnas del cliente.
"""
import io
import re
import csv
import logging
from typing import List, Dict, Any, Optional, Tuple
import openpyxl
from markitdown import MarkItDown

logger = logging.getLogger(__name__)

# Diccionario de sinónimos semánticos multilingües (Español, Portugués, Inglés)
COLUMN_SYNONYMS = {
    "mercaderia": [
        "descripcion mercaderia", "descrição da mercadoria", "descripcion de mercaderia",
        "product description", "item description", "nome do produto", "especificacao",
        "especificação", "descricao", "descrição", "descriçao", "mercaderia", "mercadería",
        "mercadoria", "produto", "detalle", "detalles", "detalhes", "name", "description",
        "designacion", "designación", "articulo", "artículo", "artigo", "concepto",
        "modelo", "denominacion", "denominación", "product name", "itens"
    ],
    "cantidad": [
        "quantidade", "qtd", "quant", "qnt", "cantidad", "cant", "cant.", "qty", "quantity",
        "unidades", "unid", "unid.", "peças", "pecas", "pçs", "pzas", "pzas.", "pcs", "pcs.",
        "ctd", "bultos", "boxes", "volumes", "qta", "cajas", "paquetes", "volumen"
    ],
    "precio_factura": [
        "valor invoice", "preco invoice", "preço invoice", "invoice price", "invoice",
        "preço unitário", "preco unitario", "preço unit", "preco unit", "valor unitário",
        "valor unitario", "vlr unit", "vl. unit", "precio unit", "precio unit.",
        "precio unitario", "p. unit", "p. unit.", "p.unit", "fob unit", "unit price",
        "precio factura", "valor unit", "valor unit.", "valor unitario", "valor fob unit",
        "pu", "p/u", "costo unitario", "preço", "preco", "precio", "price", "unitario",
        "costo", "custo", "rate", "tarifa"
    ],
    "precio_normal": [
        "preço normal", "preco normal", "valor aduaneiro", "preço tabela", "p. tabela",
        "precio normal", "valor normal", "fob normal", "p. normal", "p.normal", "precio oficial",
        "normal price", "precio referencia", "ref price", "valor aduana", "precio tabla"
    ],
    "precio_total": [
        "valor total", "vlr total", "vl. total", "preço total", "preco total", "total r$",
        "total fob", "fob total", "precio total", "valor total", "total usd", "total fob usd",
        "total importe", "total general", "total amount", "valor fob", "importe total",
        "monto total", "montante", "total", "importe", "amount", "monto"
    ],
    "marca": [
        "marca", "brand", "fabricante", "make", "marca/fabricante", "marca comercial", "trademark"
    ],
    "codigo_producto": [
        "código de barras", "codigo de barras", "código do produto", "codigo do produto",
        "código produto", "codigo produto", "referência", "referencia", "código", "codigo",
        "cód", "cod", "cod.", "item code", "part number", "p/n", "ean", "sku", "ref", "ref.",
        "barcode", "upc", "item", "nro", "item nro", "item #"
    ],
    "observacion": [
        "observação", "observacao", "observações", "observacoes", "observacion", "observación",
        "observaciones", "obs", "obs.", "nota", "notas", "posicion arancelaria", "posión arancelaria",
        "hs code", "arancel", "ncm", "posicion", "origem", "origen", "país", "pais", "country",
        "comentario", "comentarios", "comentários"
    ]
}

# Marcas comerciales base conocidas
BASE_BRANDS = [
    "LATTAFA", "ARMAF", "AFNAN", "AL HARAMAIN", "AJMAL", "RASASI", "FRAGRANCE WORLD", "AL WATANIAH",
    "VAPORESSO", "SMOK", "GEEKVAPE", "VOOPOO", "OXVA", "LOST VAPE", "UWELL", "CALIBURN", "ELFBAR",
    "PIONEER", "SONY", "JBL", "SHURE", "SENNHEISER", "BEHRINGER", "YAMAHA", "SAMSUNG", "APPLE",
    "XIAOMI", "HUAWEI", "MOTOROLA", "LG", "ASUS", "HP", "DELL", "LENOVO", "KINGSTON", "SANDISK",
    "ANEST IWATA", "DEVILBISS", "SATA", "MAKITA", "DEWALT", "BOSCH", "STANLEY", "MILWAUKEE",
    "CAROLINA HERRERA", "PACO RABANNE", "DIOR", "CHANEL", "VERSACE", "CALVIN KLEIN", "HUGO BOSS",
    "GUCCI", "DOLCE GABBANA", "BURBERRY", "MONTBLANC", "YVES SAINT LAURENT", "GIORGIO ARMANI",
    "CERAVE", "CERA VE", "LA ROCHE POSAY", "THE ORDINARY", "NIVEA", "LOREAL", "L'OREAL", "EUCERIN",
    "ISDIN", "NEUTROGENA", "MAYBELLINE", "MAC", "SEPHORA", "BIODERMA", "VICHY", "AVENE", "CLINIQUE",
    "ESTEE LAUDER", "LANCOME", "KIEHL'S", "KIEHLS", "SHISEIDO", "GARNIER"
]

_CACHED_ALL_BRANDS: Optional[List[str]] = None


def get_all_registered_brands() -> List[str]:
    """Obtiene y cachea en memoria todas las marcas de la BD + lista base ordenadas por longitud."""
    global _CACHED_ALL_BRANDS
    if _CACHED_ALL_BRANDS is not None:
        return _CACHED_ALL_BRANDS

    all_set = set(BASE_BRANDS)
    try:
        from app.database import SessionLocal
        from app.models import DespachoItem
        db = SessionLocal()
        db_brands = (
            db.query(DespachoItem.marca)
            .distinct()
            .filter(DespachoItem.marca.isnot(None), DespachoItem.marca != "")
            .all()
        )
        for r in db_brands:
            if r[0]:
                b_clean = r[0].strip().upper()
                if len(b_clean) >= 2:
                    all_set.add(b_clean)
        db.close()
    except Exception as e:
        logger.warning(f"[ClientSheetImporter] No se pudo cargar marcas de BD: {e}")

    # Ordenar por longitud descendente para que 'ACQUA DI GIO' coincida antes que 'ACQUA'
    _CACHED_ALL_BRANDS = sorted(list(all_set), key=lambda x: len(x), reverse=True)
    return _CACHED_ALL_BRANDS


def clean_header_name(header: Any) -> str:
    """Normaliza un texto de encabezado para comparación (Español, Portugués, Inglés)."""
    if not header:
        return ""
    text = str(header).strip().lower()
    # Normalizar caracteres portugueses y españoles
    text = re.sub(r'[ç]', 'c', text)
    text = re.sub(r'[áàäâã]', 'a', text)
    text = re.sub(r'[éèëê]', 'e', text)
    text = re.sub(r'[íìïî]', 'i', text)
    text = re.sub(r'[óòöôõ]', 'o', text)
    text = re.sub(r'[úùüû]', 'u', text)
    text = re.sub(r'[\r\n\t_]+', ' ', text)
    text = re.sub(r'[^\w\s\./-]', '', text)
    return text.strip()


def parse_numeric_value(val: Any) -> float:
    """Convierte cadenas con símbolos de moneda, comas y puntos a float válido."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).strip()
    if not s:
        return 0.0

    s = re.sub(r'[$€£]|USD|Gs|GS|PYG|R\$', '', s).strip()
    s = re.sub(r'\s+', '', s)

    if ',' in s and '.' in s:
        if s.rfind(',') > s.rfind('.'):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
    elif ',' in s:
        parts = s.split(',')
        if len(parts) == 2 and len(parts[1]) <= 2:
            s = s.replace(',', '.')
        else:
            s = s.replace(',', '')

    try:
        return float(s)
    except Exception:
        m = re.search(r'[-+]?\d*\.?\d+', s)
        if m:
            try:
                return float(m.group(0))
            except Exception:
                return 0.0
        return 0.0


def auto_detect_brand(description: str) -> str:
    """Detecta automáticamente la marca buscando entre las marcas registradas en el sistema."""
    if not description:
        return ""
    desc_upper = description.upper()
    all_brands = get_all_registered_brands()
    for brand in all_brands:
        if re.search(r'\b' + re.escape(brand) + r'\b', desc_upper):
            return brand.title()
    return ""


def match_columns_to_fields(headers: List[str]) -> Dict[str, Optional[int]]:
    """
    Identifica qué índice de columna corresponde a cada campo de PlanillaItem
    usando coincidencia exacta y difusa, protegiendo precios contra columnas de peso.
    """
    mapping: Dict[str, Optional[int]] = {
        "mercaderia": None,
        "cantidad": None,
        "precio_factura": None,
        "precio_normal": None,
        "precio_total": None,
        "marca": None,
        "codigo_producto": None,
        "observacion": None
    }

    used_indices = set()
    cleaned_headers = [clean_header_name(h) for h in headers]

    # Pasada 1: Coincidencias exactas
    for field_name, synonyms in COLUMN_SYNONYMS.items():
        for syn in synonyms:
            clean_syn = clean_header_name(syn)
            for idx, h in enumerate(cleaned_headers):
                if idx in used_indices or not h:
                    continue
                # Proteger precios contra columnas de peso/kilos
                if field_name.startswith("precio") and any(w in h for w in ["peso", "kg", "kilo", "weight", "gramo"]):
                    continue
                if h == clean_syn:
                    mapping[field_name] = idx
                    used_indices.add(idx)
                    break
            if mapping[field_name] is not None:
                break

    # Pasada 2: Coincidencias difusas / parciales
    for field_name, synonyms in COLUMN_SYNONYMS.items():
        if mapping[field_name] is not None:
            continue
        for syn in synonyms:
            clean_syn = clean_header_name(syn)
            for idx, h in enumerate(cleaned_headers):
                if idx in used_indices or not h:
                    continue
                if field_name.startswith("precio") and any(w in h for w in ["peso", "kg", "kilo", "weight", "gramo"]):
                    continue
                if clean_syn in h or h in clean_syn:
                    mapping[field_name] = idx
                    used_indices.add(idx)
                    break
            if mapping[field_name] is not None:
                break

    if mapping["mercaderia"] is None:
        for idx, h in enumerate(cleaned_headers):
            if idx not in used_indices and h and not any(w in h for w in ["peso", "kg", "total", "precio", "qty", "cant"]):
                mapping["mercaderia"] = idx
                used_indices.add(idx)
                break

    return mapping


class ClientSheetImporter:
    """Procesador universal de archivos de clientes."""

    @classmethod
    def import_file(cls, file_bytes: bytes, filename: str) -> Dict[str, Any]:
        ext = filename.lower().split('.')[-1] if '.' in filename else ''
        raw_rows: List[List[Any]] = []
        
        if ext in ['xlsx', 'xlsm', 'xltx']:
            raw_rows = cls._read_excel_openpyxl(file_bytes)
        elif ext == 'csv':
            raw_rows = cls._read_csv(file_bytes)
        elif ext in ['docx', 'pdf', 'txt', 'html']:
            raw_rows = cls._read_with_markitdown(file_bytes, filename)
        else:
            try:
                raw_rows = cls._read_excel_openpyxl(file_bytes)
            except Exception:
                raw_rows = cls._read_with_markitdown(file_bytes, filename)

        if not raw_rows:
            return {
                "success": False,
                "message": "No se encontraron datos o tablas legibles en el archivo.",
                "items": [],
                "total_items": 0,
                "headers": [],
                "raw_columns": []
            }

        header_idx, best_headers = cls._find_header_row(raw_rows)
        data_rows = raw_rows[header_idx + 1:]
        col_mapping = match_columns_to_fields(best_headers)

        all_registered_brands = get_all_registered_brands()
        items: List[Dict[str, Any]] = []

        for row_idx, row in enumerate(data_rows):
            while len(row) < len(best_headers):
                row.append("")

            def get_cell(field_key: str) -> Any:
                c_idx = col_mapping.get(field_key)
                if c_idx is not None and c_idx < len(row):
                    return row[c_idx]
                return None

            # Obtener celdas de texto de la fila para análisis profundo
            text_cells = [
                str(c).strip() for c in row 
                if c and not str(c).strip().replace('.', '').replace(',', '').replace('$', '').replace('-', '').isdigit()
                and len(str(c).strip()) > 1
            ]

            raw_desc = str(get_cell("mercaderia") or "").strip()
            
            # Si la columna mapeada es corta y hay otra celda de texto más larga y detallada (ej: sub-columnas de descripción combinada)
            if text_cells:
                longest_text = max(text_cells, key=len)
                if len(longest_text) > len(raw_desc) + 4:
                    desc = longest_text
                else:
                    desc = raw_desc or longest_text
            else:
                desc = raw_desc

            # Lista de palabras clave de filas que NO son mercaderías
            non_product_keywords = [
                "total", "subtotal", "grand total", "total general", "total importe", "total fob", "total r$", "total usd",
                "suma", "promedio", "saldo", "balance", "valor total", "importe total",
                "bank", "banco", "swift", "iban", "pix", "account", "transferencia", "pagamento", "payment",
                "terms", "condiciones", "condicoes", "validade", "prazo", "forma de pagamento", "vencimento",
                "container", "contenedor", "seal", "lacre", "motorista", "placa", "booking", "navio", "vessel", "b/l",
                "frete", "flete", "freight", "seguro", "insurance", "desconto", "discount", "tax", "imposto", "tarifa",
                "observacao", "observação", "observacoes", "observaciones", "nota", "notas", "assinatura", "firma"
            ]

            desc_l = desc.lower()
            if not desc or len(desc.strip()) < 3:
                continue

            if any(desc_l == kw or desc_l.startswith(kw + ' ') or desc_l.startswith(kw + ':') or desc_l.endswith(' ' + kw) for kw in non_product_keywords):
                continue

            # 1. Cantidad estrictamente > 0
            cant = parse_numeric_value(get_cell("cantidad"))
            if cant <= 0:
                # Buscar en la fila si hay un valor entero positivo representativo de cantidad
                for c in row:
                    val_n = parse_numeric_value(c)
                    if 1 <= val_n <= 100000 and float(val_n).is_integer():
                        cant = float(val_n)
                        break
            if cant <= 0:
                continue  # Omitir filas sin cantidad o cantidad <= 0

            # 2. Precios estrictamente > 0 (Omitir ítems con valor $0, muestras gratuitas, textos de pie)
            p_factura = parse_numeric_value(get_cell("precio_factura"))
            p_normal = parse_numeric_value(get_cell("precio_normal"))
            p_total = parse_numeric_value(get_cell("precio_total"))

            if p_factura <= 0 and p_total <= 0:
                continue  # Omitir filas con valor 0

            if p_factura <= 0 and p_total > 0 and cant > 0:
                p_factura = round(p_total / cant, 4)
            elif p_total <= 0 and p_factura > 0:
                p_total = round(cant * p_factura, 2)
            elif p_factura > 0 and p_total > 0:
                p_total = round(cant * p_factura, 2)

            if p_normal <= 0:
                p_normal = p_factura

            # Detección de Marca (de columna específica, de celdas de fila o de descripción)
            marca = str(get_cell("marca") or "").strip()
            if not marca:
                # Buscar en las celdas de la fila
                for cell_t in text_cells:
                    for b in all_registered_brands:
                        if re.search(r'\b' + re.escape(b) + r'\b', cell_t.upper()):
                            marca = b.title()
                            break
                    if marca:
                        break
            if not marca:
                marca = auto_detect_brand(desc)

            # Detección de Código de Barras / EAN
            codigo = str(get_cell("codigo_producto") or "").strip()
            # Buscar si hay un EAN o código de barra de 8 a 14 dígitos en la fila o en la descripción
            ean_match = None
            for cell_c in row:
                m_ean = re.search(r'\b(50[0-9]{11}|62[0-9]{11}|69[0-9]{11}|77[0-9]{11}|84[0-9]{11}|[0-9]{8,14})\b', str(cell_c))
                if m_ean:
                    ean_match = m_ean.group(0)
                    break
            if ean_match:
                codigo = ean_match
            elif codigo.endswith(".0") and codigo[:-2].isdigit():
                codigo = codigo[:-2]

            obs = str(get_cell("observacion") or "").strip()

            items.append({
                "orden": len(items) + 1,
                "item_catalogo_id": None,
                "cantidad": cant,
                "mercaderia": desc,
                "marca": marca,
                "codigo_producto": codigo,
                "precio_factura": p_factura,
                "precio_normal": p_normal,
                "precio_total": p_total,
                "observacion": obs
            })

        return {
            "success": True,
            "filename": filename,
            "total_items": len(items),
            "headers": best_headers,
            "col_mapping": col_mapping,
            "items": items
        }

    @staticmethod
    def _read_excel_openpyxl(file_bytes: bytes) -> List[List[Any]]:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
        sheet = wb.active
        rows = []
        for row in sheet.iter_rows(values_only=True):
            cleaned_row = ["" if cell is None else str(cell).strip() for cell in row]
            if any(cleaned_row):
                rows.append(cleaned_row)
        wb.close()
        return rows

    @staticmethod
    def _read_csv(file_bytes: bytes) -> List[List[Any]]:
        text = file_bytes.decode('utf-8', errors='ignore')
        first_lines = "\n".join(text.splitlines()[:5])
        delimiter = ','
        if ';' in first_lines and first_lines.count(';') > first_lines.count(','):
            delimiter = ';'
        elif '\t' in first_lines and first_lines.count('\t') > first_lines.count(','):
            delimiter = '\t'

        rows = []
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        for row in reader:
            cleaned = [c.strip() for c in row]
            if any(cleaned):
                rows.append(cleaned)
        return rows

    @classmethod
    def _read_with_markitdown(cls, file_bytes: bytes, filename: str) -> List[List[Any]]:
        try:
            md = MarkItDown()
            stream = io.BytesIO(file_bytes)
            ext = '.' + filename.split('.')[-1] if '.' in filename else '.txt'
            res = md.convert_stream(stream, file_extension=ext)
            md_text = res.text_content or ""
            return cls._parse_markdown_tables(md_text)
        except Exception as e:
            logger.warning(f"[ClientSheetImporter] Error en MarkItDown: {e}")
            return []

    @staticmethod
    def _parse_markdown_tables(md_text: str) -> List[List[Any]]:
        rows: List[List[Any]] = []
        for line in md_text.splitlines():
            line_str = line.strip()
            if line_str.startswith('|') and line_str.endswith('|'):
                if re.match(r'^\|[\s\-:|]+\|$', line_str):
                    continue
                cells = [c.strip() for c in line_str.split('|')[1:-1]]
                if any(cells):
                    rows.append(cells)
        return rows

    @staticmethod
    def _find_header_row(rows: List[List[Any]], max_scan: int = 20) -> Tuple[int, List[str]]:
        all_keywords = set()
        for syn_list in COLUMN_SYNONYMS.values():
            for s in syn_list:
                all_keywords.add(clean_header_name(s))

        best_score = -1
        best_idx = 0

        for idx, row in enumerate(rows[:max_scan]):
            score = 0
            for cell in row:
                c_clean = clean_header_name(cell)
                if not c_clean:
                    continue
                if c_clean in all_keywords:
                    score += 3
                elif any(kw in c_clean for kw in all_keywords):
                    score += 1
            if score > best_score and score > 0:
                best_score = score
                best_idx = idx

        # Reconstruir encabezados compuestos integrando filas superiores estrictamente por columna
        num_cols = max(len(r) for r in rows[:best_idx + 1]) if rows else 0
        composite_headers = ['' for _ in range(num_cols)]

        for r_i in range(best_idx + 1):
            r = rows[r_i]
            for c_i in range(num_cols):
                cell_val = str(r[c_i]).strip() if c_i < len(r) else ''
                if not cell_val:
                    continue
                if not composite_headers[c_i]:
                    composite_headers[c_i] = cell_val
                else:
                    if cell_val.upper() not in composite_headers[c_i].upper():
                        composite_headers[c_i] = f"{composite_headers[c_i]} {cell_val}"

        # Propagar agrupadores de sección (VALOR, PESO) a columnas de totales adyacentes
        for c_i in range(num_cols):
            h = composite_headers[c_i].upper()
            if 'VALOR' in h:
                if c_i + 1 < num_cols and 'TOTAL' in composite_headers[c_i + 1].upper() and 'VALOR' not in composite_headers[c_i + 1].upper() and 'PESO' not in composite_headers[c_i + 1].upper():
                    composite_headers[c_i + 1] = f"VALOR {composite_headers[c_i + 1]}"
            if 'PESO' in h:
                if c_i + 1 < num_cols and 'TOTAL' in composite_headers[c_i + 1].upper() and 'PESO' not in composite_headers[c_i + 1].upper() and 'VALOR' not in composite_headers[c_i + 1].upper():
                    composite_headers[c_i + 1] = f"PESO {composite_headers[c_i + 1]}"

        final_headers = [
            h.strip() if h.strip() else f"Columna {i+1}" 
            for i, h in enumerate(composite_headers)
        ]
        return best_idx, final_headers
