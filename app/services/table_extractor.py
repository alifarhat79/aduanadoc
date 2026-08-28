import re
from typing import List, Dict, Any
from app.services.normalizer import clean_text, parse_currency

def parse_observation_details(raw_obs: str, marca: str = "") -> tuple[str, str]:
    """
    Analiza la cadena de observación para separar:
    1. Código de Producto / EAN / Código de Barra / SKU (ej. 6294015160734, 3614274347562, 719346034425, etc.)
    2. Descripción limpia de la mercadería sin códigos numéricos al final/inicio y sin prefijos repetidos de la marca.
    """
    if not raw_obs:
        return "", ""

    text = clean_text(raw_obs) or ""
    codigo_producto = ""
    descripcion = text

    # 1. Buscar código EAN / Código de barras de 8 a 14 dígitos al final de la descripción
    # Ej: "ODYSSEY TYRANT (M)EDP SP 3.4OZ 6294015160734" -> EAN: 6294015160734
    end_ean_match = re.search(r"\b([0-9]{8,14})\s*$", descripcion)
    if end_ean_match:
        codigo_producto = end_ean_match.group(1).strip()
        descripcion = descripcion[:end_ean_match.start()].strip()

    # 2. Si no se encontró al final, buscar código numérico largo (8 a 14 dígitos) al inicio
    # Ej: "6295199818459 L-ARMAF ODYSSEY..." -> EAN: 6295199818459
    if not codigo_producto:
        start_ean_match = re.match(r"^([0-9]{8,14})\s+(.*)$", descripcion)
        if start_ean_match:
            codigo_producto = start_ean_match.group(1).strip()
            descripcion = start_ean_match.group(2).strip()

    # 3. Si aún no hay código o había un SKU interno al inicio (ej. I0095135)
    start_sku_match = re.match(r"^([A-Za-z0-9_-]{4,20})\s+(.*)$", descripcion)
    if start_sku_match:
        potential_sku = start_sku_match.group(1).strip()
        if any(c.isdigit() for c in potential_sku):
            if not codigo_producto:
                codigo_producto = potential_sku
            descripcion = start_sku_match.group(2).strip()

    # 4. Remover cualquier código numérico residual de 8 a 14 dígitos que haya quedado
    residual_ean = re.search(r"\b([0-9]{8,14})\b", descripcion)
    if residual_ean:
        if not codigo_producto:
            codigo_producto = residual_ean.group(1).strip()
        descripcion = (descripcion[:residual_ean.start()] + " " + descripcion[residual_ean.end():]).strip()

    # 5. Eliminar patrones secuenciales de ítems que no forman parte de la descripción comercial:
    # Ej: "COD: ITEM NRO.1", "COD: ITEM NRO.12", "ITEM NRO.5", "COD: ITEM N° 3", etc.
    descripcion = re.sub(r"\bCOD\s*:\s*ITEM\s*(?:NRO\.?|N°|NUM\.?|NO\.?)\s*\d+\b", "", descripcion, flags=re.IGNORECASE).strip()
    descripcion = re.sub(r"\bITEM\s*(?:NRO\.?|N°|NUM\.?|NO\.?)\s*\d+\b", "", descripcion, flags=re.IGNORECASE).strip()
    descripcion = re.sub(r"\bCOD\s*:\s*ITEM\s*\d+\b", "", descripcion, flags=re.IGNORECASE).strip()
    descripcion = re.sub(r"\bCOD\s*:\s*$", "", descripcion, flags=re.IGNORECASE).strip()

    # 6. Limpiar prefijos de marca o letras de lote al inicio (ej. "L-ARMAF", "M-ARMAF", "ARMAF -")
    if marca:
        clean_marca = re.escape(marca.strip())
        descripcion = re.sub(rf"^(?:[A-Z]-)?{clean_marca}\s*[-:]?\s*", "", descripcion, flags=re.IGNORECASE).strip()

    # Quitar guiones, dos puntos o espacios redundantes sobrantes
    descripcion = re.sub(r"^[-:\s]+|[-:\s]+$", "", descripcion).strip()
    descripcion = re.sub(r"\s{2,}", " ", descripcion).strip()

    if not descripcion:
        descripcion = text

    return codigo_producto, descripcion

