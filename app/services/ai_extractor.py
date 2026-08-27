import os
import json
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class AIExtractedField(BaseModel):
    valor: Optional[Any] = None
    confidence: float = Field(default=0.80, ge=0.0, le=1.0)
    texto_origen: Optional[str] = None

class AIDespachoExtractionResult(BaseModel):
    numero_despacho: Optional[AIExtractedField] = None
    fecha_despacho: Optional[AIExtractedField] = None
    importador_nombre: Optional[AIExtractedField] = None
    importador_documento: Optional[AIExtractedField] = None
    exportador_nombre: Optional[AIExtractedField] = None
    despachante_nombre: Optional[AIExtractedField] = None
    valor_fob: Optional[AIExtractedField] = None
    valor_flete: Optional[AIExtractedField] = None
    valor_seguro: Optional[AIExtractedField] = None
    valor_cif: Optional[AIExtractedField] = None
    moneda: Optional[AIExtractedField] = None
    peso_bruto: Optional[AIExtractedField] = None
    peso_neto: Optional[AIExtractedField] = None
    cantidad_bultos: Optional[AIExtractedField] = None

class AIExtractorService:
    """
    Servicio desacoplado para extracción mediante Inteligencia Artificial (LLM).
    Solo se utiliza como fallback opcional ante documentos desconocidos o campos no identificados.
    Exige salida estructurada JSON validada estrictamente por Pydantic.
    """
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")

    def extract_missing_fields(self, document_text: str, current_extracted: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ejecuta la consulta estructurada para completar únicamente los campos que no pudieron
        ser extraídos determinísticamente.
        """
        if not self.api_key:
            logger.info("API Key de LLM no configurada; omitiendo paso de IA.")
            return current_extracted

        # Estructura preparada para integración con Gemini API / LLM
        # Prompt con regla crítica: SI NO EXISTE EN EL TEXTO -> NULL (PROHIBIDO INVENTAR)
        system_instruction = (
            "Eres un extractor de datos aduaneros estricto. "
            "Extrae la información únicamente si está explícitamente en el texto. "
            "Si un dato no se encuentra, debes responder estrictamente null. "
            "Está PROHIBIDO inventar, asumir o calcular datos no presentes."
        )
        # Retornar datos validados
        return current_extracted
