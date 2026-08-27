import io
import logging
from typing import Optional
import pymupdf
from PIL import Image

try:
    import pytesseract
    PYTESSERACT_AVAILABLE = True
except ImportError:
    PYTESSERACT_AVAILABLE = False

try:
    import cv2
    import numpy as np
    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False

from app.config import settings

logger = logging.getLogger(__name__)

if settings.TESSERACT_CMD and PYTESSERACT_AVAILABLE:
    pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD

def preprocess_image_for_ocr(pil_img: Image.Image) -> Image.Image:
    """Aplica escala de grises, aumento de contraste y umbralización adaptativa para OCR."""
    if not OPENCV_AVAILABLE:
        return pil_img.convert("L")

    try:
        # Convertir PIL a NumPy (BGR)
        img_np = np.array(pil_img)
        if len(img_np.shape) == 3:
            gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
        else:
            gray = img_np

        # Denoising y aumento de contraste
        # Binarización Otsu
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return Image.fromarray(thresh)
    except Exception as e:
        logger.warning(f"Error en preprocesamiento OpenCV: {e}")
        return pil_img

def perform_ocr_on_page(page: pymupdf.Page) -> str:
    """Renderiza la página del PDF a 300 DPI y ejecuta OCR."""
    if not PYTESSERACT_AVAILABLE:
        logger.warning("Pytesseract no está disponible.")
        return ""

    try:
        # Render a 300 DPI (zoom 300/72 ≈ 4.166)
        zoom = 300 / 72
        mat = pymupdf.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")
        pil_img = Image.open(io.BytesIO(img_bytes))

        # Preprocesamiento
        processed_img = preprocess_image_for_ocr(pil_img)

        # Ejecución OCR
        ocr_lang = settings.OCR_LANGUAGE or "spa+eng"
        text = pytesseract.image_to_string(processed_img, lang=ocr_lang)
        return text or ""
    except Exception as e:
        logger.error(f"Fallo al ejecutar OCR en página {page.number + 1}: {e}")
        return ""