KNOWN_UNITS = {
    'UNIDAD', 'UNIDADES', 'KILO', 'KILOS', 'KG', 'DOCENA', 'DOCENAS', 'LITRO',
    'LITROS', 'PAR', 'PARES', 'METRO', 'METROS', 'SET', 'SETS', 'CAJA', 'CAJAS',
    'PACK', 'PACKS', 'PIEZA', 'PIEZAS', 'GR', 'GRAMO', 'GRAMOS'
}

def extract_items_from_pages(pages_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Extrae los ítems y subítems de mercancías a lo largo de todas las páginas del PDF.
    Aísla la marca, el código de producto/EAN, cantidad, peso, valores y la descripción limpia.
    Soporta formato de sub-ítems, formato SOFIA Grid (N/TOTAL) y formato de ítems estándar.
    """
    extracted_items: List[Dict[str, Any]] = []
    
    # 1. Patrón para SUB ITEMS de SOFIA
    subitem_pattern = re.compile(
        r"NRO\s+ITEM\s*:\s*(\d+)\s+POS\.ARANC\.\s*:\s*([0-9A-Za-z.]+)\s+SUB\s+ITEM\s+NRO\s*:\s*(\d+)"
        r".*?CANTIDAD\s*:\s*([0-9.,]+).*?FOB\s*:\s*([0-9.,]+)"
        r"(?:.*?MARCA\s*:\s*([^\n\r]+?)(?=\s+OBSERVACION|\s*[\n\r]))?"
        r"(?:.*?OBSERVACION\s*:\s*([^\n\r]+))?",
        re.DOTALL | re.IGNORECASE
    )

    for page_info in pages_data:
        text = page_info["text"]
        page_num = page_info["page_num"]

        subitem_matches = list(subitem_pattern.finditer(text))
        if subitem_matches:
            for m in subitem_matches:
                nro_item = int(m.group(1))
                pos_aranc = clean_text(m.group(2))
                sub_item_nro = int(m.group(3))
                cantidad = parse_currency(m.group(4))
                fob = parse_currency(m.group(5))
                marca = clean_text(m.group(6)) or ""
                raw_obs = clean_text(m.group(7)) or ""

                codigo_producto, descripcion = parse_observation_details(raw_obs, marca)

                if not descripcion:
                    descripcion = f"Subítem {sub_item_nro} - Posición {pos_aranc}"

                extracted_items.append({
                    "numero_item": nro_item,
                    "numero_subitem": sub_item_nro,
                    "codigo_ncm": pos_aranc,
                    "codigo_producto": codigo_producto or None,
                    "descripcion": descripcion,
                    "marca": marca or "Sin Marca",
                    "cantidad": cantidad,
                    "unidad": "UNIDAD",
                    "peso_neto": None,
                    "peso_bruto": None,
                    "valor_unitario": round(fob / cantidad, 2) if (fob and cantidad and cantidad > 0) else None,
                    "valor_total": fob,
                    "pais_origen": None,
                    "pais_procedencia": None,
                    "pagina_origen": page_num
                })

    # 2. Si no hubo subitems, probar formato SOFIA Grid / Zona Franca (ej. 1/26, 2/26 con bloques)
    if not extracted_items:
        # Pre-procesar y unir líneas continuas limpiando cabeceras de página
        all_lines_data: List[Dict[str, Any]] = []
        for page_info in pages_data:
            text = page_info["text"]
            page_num = page_info["page_num"]
            for l in text.splitlines():
                l_str = l.strip()
                if l_str and not l_str.startswith("DESPACHO NUMERO:") and not l_str.startswith("REGIMEN :") and not l_str.startswith("ADUANA :") and not l_str.startswith("USUARIO:"):
                    all_lines_data.append({"line": l_str, "page": page_num})

        i = 0
        n = len(all_lines_data)
        while i < n:
            line = all_lines_data[i]["line"]
            page_num = all_lines_data[i]["page"]
            
            # Match formato "1/26 N 3303.00.20.000W ..." o "1/26"
            m_grid = re.match(r"^(\d{1,3})\s*/\s*(\d{1,3})(?:\s+[A-Z]\s+([0-9.]{6,15}[A-Za-z]?))?", line)
            if m_grid:
                item_nro = int(m_grid.group(1))
                total_items = int(m_grid.group(2))
                pos_aranc = m_grid.group(3) if m_grid.group(3) else None

                fob_factura = None
                desc = None
                marca = None
                cantidad = None
                unidad = None
                fob_uss = None
                kilo_neto = None
                pais_origen = None
                pais_proc = None

                j = i + 1
                while j < n and j < i + 35:
                    sub_line = all_lines_data[j]["line"]
                    
                    if j > i + 2 and re.match(r"^\d{1,3}\s*/\s*\d{1,3}", sub_line):
                        break

                    # Posición arancelaria si no vino en la primera línea
                    if not pos_aranc and (re.match(r"^[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]{3}[A-Z]?$", sub_line) or re.match(r"^[0-9.]{6,15}[A-Z]?$", sub_line)):
                        pos_aranc = sub_line

                    # FOB Factura
                    if sub_line.upper() == "FOB FACTURA" and j + 1 < n:
                        fob_factura = parse_currency(all_lines_data[j+1]["line"])

                    # Línea Comercial Directa: [DESCRIPCION] [MARCA] [CANT] [UNIDAD] [FOB]
                    m_com = re.search(r"^(.*?)\s+([A-Za-z0-9.\-\s]+?)\s+([0-9.,]+)\s+(UNIDAD|KILOGRAMO|PAR|METRO|LITRO|DOCENA|CAJA|SET|BTO)\s+([0-9.,]+)$", sub_line, re.IGNORECASE)
                    if m_com and not desc:
                        desc = clean_text(m_com.group(1))
                        marca = clean_text(m_com.group(2)) or "Sin Marca"
                        cantidad = parse_currency(m_com.group(3)) or 1.0
                        unidad = m_com.group(4).upper()
                        fob_uss = parse_currency(m_com.group(5))

                    # Bloque Kilo Neto / Países
                    if "KILO NETO" in sub_line.upper() and j + 1 < n and "ESTADO" in all_lines_data[j+1]["line"].upper():
                        k = j + 2
                        if k < n and re.match(r"^[0-9.,]+$", all_lines_data[k]["line"]):
                            k += 1
                        if k + 2 < n:
                            pais_origen = all_lines_data[k]["line"]
                            pais_proc = all_lines_data[k+1]["line"]
                            kilo_neto = parse_currency(all_lines_data[k+2]["line"])

                    j += 1

                if pos_aranc or fob_factura or desc:
                    codigo_producto, descripcion = parse_observation_details(desc or "", marca or "")
                    
                    # Sanear valores astronómicos erróneos
                    val_tot = fob_uss or fob_factura or 0.0
                    if val_tot and val_tot > 5000000.0:
                        val_tot = 0.0

                    extracted_items.append({
                        "numero_item": item_nro,
                        "numero_subitem": None,
                        "codigo_ncm": pos_aranc,
                        "codigo_producto": codigo_producto or None,
                        "descripcion": descripcion or desc or f"Ítem {item_nro} - Posición {pos_aranc or 'N/A'}",
                        "marca": marca or "Sin Marca",
                        "cantidad": cantidad or 1.0,
                        "unidad": unidad or "UNIDAD",
                        "peso_neto": kilo_neto,
                        "peso_bruto": None,
                        "valor_unitario": round(val_tot / (cantidad or 1), 2) if (val_tot and cantidad and cantidad > 0) else None,
                        "valor_total": val_tot,
                        "pais_origen": pais_origen,
                        "pais_procedencia": pais_proc,
                        "pagina_origen": page_num
                    })
                    i = j - 1
            i += 1

    # 3. Si aún no hay items, buscar formato estándar con expresiones regulares
    if not extracted_items:
        main_item_pattern = re.compile(
            r"NRO\s+DE\s+PEDIDO\s*:\s*(\d+)(?:/\d+)?.*?POSICION\s+ARANCELARIA\s*:\s*([0-9A-Za-z.]+)"
            r".*?FOB\s+FACTURA\s*[\n\r\s]*([0-9.,]+)"
            r"(?:.*?DESCRIPCION\s+EN\s+TERMINOS\s+COMERCIALES\s*[\n\r\s]*([^\n\r]+))?"
            r"(?:.*?CANTIDAD\s+UNIDAD\s+FOB\s+U\$S\s*[\n\r\s]*([0-9.,]+)\s*([A-Za-z]+))?",
            re.DOTALL | re.IGNORECASE
        )
        for page_info in pages_data:
            text = page_info["text"]
            page_num = page_info["page_num"]
            main_matches = list(main_item_pattern.finditer(text))
            for m in main_matches:
                nro_item = int(m.group(1))
                pos_aranc = clean_text(m.group(2))
                fob = parse_currency(m.group(3))
                desc = clean_text(m.group(4))
                cant = parse_currency(m.group(5))
                unidad = clean_text(m.group(6)) or "UNIDAD"

                codigo_producto, descripcion = parse_observation_details(desc or "", "")

                extracted_items.append({
                    "numero_item": nro_item,
                    "numero_subitem": None,
                    "codigo_ncm": pos_aranc,
                    "codigo_producto": codigo_producto or None,
                    "descripcion": descripcion or f"Ítem {nro_item} - Posición {pos_aranc}",
                    "marca": "Sin Marca",
                    "cantidad": cant or 1.0,
                    "unidad": unidad,
                    "peso_neto": None,
                    "peso_bruto": None,
                    "valor_unitario": round(fob / cant, 2) if (fob and cant and cant > 0) else None,
                    "valor_total": fob,
                    "pais_origen": None,
                    "pais_procedencia": None,
                    "pagina_origen": page_num
                })

    # 4. Capa de rescate: Búsqueda genérica por Posición Arancelaria (NCM) en páginas
    if not extracted_items:
        item_counter = 1
        for page_info in pages_data:
            text = page_info["text"]
            page_num = page_info["page_num"]
            ncm_matches = re.finditer(r"\b([0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]{3}[A-Za-z]?)\b", text)
            for nm in ncm_matches:
                pos_code = nm.group(1)
                context = text[max(0, nm.start() - 100):min(len(text), nm.end() + 300)]
                fob_match = re.search(r"(?:FOB|FACTURA|VALOR)\s*(?:U\$S|USD)?\s*[:\n\r\s]*([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2}))", context, re.IGNORECASE)
                fob_val = parse_currency(fob_match.group(1)) if fob_match else None

                extracted_items.append({
                    "numero_item": item_counter,
                    "numero_subitem": None,
                    "codigo_ncm": pos_code,
                    "codigo_producto": None,
                    "descripcion": f"Mercadería Posición {pos_code}",
                    "marca": "Sin Marca",
                    "cantidad": 1.0,
                    "unidad": "UNIDAD",
                    "peso_neto": None,
                    "peso_bruto": None,
                    "valor_unitario": fob_val,
                    "valor_total": fob_val,
                    "pais_origen": None,
                    "pais_procedencia": None,
                    "pagina_origen": page_num
                })
                item_counter += 1

    return extracted_items

