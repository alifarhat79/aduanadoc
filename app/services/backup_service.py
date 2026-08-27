import os
import io
import time
import zipfile
import logging
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from app.config import settings, BASE_DIR

logger = logging.getLogger(__name__)

class BackupService:
    def __init__(self, backup_dir: Optional[str] = None):
        self.backup_dir = Path(backup_dir or settings.BACKUP_DIR)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create_system_backup(
        self,
        reason: str = "MANUAL",
        include_db: bool = True,
        include_uploads: bool = True,
        include_code: bool = True
    ) -> Dict[str, Any]:
        """
        Crea un archivo ZIP con el respaldo completo del sistema:
        - Base de datos SQLite (data/despachos.db)
        - Archivos PDF subidos (uploads/)
        - Código de la aplicación (app/)
        - Configuración y dependencias (requirements.txt, iniciar_app.bat, etc.)
        """
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_filename = f"backup_aduanadoc_{timestamp_str}.zip"
        zip_filepath = self.backup_dir / zip_filename

        # Exclusiones seguras
        excluded_dirs = {
            "__pycache__", ".pytest_cache", ".git", ".venv", "venv",
            "backups", "updates", ".system_generated", "tmp"
        }
        excluded_extensions = {".pyc", ".pyo", ".pyd", ".tmp", ".log"}

        try:
            with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
                
                # 1. Base de datos SQLite
                if include_db:
                    db_path = Path(settings.DATA_DIR) / "despachos.db"
                    if db_path.exists():
                        # Usar SQLite backup API para consistencia transaccional
                        temp_db_copy = self.backup_dir / f"temp_backup_{timestamp_str}.db"
                        try:
                            source_conn = sqlite3.connect(str(db_path))
                            dest_conn = sqlite3.connect(str(temp_db_copy))
                            with dest_conn:
                                source_conn.backup(dest_conn)
                            dest_conn.close()
                            source_conn.close()

                            zipf.write(temp_db_copy, arcname="data/despachos.db")
                        finally:
                            if temp_db_copy.exists():
                                temp_db_copy.unlink(missing_ok=True)

                # 2. Archivos PDF subidos
                if include_uploads:
                    uploads_path = Path(settings.UPLOAD_DIR).resolve()
                    if uploads_path.exists():
                        for root, dirs, files in os.walk(uploads_path):
                            dirs[:] = [d for d in dirs if d not in excluded_dirs]
                            for file in files:
                                if not any(file.endswith(ext) for ext in excluded_extensions):
                                    full_path = (Path(root) / file).resolve()
                                    try:
                                        rel_path = full_path.relative_to(BASE_DIR.resolve())
                                    except ValueError:
                                        rel_path = Path("uploads") / full_path.relative_to(uploads_path)
                                    zipf.write(full_path, arcname=str(rel_path).replace("\\", "/"))

                # 3. Código fuente y configuración
                if include_code:
                    for folder_name in ["app"]:
                        folder_path = (BASE_DIR / folder_name).resolve()
                        if folder_path.exists():
                            for root, dirs, files in os.walk(folder_path):
                                dirs[:] = [d for d in dirs if d not in excluded_dirs]
                                for file in files:
                                    if not any(file.endswith(ext) for ext in excluded_extensions):
                                        full_path = (Path(root) / file).resolve()
                                        rel_path = full_path.relative_to(BASE_DIR.resolve())
                                        zipf.write(full_path, arcname=str(rel_path).replace("\\", "/"))

                    # Archivos clave en la raíz
                    root_files = ["requirements.txt", "iniciar_app.bat", "iniciar.bat", "README.md"]
                    for rf in root_files:
                        rf_path = (BASE_DIR / rf).resolve()
                        if rf_path.exists():
                            zipf.write(rf_path, arcname=rf)

                # Metadato de respaldo dentro del ZIP
                manifest = {
                    "backup_name": zip_filename,
                    "created_at": datetime.now().isoformat(),
                    "reason": reason,
                    "app_version": settings.APP_VERSION,
                    "system_title": settings.APP_TITLE
                }
                import json
                zipf.writestr("backup_manifest.json", json.dumps(manifest, indent=2))

            # Tamaño del archivo
            file_size_bytes = zip_filepath.stat().st_size
            file_size_mb = round(file_size_bytes / (1024 * 1024), 2)

            # Intentar subida opcional a Google Drive API si está configurado
            drive_uploaded = self._try_upload_to_gdrive(zip_filepath)

            # Limpiar backups antiguos (mantener últimos N según configuración)
            max_keep = getattr(settings, "BACKUP_MAX_KEEP", 3)
            self._cleanup_old_backups(max_keep=max_keep)

            logger.info(f"[BackupService] Backup creado con éxito: {zip_filename} ({file_size_mb} MB) [Motivo: {reason}]")

            return {
                "success": True,
                "filename": zip_filename,
                "path": str(zip_filepath),
                "size_mb": file_size_mb,
                "size_bytes": file_size_bytes,
                "created_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                "reason": reason,
                "drive_uploaded": drive_uploaded
            }

        except Exception as e:
            logger.error(f"[BackupService] Error al generar backup: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    def list_backups(self) -> List[Dict[str, Any]]:
        """Lista todos los archivos de respaldo disponibles en el directorio backups."""
        backups = []
        if not self.backup_dir.exists():
            return []

        for p in sorted(self.backup_dir.glob("backup_aduanadoc_*.zip"), key=os.path.getmtime, reverse=True):
            stat = p.stat()
            size_mb = round(stat.st_size / (1024 * 1024), 2)
            backups.append({
                "filename": p.name,
                "size_mb": size_mb,
                "size_formatted": f"{size_mb} MB" if size_mb >= 0.1 else f"{round(stat.st_size / 1024, 1)} KB",
                "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%d/%m/%Y %H:%M:%S"),
                "download_url": f"/api/backup/download/{p.name}"
            })

        return backups

    def get_backup_path(self, filename: str) -> Optional[Path]:
        """Obtiene la ruta segura de un archivo de backup para descarga."""
        # Evitar path traversal
        clean_name = Path(filename).name
        if not clean_name.startswith("backup_aduanadoc_") or not clean_name.endswith(".zip"):
            return None

        target = self.backup_dir / clean_name
        return target if target.exists() else None

    def get_backup_file_path(self, filename: str) -> Optional[Path]:
        """Alias de compatibilidad para get_backup_path."""
        return self.get_backup_path(filename)


    def delete_backup(self, filename: str) -> bool:
        """Elimina un archivo de backup."""
        target = self.get_backup_path(filename)
        if target and target.exists():
            target.unlink(missing_ok=True)
            return True
        return False

    def _cleanup_old_backups(self, max_keep: Optional[int] = None):
        """Elimina los backups más antiguos si se excede la cantidad máxima (por defecto 3)."""
        limit = max_keep if max_keep is not None else getattr(settings, "BACKUP_MAX_KEEP", 3)
        all_backups = sorted(self.backup_dir.glob("backup_aduanadoc_*.zip"), key=os.path.getmtime, reverse=True)
        if len(all_backups) > limit:
            for old_p in all_backups[limit:]:
                try:
                    old_p.unlink(missing_ok=True)
                except Exception:
                    pass

    def cleanup_old_backups(self, max_keep: Optional[int] = None) -> int:
        """Método público para purgar respaldos antiguos."""
        limit = max_keep if max_keep is not None else getattr(settings, "BACKUP_MAX_KEEP", 3)
        all_backups = sorted(self.backup_dir.glob("backup_aduanadoc_*.zip"), key=os.path.getmtime, reverse=True)
        deleted = 0
        if len(all_backups) > limit:
            for old_p in all_backups[limit:]:
                try:
                    old_p.unlink(missing_ok=True)
                    deleted += 1
                except Exception:
                    pass
        return deleted

    def _try_upload_to_gdrive(self, zip_filepath: Path) -> bool:
        """Intenta subir el backup a Google Drive si las credenciales tienen permisos."""
        try:
            from app.services.gdrive_service import GoogleDriveService
            gdrive = GoogleDriveService()
            drive_service = gdrive.get_drive_service()
            
            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(str(zip_filepath), mimetype='application/zip', resumable=True)
            file_metadata = {
                'name': zip_filepath.name,
                'parents': [gdrive.folder_id]
            }
            drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name',
                supportsAllDrives=True
            ).execute()
            return True
        except Exception as err:
            logger.debug(f"[BackupService] Subida directa por API omitida ({err}). El archivo queda respaldado en backups/ (sincronizado por Google Drive Desktop).")
            return False
