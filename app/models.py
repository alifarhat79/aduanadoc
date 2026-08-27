from datetime import datetime, date, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime, Date, ForeignKey, JSON, Index
)
from sqlalchemy.orm import relationship
from app.database import Base

class Despacho(Base):
    """Modelo principal de la declaración aduanera / despacho."""
    __tablename__ = "despachos"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)

    # Propietario / Dueño asignado
    propietario = Column(String(150), nullable=True, index=True)

    # Identificación
    numero_despacho = Column(String(100), index=True, nullable=True)
    numero_declaracion = Column(String(100), nullable=True)
    referencia = Column(String(100), nullable=True)
    fecha_despacho = Column(Date, nullable=True)
    fecha_registro = Column(Date, nullable=True)
    fecha_liberacion = Column(Date, nullable=True)

    # Importador
    importador_nombre = Column(String(255), nullable=True, index=True)
    importador_documento = Column(String(100), nullable=True)
    importador_direccion = Column(Text, nullable=True)

    # Exportador / Proveedor
    exportador_nombre = Column(String(255), nullable=True)
    exportador_pais = Column(String(100), nullable=True)
    exportador_direccion = Column(Text, nullable=True)

    # Despachante
    despachante_nombre = Column(String(255), nullable=True)
    despachante_documento = Column(String(100), nullable=True)
    despachante_empresa = Column(String(255), nullable=True)

    # Transporte
    modalidad_transporte = Column(String(50), nullable=True)  # MARITIMO, AEREO, TERRESTRE, ZONA_FRANCA
    bl = Column(String(100), nullable=True)
    hbl = Column(String(100), nullable=True)
    mbl = Column(String(100), nullable=True)
    awb = Column(String(100), nullable=True)
    contenedor = Column(String(100), nullable=True)
    buque = Column(String(100), nullable=True)
    vuelo = Column(String(100), nullable=True)
    matricula = Column(String(100), nullable=True)
    empresa_transporte = Column(String(255), nullable=True)

    puerto_origen = Column(String(150), nullable=True)
    puerto_destino = Column(String(150), nullable=True)
    pais_origen = Column(String(100), nullable=True)
    pais_procedencia = Column(String(100), nullable=True)

    aduana = Column(String(150), nullable=True)
    regimen = Column(String(100), nullable=True)
    canal = Column(String(50), nullable=True)  # VERDE, NARANJA, ROJO

    # Financiero
    valor_fob = Column(Float, nullable=True)
    valor_flete = Column(Float, nullable=True)
    valor_seguro = Column(Float, nullable=True)
    valor_cif = Column(Float, nullable=True)
    valor_imponible = Column(Float, nullable=True)
    valor_aduanero = Column(Float, nullable=True)
    moneda = Column(String(10), default="USD")
    tipo_cambio = Column(Float, nullable=True)

    # Tributos
    impuesto_importacion = Column(Float, nullable=True)
    iva = Column(Float, nullable=True)
    otros_impuestos = Column(Float, nullable=True)
    total_impuestos = Column(Float, nullable=True)
    total_general = Column(Float, nullable=True)

    # Pesos y bultos
    cantidad_bultos = Column(Float, nullable=True)
    peso_bruto = Column(Float, nullable=True)
    peso_neto = Column(Float, nullable=True)

    observaciones = Column(Text, nullable=True)

    # Archivo original y metadatos técnicos
    archivo_pdf = Column(String(500), nullable=False)
    nombre_archivo_original = Column(String(255), nullable=False)
    hash_archivo = Column(String(64), index=True, nullable=False)  # SHA-256
    tipo_documento_detectado = Column(String(100), nullable=True)
    metodo_extraccion = Column(String(50), default="TEXT")  # TEXT, OCR, MIXTO
    numero_paginas = Column(Integer, default=1)

    # Estados: PROCESANDO, PROCESADO, REVISAR, CONFIRMADO, ERROR, DUPLICADO
    estado_procesamiento = Column(String(50), default="PROCESADO", index=True)
    confianza_promedio = Column(Float, default=1.0)
    metadata_extraccion = Column(JSON, default=dict)  # Detalle por campo: confianza, pagina, texto_origen

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relaciones
    items = relationship("DespachoItem", back_populates="despacho", cascade="all, delete-orphan")
    auditorias = relationship("DespachoAuditoria", back_populates="despacho", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_despacho_unique_lookup", "numero_despacho", "fecha_despacho", "importador_documento"),
    )


class DespachoItem(Base):
    """Ítems de mercancías pertenecientes a un despacho."""
    __tablename__ = "despacho_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    despacho_id = Column(Integer, ForeignKey("despachos.id", ondelete="CASCADE"), nullable=False)

    numero_item = Column(Integer, nullable=False, default=1)
    numero_subitem = Column(Integer, nullable=True)
    codigo_ncm = Column(String(50), nullable=True)
    codigo_producto = Column(String(100), nullable=True)  # Código EAN / SKU / Referencia
    descripcion = Column(Text, nullable=True)
    marca = Column(String(150), nullable=True)
    cantidad = Column(Float, nullable=True)
    unidad = Column(String(50), nullable=True)
    peso_neto = Column(Float, nullable=True)
    peso_bruto = Column(Float, nullable=True)
    valor_unitario = Column(Float, nullable=True)
    valor_total = Column(Float, nullable=True)
    pais_origen = Column(String(100), nullable=True)
    pais_procedencia = Column(String(100), nullable=True)
    pagina_origen = Column(Integer, nullable=True)

    despacho = relationship("Despacho", back_populates="items")


class DespachoAuditoria(Base):
    """Trazabilidad y registro de cambios manuales realizados por el usuario."""
    __tablename__ = "despacho_auditoria"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    despacho_id = Column(Integer, ForeignKey("despachos.id", ondelete="CASCADE"), nullable=False)

    campo_modificado = Column(String(100), nullable=False)
    valor_anterior = Column(Text, nullable=True)
    valor_nuevo = Column(Text, nullable=True)
    usuario = Column(String(100), default="Operador")
    fecha_modificacion = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    despacho = relationship("Despacho", back_populates="auditorias")


class ProcessingLog(Base):
    """Registro técnico detallado de cada procesamiento de archivo."""
    __tablename__ = "processing_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre_archivo = Column(String(255), nullable=False)
    hash_archivo = Column(String(64), nullable=False)
    metodo = Column(String(50), nullable=False)  # TEXT / OCR / MIXTO
    numero_paginas = Column(Integer, default=1)
    tiempo_ms = Column(Integer, default=0)
    campos_identificados = Column(Integer, default=0)
    campos_no_identificados = Column(Integer, default=0)
    confianza_promedio = Column(Float, default=0.0)
    errores = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
