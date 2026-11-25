import os
import io
import json
import hashlib
import tempfile
import logging
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from pathlib import Path

from app.models import Despacho
from app.services.candado_procesos import candado_entre_procesos
from app.services.pipeline import process_pdf_file
from app.services.turso_service import TursoService
from app.config import settings

logger = logging.getLogger(__name__)


@contextmanager
def _candados_escaneo(espera: float):
    """Candado de hilos (este proceso) + candado entre procesos, con espera máxima total."""
    inicio = time.monotonic()
    if not _SCAN_LOCK.acquire(timeout=max(0.0, espera)):
        yield False
        return
    try:
        restante = max(0.0, espera - (time.monotonic() - inicio))
        with candado_entre_procesos("escaneo_pdfs", espera_max=restante) as obtenido:
            yield obtenido
    finally:
        _SCAN_LOCK.release()


def _refrescar_sesion(db: Session) -> None:
    """
    Tras esperar el candado, otro proceso pudo haber guardado despachos: se cierra la lectura en curso
    (si no hay cambios pendientes) para que las comprobaciones de duplicados vean esos datos nuevos.
    """
    if not (db.new or db.dirty or db.deleted):
        db.rollback()
    db.expire_all()

# Candado global: evita que el vigilante automático y la sincronización manual procesen el mismo PDF en paralelo
_SCAN_LOCK = threading.Lock()

# Esperas antes de reintentar automáticamente un PDF que falló (luego, una vez por día).
# Antes se volvía a descargar y procesar cada 60 segundos para siempre.
_ESPERAS_REINTENTO = (timedelta(minutes=5), timedelta(hours=1), timedelta(hours=6), timedelta(hours=24))


def _resultado_vacio(aviso: str) -> Dict[str, Any]:
    return {"total_encontrados": 0, "nuevos_procesados": 0, "procesados": 0, "omitidos_duplicados": 0,
            "duplicados": 0, "errores": 0, "detalles": [], "aviso": aviso}


