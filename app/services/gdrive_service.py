import os
import io
import json
import hashlib
import tempfile
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from pathlib import Path

from app.models import Despacho
from app.services.pipeline import process_pdf_file
from app.services.turso_service import TursoService
from app.config import settings

logger = logging.getLogger(__name__)

class GoogleDriveService:
    def __init__(self, folder_id: Optional[str] = None, credentials_file: Optional[str] = None):
        self.folder_id = folder_id or os.getenv("GDRIVE_FOLDER_ID", "1NP6zJHL9w_bV0W1BysIDRIZ5FXZzc5Kv")
        self.credentials_file = credentials_file or os.getenv("GDRIVE_CREDENTIALS_FILE", "./service_account.json")

    @staticmethod
    def is_api_available() -> bool:
        """Verifica si las librerías de Google API están instaladas."""
        try:
            import google.oauth2.service_account
            import googleapiclient.discovery
            return True
        except ImportError:
            return False

    def get_drive_service(self):
        """Inicializa el cliente de Google Drive API usando Service Account si existe el archivo."""
        if not self.is_api_available():
            raise ModuleNotFoundError(
                "Las librerías de Google API no están instaladas en este entorno de Python. "
                "Para activarlo, ejecuta: pip install google-api-python-client google-auth"
            )

        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        cred_path = Path(self.credentials_file)
        if not cred_path.is_absolute():
            cred_path = Path(__file__).resolve().parent.parent.parent / self.credentials_file

        if not cred_path.exists():
            raise FileNotFoundError(
                f"No se encontró el archivo de credenciales de Google Drive en '{cred_path}'. "
                f"Para conectar por API necesitas colocar tu archivo 'service_account.json' en la raíz del proyecto."
            )

        SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
        creds = service_account.Credentials.from_service_account_file(str(cred_path), scopes=SCOPES)
        return build('drive', 'v3', credentials=creds)

    def scan_and_process(
        self,
        db: Session,
        allow_duplicate: bool = False,
        propietario: Optional[str] = None,
        propietario_default: Optional[str] = None
    ) -> Dict[str, Any]:
        """Alias para scan_and_process_folder."""
        owner = propietario or propietario_default or "Google Drive"
        return self.scan_and_process_folder(db=db, propietario_default=owner, allow_duplicate=allow_duplicate)

    def scan_and_process_folder(
        self,
        db: Session,
        propietario_default: str = "Google Drive",
        allow_duplicate: bool = False,
        propietario: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Escanea la carpeta de Google Drive por API, procesa los PDFs nuevos y omite duplicados.
        """
        owner = propietario or propietario_default or "Google Drive"
        drive_service = self.get_drive_service()
        
        # Consultar archivos PDF en la carpeta (soporta Unidades Compartidas y Mi Unidad)
        query = f"'{self.folder_id}' in parents and (mimeType = 'application/pdf' or name contains '.pdf' or name contains '.PDF') and trashed = false"
        results = drive_service.files().list(
            q=query,
            fields="files(id, name, md5Checksum, size, modifiedTime)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=100
        ).execute()

        files = results.get('files', [])
        
        total_encontrados = len(files)
        nuevos_procesados = 0
        omitidos_duplicados = 0
        errores = 0
        detalles = []

        from googleapiclient.http import MediaIoBaseDownload

        for f in files:
            file_id = f['id']
            file_name = f['name'].strip()

            try:
                # 1. Comprobación RÁPIDA por NOMBRE DE ARCHIVO antes de descargar:
                # Si el nombre ya está registrado en la base de datos, se omite de inmediato sin descargar.
                existente_por_nombre = db.query(Despacho).filter(
                    (Despacho.nombre_archivo_original == file_name) |
                    (Despacho.archivo_pdf.like(f"%{file_name}%"))
                ).first()

                # Si el nombre del archivo contiene un código de despacho conocido (ej: 26021ZF2I000919N.pdf)
                if not existente_por_nombre:
                    import re
                    match_num = re.search(r"\b([0-9]{2}[0-9A-Za-z]{10,18})\b", file_name)
                    if match_num:
                        posible_num = match_num.group(1).upper()
                        existente_por_nombre = db.query(Despacho).filter(Despacho.numero_despacho == posible_num).first()

                if existente_por_nombre:
                    omitidos_duplicados += 1
                    detalles.append({
                        "archivo": file_name,
                        "estado": "OMITIDO",
                        "motivo": f"Ya existe por nombre de archivo (Despacho Nº {existente_por_nombre.numero_despacho or existente_por_nombre.id})",
                        "despacho_id": existente_por_nombre.id
                    })
                    continue

                # 2. Si el nombre es nuevo, descargar a archivo temporal para procesar
                request = drive_service.files().get_media(fileId=file_id)
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
                    downloader = MediaIoBaseDownload(tmp_file, request)
                    done = False
                    while not done:
                        status, done = downloader.next_chunk()
                    tmp_path = tmp_file.name

                # 3. Comprobación por HASH criptográfico SHA-256 (por si le cambiaron el nombre pero el contenido es idéntico)
                with open(tmp_path, "rb") as pdf_bytes:
                    file_hash = hashlib.sha256(pdf_bytes.read()).hexdigest()

                existente = db.query(Despacho).filter(Despacho.hash_archivo == file_hash).first()
                if existente:
                    omitidos_duplicados += 1
                    detalles.append({
                        "archivo": file_name,
                        "estado": "OMITIDO",
                        "motivo": f"Ya registrado previamente por contenido idéntico (Despacho Nº {existente.numero_despacho or existente.id})",
                        "despacho_id": existente.id
                    })
                    os.remove(tmp_path)
                    continue

                # Procesar PDF nuevo a través del pipeline aduanero
                despacho, log = process_pdf_file(
                    db=db,
                    file_path=tmp_path,
                    original_filename=file_name,
                    allow_duplicate=False,
                    propietario=propietario_default
                )

                nuevos_procesados += 1
                detalles.append({
                    "archivo": file_name,
                    "estado": "PROCESADO_EXITOSO",
                    "despacho_id": despacho.id,
                    "numero_despacho": despacho.numero_despacho or "S/N",
                    "importador": despacho.importador_nombre or "No identificado",
                    "items_extraidos": len(despacho.items)
                })

                # Disparar notificación (Telegram / Webhook)
                try:
                    from app.services.notification_service import NotificationService
                    noti = NotificationService()
                    desp_dict = {
                        "numero_despacho": despacho.numero_despacho,
                        "importador_nombre": despacho.importador_nombre,
                        "propietario": despacho.propietario,
                        "canal": despacho.canal,
                        "valor_fob": despacho.valor_fob,
                        "valor_cif": despacho.valor_cif,
                        "nombre_archivo_original": file_name
                    }
                    noti.notify_new_despacho(despacho_dict=desp_dict, items_count=len(despacho.items), source="Google Drive Cloud")
                except Exception as n_err:
                    logger.warning(f"No se pudo enviar notificación de nuevo despacho: {n_err}")

                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

            except Exception as err:
                errores += 1
                detalles.append({
                    "archivo": file_name,
                    "estado": "ERROR",
                    "error": str(err)
                })

        # Si hubo despachos nuevos, crear backup automático del sistema
        if nuevos_procesados > 0:
            try:
                from app.services.backup_service import BackupService
                backup_svc = BackupService()
                backup_svc.create_system_backup(reason=f"AUTO_GDRIVE_CLOUD_{nuevos_procesados}_DESPACHOS")
            except Exception as b_err:
                logger.warning(f"No se pudo crear backup automático tras escaneo GDrive Cloud: {b_err}")

        # Si Turso está configurado y hubo despachos nuevos, sincronizar a la nube
        turso = TursoService()
        if nuevos_procesados > 0 and turso.is_configured():
            try:
                for det in detalles:
                    if det.get("estado") in ["PROCESADO", "CONFIRMADO", "PROCESADO_EXITOSO"] and det.get("despacho_id"):
                        turso.sync_push_despacho(det["despacho_id"], db)
                logger.info(f"[GoogleDriveService] {nuevos_procesados} despachos sincronizados automáticamente a Turso Cloud.")
            except Exception as e:
                logger.warning(f"No se pudo sincronizar automáticamente con Turso: {e}")

        return {
            "total_encontrados": total_encontrados,
            "nuevos_procesados": nuevos_procesados,
            "procesados": nuevos_procesados,
            "omitidos_duplicados": omitidos_duplicados,
            "duplicados": omitidos_duplicados,
            "errores": errores,
            "detalles": detalles
        }

    def scan_local_folder(
        self,
        db: Session,
        local_path: str,
        propietario_default: str = "Carpeta Local",
        allow_duplicate: bool = False,
        propietario: Optional[str] = None
    ) -> Dict[str, Any]:
        r"""
        Escanea una carpeta local sincronizada de Google Drive en Windows (ej: J:\My Drive\...).
        """
        owner = propietario or propietario_default or "Carpeta Local"
        folder = Path(local_path)
        if not folder.exists() or not folder.is_dir():
            raise FileNotFoundError(f"La carpeta local '{local_path}' no existe o no es accesible.")

        pdf_files = list(folder.glob("*.pdf")) + list(folder.glob("*.PDF"))
        
        total_encontrados = len(pdf_files)
        nuevos_procesados = 0
        omitidos_duplicados = 0
        errores = 0
        detalles = []

        for p in pdf_files:
            file_name = p.name.strip()
            try:
                # 1. Comprobar primero por Nombre de Archivo
                existente_por_nombre = db.query(Despacho).filter(
                    (Despacho.nombre_archivo_original == file_name) |
                    (Despacho.archivo_pdf.like(f"%{file_name}%"))
                ).first()

                if not existente_por_nombre:
                    import re
                    match_num = re.search(r"\b([0-9]{2}[0-9A-Za-z]{10,18})\b", file_name)
                    if match_num:
                        posible_num = match_num.group(1).upper()
                        existente_por_nombre = db.query(Despacho).filter(Despacho.numero_despacho == posible_num).first()

                if existente_por_nombre:
                    omitidos_duplicados += 1
                    detalles.append({
                        "archivo": file_name,
                        "estado": "OMITIDO",
                        "motivo": f"Ya existe por nombre de archivo (Despacho Nº {existente_por_nombre.numero_despacho or existente_por_nombre.id})",
                        "despacho_id": existente_por_nombre.id
                    })
                    continue

                # 2. Comprobar por Hash SHA-256
                with open(p, "rb") as f:
                    file_hash = hashlib.sha256(f.read()).hexdigest()

                existente = db.query(Despacho).filter(Despacho.hash_archivo == file_hash).first()
                if existente:
                    omitidos_duplicados += 1
                    detalles.append({
                        "archivo": file_name,
                        "estado": "OMITIDO",
                        "motivo": f"Ya registrado previamente por contenido idéntico (Despacho Nº {existente.numero_despacho or existente.id})",
                        "despacho_id": existente.id
                    })
                    continue

                despacho, log = process_pdf_file(
                    db=db,
                    file_path=str(p),
                    original_filename=file_name,
                    allow_duplicate=False,
                    propietario=propietario_default
                )

                nuevos_procesados += 1
                detalles.append({
                    "archivo": file_name,
                    "estado": "PROCESADO_EXITOSO",
                    "despacho_id": despacho.id,
                    "numero_despacho": despacho.numero_despacho or "S/N",
                    "importador": despacho.importador_nombre or "No identificado",
                    "items_extraidos": len(despacho.items)
                })

                # Disparar notificación (Telegram / Webhook)
                try:
                    from app.services.notification_service import NotificationService
                    noti = NotificationService()
                    desp_dict = {
                        "numero_despacho": despacho.numero_despacho,
                        "importador_nombre": despacho.importador_nombre,
                        "propietario": despacho.propietario,
                        "canal": despacho.canal,
                        "valor_fob": despacho.valor_fob,
                        "valor_cif": despacho.valor_cif,
                        "nombre_archivo_original": file_name
                    }
                    noti.notify_new_despacho(despacho_dict=desp_dict, items_count=len(despacho.items), source="Carpeta Local")
                except Exception as n_err:
                    logger.warning(f"No se pudo enviar notificación de nuevo despacho local: {n_err}")

            except Exception as err:
                errores += 1
                detalles.append({
                    "archivo": file_name,
                    "estado": "ERROR",
                    "error": str(err)
                })

        # Si hubo despachos nuevos, crear backup automático del sistema
        if nuevos_procesados > 0:
            try:
                from app.services.backup_service import BackupService
                backup_svc = BackupService()
                backup_svc.create_system_backup(reason=f"AUTO_LOCAL_FOLDER_{nuevos_procesados}_DESPACHOS")
            except Exception as b_err:
                logger.warning(f"No se pudo crear backup automático tras escaneo local: {b_err}")

        # Si Turso está configurado y hubo despachos nuevos, sincronizar a la nube
        turso = TursoService()
        if nuevos_procesados > 0 and turso.is_configured():
            try:
                for det in detalles:
                    if det.get("estado") in ["PROCESADO", "CONFIRMADO", "PROCESADO_EXITOSO"] and det.get("despacho_id"):
                        turso.sync_push_despacho(det["despacho_id"], db)
                logger.info(f"[GoogleDriveService] {nuevos_procesados} despachos sincronizados automáticamente a Turso Cloud.")
            except Exception as e:
                logger.warning(f"No se pudo sincronizar automáticamente con Turso: {e}")

        return {
            "total_encontrados": total_encontrados,
            "nuevos_procesados": nuevos_procesados,
            "procesados": nuevos_procesados,
            "omitidos_duplicados": omitidos_duplicados,
            "duplicados": omitidos_duplicados,
            "errores": errores,
            "detalles": detalles
        }
