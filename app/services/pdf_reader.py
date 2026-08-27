import os
import pymupdf
from typing import List, Dict, Any, Tuple
from app.services.ocr_service import perform_ocr_on_page
from app.config import settings

MIN_TEXT_CHARS_THRESHOLD = 35  # Umbral para decidir si una página necesita OCR

def extract_pdf_pages(file_path: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    Abre un archivo PDF y extrae el contenido de cada página.
    Determina si la página contiene texto seleccionable o si requiere OCR.
    
    Retorna:
        - Lista de diccionarios por página con:
            - page_num (1-indexed)
            - text: texto extraído
            - method: 'DIGITAL_TEXT' o 'OCR'
            - char_count: cantidad de caracteres
        - Método global predominante ('DIGITAL_TEXT', 'OCR', 'MIXTO')
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Archivo no encontrado: {file_path}")

    pages_data: List[Dict[str, Any]] = []
    doc = pymupdf.open(file_path)
    total_pages = len(doc)
    
    ocr_count = 0
    digital_count = 0

    try:
        for page_idx in range(total_pages):
            page = doc[page_idx]
            text = page.get_text("text") or ""
            char_count = len(text.strip())
            
            method = "DIGITAL_TEXT"
            if char_count < MIN_TEXT_CHARS_THRESHOLD:
                # La página tiene poco o ningún texto digital -> Intentar OCR si está habilitado
                if settings.OCR_ENABLED:
                    ocr_text = perform_ocr_on_page(page)
                    if len(ocr_text.strip()) > char_count:
                        text = ocr_text
                        method = "OCR"
                        ocr_count += 1
                    else:
                        digital_count += 1
                else:
                    digital_count += 1
            else:
                digital_count += 1

            pages_data.append({
                "page_num": page_idx + 1,
                "text": text,
                "method": method,
                "char_count": len(text.strip())
            })
    finally:
        doc.close()

    if ocr_count == total_pages:
        global_method = "OCR"
    elif ocr_count > 0:
        global_method = "MIXTO"
    else:
        global_method = "DIGITAL_TEXT"

    return pages_data, global_method