class GoogleDriveService:
    # Archivos que fallaron al procesarse en esta sesión (nombre -> {error, firma, intentos, proximo})
    failed_files: Dict[str, Any] = {}

    @classmethod
    def _registrar_fallo(cls, file_name: str, firma: Optional[str], err: Exception) -> None:
        previo = cls.failed_files.get(file_name)
        intentos = (previo.get("intentos", 0) if isinstance(previo, dict) and previo.get("firma") == firma else 0) + 1
        espera = _ESPERAS_REINTENTO[min(intentos, len(_ESPERAS_REINTENTO)) - 1]
        cls.failed_files[file_name] = {"error": str(err), "firma": firma, "intentos": intentos,
                                       "proximo": datetime.now() + espera}

    @classmethod
    def _omitir_por_fallo_previo(cls, file_name: str, firma: Optional[str]) -> Optional[Dict[str, Any]]:
        """Devuelve el fallo anterior si todavía no corresponde reintentar (mismo archivo, espera vigente)."""
        info = cls.failed_files.get(file_name)
        if info is None:
            return None
        if not isinstance(info, dict):
            return {"error": str(info)}
        if firma and info.get("firma") and firma != info.get("firma"):
            return None  # el archivo cambió en Drive: se reintenta enseguida
        if info.get("proximo") and datetime.now() >= info["proximo"]:
            return None
        return info

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

    def _list_pdf_files(self, drive_service) -> List[Dict[str, Any]]:
        """Lista todos los PDFs de la carpeta de Google Drive (con paginación)."""
        query = f"'{self.folder_id}' in parents and (mimeType = 'application/pdf' or name contains '.pdf' or name contains '.PDF') and trashed = false"
        files = []
        page_token = None
        while True:
            results = drive_service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, md5Checksum, size, modifiedTime)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                pageSize=500,
                pageToken=page_token
            ).execute()
            files.extend(results.get('files', []))
            page_token = results.get('nextPageToken')
            if not page_token:
                break
        return files

    @staticmethod
    def _find_existing_by_name(db: Session, file_name: str) -> Optional[Despacho]:
        """Busca un despacho ya registrado por nombre de archivo o por número de despacho contenido en el nombre."""
        # El pipeline guarda el PDF como "<timestamp>_<hash8>_<nombre original>".
        # Se compara el FINAL exacto de la ruta (con comodines escapados). Antes se usaba
        # LIKE '%nombre%': "_" actuaba como comodín y un nombre que fuera sufijo de otro
        # (p. ej. "1.pdf" dentro de "..._11.pdf") se omitía para siempre como "ya existente".
        existente = db.query(Despacho).filter(
            (Despacho.nombre_archivo_original == file_name) |
            (Despacho.archivo_pdf == file_name) |
            Despacho.archivo_pdf.endswith(f"_{file_name}", autoescape=True) |
            Despacho.archivo_pdf.endswith(f"/{file_name}", autoescape=True) |
            Despacho.archivo_pdf.endswith(f"\\{file_name}", autoescape=True)
        ).first()
        if not existente:
            match_num = re.search(r"\b([0-9]{2}[0-9A-Za-z]{10,18})\b", file_name)
            if match_num:
                existente = db.query(Despacho).filter(Despacho.numero_despacho == match_num.group(1).upper()).first()
        return existente

    def detect_new_files(self, db: Session) -> List[str]:
        """
        Detección RÁPIDA (sin descargar) de PDFs en Google Drive que aún no están en la base.
        Excluye los archivos que ya fallaron al procesarse para no reabrir el modal en bucle.
        """
        drive_service = self.get_drive_service()
        nuevos = []
        for f in self._list_pdf_files(drive_service):
            name = f['name'].strip()
            if name in GoogleDriveService.failed_files:
                continue
            if not self._find_existing_by_name(db, name):
                nuevos.append(name)
        return nuevos

    @staticmethod
    def is_scanning() -> bool:
        return _SCAN_LOCK.locked()

    def scan_and_process_folder(
        self,
        db: Session,
        propietario_default: str = "Google Drive",
        allow_duplicate: bool = False,
        propietario: Optional[str] = None,
        reintentar_fallidos: bool = False,
        espera_candado: float = 0.0
    ) -> Dict[str, Any]:
        """
        Escanea la carpeta de Google Drive por API, procesa los PDFs nuevos y omite duplicados.
        Candados: de hilos (vigilante y sincronización manual del mismo servidor) y entre procesos
        (servidor web y vigilante en segundo plano), para que nunca procesen el mismo PDF a la vez.
        """
        with _candados_escaneo(espera_candado) as obtenido:
            if not obtenido:
                return _resultado_vacio("Otro proceso de AduanaDoc está importando PDFs en esta PC; "
                                        "se revisará en el próximo ciclo.")
            _refrescar_sesion(db)
            return self._scan_and_process_folder_locked(db, propietario_default, propietario, reintentar_fallidos)

    def _scan_and_process_folder_locked(
        self,
        db: Session,
        propietario_default: str,
        propietario: Optional[str],
        reintentar_fallidos: bool = False
    ) -> Dict[str, Any]:
        owner = propietario or propietario_default or "Google Drive"
        drive_service = self.get_drive_service()
        
        # Consultar archivos PDF en la carpeta con paginación completa (soporta Unidades Compartidas y Mi Unidad)
        files = self._list_pdf_files(drive_service)
        
        total_encontrados = len(files)
        nuevos_procesados = 0
        omitidos_duplicados = 0
        errores = 0
        detalles = []

        from googleapiclient.http import MediaIoBaseDownload

        for f in files:
            file_id = f['id']
            file_name = f['name'].strip()
            firma = f.get('md5Checksum') or f.get('modifiedTime') or f.get('size')
            tmp_path = None

            try:
                # 1. Comprobación RÁPIDA por NOMBRE DE ARCHIVO (o número de despacho) antes de descargar
                existente_por_nombre = self._find_existing_by_name(db, file_name)
                if existente_por_nombre:
                    omitidos_duplicados += 1
                    detalles.append({
                        "archivo": file_name,
                        "estado": "OMITIDO",
                        "motivo": f"Ya existe por nombre de archivo (Despacho Nº {existente_por_nombre.numero_despacho or existente_por_nombre.id})",
                        "despacho_id": existente_por_nombre.id
                    })
                    continue

                # 1b. Un PDF que ya falló no se vuelve a descargar en cada ciclo: se espera a que cambie
                #     en Drive o a que venza su espera (la sincronización manual lo reintenta siempre).
                fallo_previo = None if reintentar_fallidos else self._omitir_por_fallo_previo(file_name, firma)
                if fallo_previo is not None:
                    detalles.append({
                        "archivo": file_name,
                        "estado": "OMITIDO_ERROR_PREVIO",
                        "motivo": f"Falló antes ({fallo_previo.get('error', '')[:120]}); se reintentará más tarde",
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
                    propietario=owner
                )

                nuevos_procesados += 1
                GoogleDriveService.failed_files.pop(file_name, None)
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

                # Disparar notificación nativa de Windows (Toast / Balloon)
                try:
                    from app.services.windows_notification_service import WindowsNotificationService
                    WindowsNotificationService.notify_new_despacho(
                        numero_despacho=despacho.numero_despacho or file_name,
                        importador=despacho.importador_nombre
                    )
                except Exception as w_err:
                    logger.warning(f"No se pudo enviar notificación de Windows: {w_err}")

                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

            except Exception as err:
                errores += 1
                GoogleDriveService._registrar_fallo(file_name, firma, err)
                logger.error(f"[GoogleDriveService] Error procesando '{file_name}': {err}")
                try:
                    db.rollback()
                except Exception:
                    pass
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
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
        propietario: Optional[str] = None,
        espera_candado: float = 120.0
    ) -> Dict[str, Any]:
        r"""
        Escanea una carpeta local sincronizada de Google Drive en Windows (ej: J:\My Drive\...).
        Usa los mismos candados que el escaneo por API para no procesar un PDF dos veces.
        """
        if not Path(local_path).is_dir():
            raise FileNotFoundError(f"La carpeta local '{local_path}' no existe o no es accesible.")
        with _candados_escaneo(espera_candado) as obtenido:
            if not obtenido:
                return _resultado_vacio("Otro proceso de AduanaDoc está importando PDFs en esta PC; "
                                        "intente de nuevo en unos minutos.")
            _refrescar_sesion(db)
            return self._scan_local_folder_locked(db, local_path, propietario_default, propietario)

    def _scan_local_folder_locked(
        self,
        db: Session,
        local_path: str,
        propietario_default: str,
        propietario: Optional[str]
    ) -> Dict[str, Any]:
        owner = propietario or propietario_default or "Carpeta Local"
        folder = Path(local_path)
        if not folder.exists() or not folder.is_dir():
            raise FileNotFoundError(f"La carpeta local '{local_path}' no existe o no es accesible.")

        # En Windows glob no distingue mayúsculas: "*.pdf" + "*.PDF" listaba cada archivo DOS veces
        # (inflando el conteo de duplicados). Se filtra una sola vez por extensión.
        pdf_files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")

        total_encontrados = len(pdf_files)
        nuevos_procesados = 0
        omitidos_duplicados = 0
        errores = 0
        detalles = []

        for p in pdf_files:
            file_name = p.name.strip()
            try:
                # 1. Comprobar primero por Nombre de Archivo (o número de despacho en el nombre)
                existente_por_nombre = self._find_existing_by_name(db, file_name)

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
                    propietario=owner
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
                logger.error(f"[GoogleDriveService] Error procesando local '{file_name}': {err}")
                # Sin rollback, un error de base dejaba la sesión inválida y TODOS los archivos
                # siguientes fallaban con "transaction has been rolled back".
                try:
                    db.rollback()
                except Exception:
                    pass
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
