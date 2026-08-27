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

def extract_items_from_pages(pages_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Extrae los ítems y subítems de mercancías a lo largo de todas las páginas del PDF.
    Aísla la marca, el código de producto/EAN y la descripción limpia.
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

    # 2. Patrón para Ítems Principales de SOFIA
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

        # Buscar sub-ítems
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

    # Si no se encontraron subitems, buscar ítems principales estándar
    if not extracted_items:
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
                    "cantidad": cant,
                    "unidad": unidad,
                    "peso_neto": None,
                    "peso_bruto": None,
                    "valor_unitario": round(fob / cant, 2) if (fob and cant and cant > 0) else None,
                    "valor_total": fob,
                    "pais_origen": None,
                    "pais_procedencia": None,
                    "pagina_origen": page_num
                })

    return extracted_items
