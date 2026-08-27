import hashlib
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from app.models import Despacho

def calculate_sha256(file_path: str) -> str:
    """Calcula el hash SHA-256 de un archivo en disco."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def calculate_bytes_sha256(content: bytes) -> str:
    """Calcula el hash SHA-256 de un buffer en memoria."""
    return hashlib.sha256(content).hexdigest()

def check_duplicate(db: Session, file_hash: str, numero_despacho: Optional[str] = None, fecha_despacho = None, importador_doc: Optional[str] = None) -> Tuple[bool, Optional[str], Optional[int]]:
    """
    Verifica si un documento o despacho ya existe en la base de datos.
    Retorna: (es_duplicado, motivo, despacho_id_existente)
    """
    # 1. Chequeo por hash exacto del archivo
    existing_by_hash = db.query(Despacho).filter(Despacho.hash_archivo == file_hash).first()
    if existing_by_hash:
        return True, f"Archivo idéntico ya registrado previamente (ID: {existing_by_hash.id}, Despacho: {existing_by_hash.numero_despacho})", existing_by_hash.id

    # 2. Chequeo por clave de negocio si los campos están disponibles
    if numero_despacho and importador_doc and fecha_despacho:
        existing_by_fields = db.query(Despacho).filter(
            Despacho.numero_despacho == numero_despacho,
            Despacho.fecha_despacho == fecha_despacho,
            Despacho.importador_documento == importador_doc
        ).first()
        if existing_by_fields:
            return True, f"Posible despacho ya registrado con mismo Nº {numero_despacho}, fecha y RUC (ID: {existing_by_fields.id})", existing_by_fields.id

    return False, None, None
