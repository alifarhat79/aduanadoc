from datetime import date, datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, Field

class ExtractedFieldDetail(BaseModel):
    valor: Optional[Any] = None
    valor_original: Optional[str] = None
    confidence: float = 1.0
    pagina: int = 1
    texto_origen: Optional[str] = None
    metodo: str = "TEXT"  # TEXT / OCR / MANUAL

class DespachoItemBase(BaseModel):
    numero_item: int = 1
    numero_subitem: Optional[int] = None
    codigo_ncm: Optional[str] = None
    codigo_producto: Optional[str] = None
    descripcion: Optional[str] = None
    marca: Optional[str] = None
    cantidad: Optional[float] = None
    unidad: Optional[str] = None
    peso_neto: Optional[float] = None
    peso_bruto: Optional[float] = None
    valor_unitario: Optional[float] = None
    valor_total: Optional[float] = None
    pais_origen: Optional[str] = None
    pais_procedencia: Optional[str] = None
    pagina_origen: Optional[int] = None

class DespachoItemCreate(DespachoItemBase):
    pass

class DespachoItemResponse(DespachoItemBase):
    id: int
    despacho_id: int
    model_config = ConfigDict(from_attributes=True)

class DespachoAuditoriaResponse(BaseModel):
    id: int
    campo_modificado: str
    valor_anterior: Optional[str] = None
    valor_nuevo: Optional[str] = None
    usuario: str
    fecha_modificacion: datetime
    model_config = ConfigDict(from_attributes=True)

class DespachoBase(BaseModel):
    propietario: Optional[str] = None
    numero_despacho: Optional[str] = None
    numero_declaracion: Optional[str] = None
    referencia: Optional[str] = None
    fecha_despacho: Optional[date] = None
    fecha_registro: Optional[date] = None
    fecha_liberacion: Optional[date] = None

    importador_nombre: Optional[str] = None
    importador_documento: Optional[str] = None
    importador_direccion: Optional[str] = None

    exportador_nombre: Optional[str] = None
    exportador_pais: Optional[str] = None
    exportador_direccion: Optional[str] = None

    despachante_nombre: Optional[str] = None
    despachante_documento: Optional[str] = None
    despachante_empresa: Optional[str] = None

    modalidad_transporte: Optional[str] = None
    bl: Optional[str] = None
    hbl: Optional[str] = None
    mbl: Optional[str] = None
    awb: Optional[str] = None
    contenedor: Optional[str] = None
    buque: Optional[str] = None
    vuelo: Optional[str] = None
    matricula: Optional[str] = None
    empresa_transporte: Optional[str] = None

    puerto_origen: Optional[str] = None
    puerto_destino: Optional[str] = None
    pais_origen: Optional[str] = None
    pais_procedencia: Optional[str] = None

    aduana: Optional[str] = None
    regimen: Optional[str] = None
    canal: Optional[str] = None

    valor_fob: Optional[float] = None
    valor_flete: Optional[float] = None
    valor_seguro: Optional[float] = None
    valor_cif: Optional[float] = None
    valor_imponible: Optional[float] = None
    valor_aduanero: Optional[float] = None
    moneda: Optional[str] = "USD"
    tipo_cambio: Optional[float] = None

    impuesto_importacion: Optional[float] = None
    iva: Optional[float] = None
    otros_impuestos: Optional[float] = None
    total_impuestos: Optional[float] = None
    total_general: Optional[float] = None

    cantidad_bultos: Optional[float] = None
    peso_bruto: Optional[float] = None
    peso_neto: Optional[float] = None

    observaciones: Optional[str] = None
    estado_procesamiento: Optional[str] = "PROCESADO"

class DespachoCreate(DespachoBase):
    archivo_pdf: str
    nombre_archivo_original: str
    hash_archivo: str
    tipo_documento_detectado: Optional[str] = None
    metodo_extraccion: Optional[str] = "TEXT"
    numero_paginas: Optional[int] = 1
    confianza_promedio: Optional[float] = 1.0
    metadata_extraccion: Optional[Dict[str, Any]] = None
    items: Optional[List[DespachoItemCreate]] = []

class DespachoUpdate(BaseModel):
    propietario: Optional[str] = None
    numero_despacho: Optional[str] = None
    numero_declaracion: Optional[str] = None
    referencia: Optional[str] = None
    fecha_despacho: Optional[date] = None
    fecha_registro: Optional[date] = None
    fecha_liberacion: Optional[date] = None

    importador_nombre: Optional[str] = None
    importador_documento: Optional[str] = None
    importador_direccion: Optional[str] = None

    exportador_nombre: Optional[str] = None
    exportador_pais: Optional[str] = None
    exportador_direccion: Optional[str] = None

    despachante_nombre: Optional[str] = None
    despachante_documento: Optional[str] = None

    modalidad_transporte: Optional[str] = None
    bl: Optional[str] = None
    hbl: Optional[str] = None
    mbl: Optional[str] = None
    awb: Optional[str] = None
    contenedor: Optional[str] = None
    buque: Optional[str] = None
    vuelo: Optional[str] = None

    puerto_origen: Optional[str] = None
    puerto_destino: Optional[str] = None
    pais_origen: Optional[str] = None
    pais_procedencia: Optional[str] = None

    aduana: Optional[str] = None
    regimen: Optional[str] = None
    canal: Optional[str] = None

    valor_fob: Optional[float] = None
    valor_flete: Optional[float] = None
    valor_seguro: Optional[float] = None
    valor_cif: Optional[float] = None
    valor_imponible: Optional[float] = None
    valor_aduanero: Optional[float] = None
    moneda: Optional[str] = "USD"
    tipo_cambio: Optional[float] = None

    impuesto_importacion: Optional[float] = None
    iva: Optional[float] = None
    otros_impuestos: Optional[float] = None
    total_impuestos: Optional[float] = None
    total_general: Optional[float] = None

    cantidad_bultos: Optional[float] = None
    peso_bruto: Optional[float] = None
    peso_neto: Optional[float] = None

    observaciones: Optional[str] = None
    estado_procesamiento: Optional[str] = "CONFIRMADO"
    items: Optional[List[DespachoItemCreate]] = None

class DespachoResponse(DespachoBase):
    id: int
    archivo_pdf: str
    nombre_archivo_original: str
    hash_archivo: str
    tipo_documento_detectado: Optional[str] = None
    metodo_extraccion: str
    numero_paginas: int
    confianza_promedio: float
    metadata_extraccion: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime
    items: List[DespachoItemResponse] = []
    auditorias: List[DespachoAuditoriaResponse] = []

    model_config = ConfigDict(from_attributes=True)

class DashboardStats(BaseModel):
    total_despachos: int = 0
    despachos_este_mes: int = 0
    total_cif: float = 0.0
    total_fob: float = 0.0
    total_impuestos: float = 0.0
    total_peso_bruto: float = 0.0
    pendientes_revision: int = 0
    confirmados: int = 0
    moneda_predominante: str = "USD"
