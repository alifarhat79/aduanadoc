import os
import io
import json
import shutil
import zipfile
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from app.config import settings, BASE_DIR
from app.services.backup_service import BackupService

logger = logging.getLogger(__name__)

class UpdaterService:
    def __init__(self, updates_dir: Optional[str] = None):
        self.updates_dir = Path(updates_dir or settings.UPDATES_DIR)
        self.updates_dir.mkdir(parents=True, exist_ok=True)
        self.version_file = self.updates_dir / "version.json"
        self.update_zip_file = self.updates_dir / "update_latest.zip"

    def publish_update(self, new_version: str, changelog: str = "") -> Dict[str, Any]:
        """
        Para el PROGRAMADOR: Empaqueta la versión actual del código en un archivo de actualización
        y genera el manifiesto version.json en la carpeta compartida / Google Drive.
        """
        new_version = new_version.strip()
        if not new_version:
            raise ValueError("El número de versión no puede estar vacío.")

        # Exclusiones estrictas para no incluir datos de usuario ni temporales
        excluded_dirs = {
            "__pycache__", ".pytest_cache", ".git", ".venv", "venv",
            "backups", "updates", "data", "uploads", ".system_generated", "tmp"
        }
        excluded_files = {
            ".env", "service_account.json", "despachos.db"
        }
        excluded_extensions = {".pyc", ".pyo", ".pyd", ".tmp", ".log", ".db"}

        try:
            # 1. Crear el ZIP de actualización
            with zipfile.ZipFile(self.update_zip_file, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
                
                # Empaquetar todo el directorio app/
                app_dir = BASE_DIR / "app"
                if app_dir.exists():
                    for root, dirs, files in os.walk(app_dir):
                        dirs[:] = [d for d in dirs if d not in excluded_dirs]
                        for file in files:
                            if file not in excluded_files and not any(file.endswith(ext) for ext in excluded_extensions):
                                full_path = Path(root) / file
                                rel_path = full_path.relative_to(BASE_DIR)
                                zipf.write(full_path, arcname=str(rel_path).replace("\\", "/"))

                # Empaquetar launchers y dependencias
                root_files = ["requirements.txt", "iniciar_app.bat", "iniciar.bat", "README.md", "CONTINUACION_ANTIGRAVITY.md"]
                for rf in root_files:
                    rf_path = BASE_DIR / rf
                    if rf_path.exists():
                        zipf.write(rf_path, arcname=rf)

            # 2. Calcular Hash SHA-256 y Tamaño
            file_size_bytes = self.update_zip_file.stat().st_size
            with open(self.update_zip_file, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()

            # 3. Crear Manifiesto version.json
            manifest = {
                "version": new_version,
                "previous_version": settings.APP_VERSION,
                "released_at": datetime.now().isoformat(),
                "changelog": changelog or f"Actualización a la versión {new_version}",
                "update_file": "update_latest.zip",
                "sha256": file_hash,
                "file_size_bytes": file_size_bytes,
                "file_size_mb": round(file_size_bytes / (1024 * 1024), 2)
            }

            with open(self.version_file, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)

            # 4. Actualizar versión en el archivo .env local del programador
            self._update_env_version(new_version)
            settings.APP_VERSION = new_version

            logger.info(f"[UpdaterService] Actualización publicada con éxito: v{new_version} ({manifest['file_size_mb']} MB)")

            return {
                "success": True,
                "version": new_version,
                "changelog": changelog,
                "manifest": manifest
            }

        except Exception as e:
            logger.error(f"[UpdaterService] Error al publicar actualización: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    def check_for_updates(self) -> Dict[str, Any]:
        """
        Para OTRAS PCs: Consulta si existe una versión más reciente disponible en la carpeta compartida / Google Drive.
        """
        # 1. Intentar leer version.json local en updates/ (sincronizado por Google Drive Desktop)
        manifest = None
        if self.version_file.exists():
            try:
                with open(self.version_file, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except Exception as e:
                logger.warning(f"[UpdaterService] Error al leer version.json: {e}")

        # 2. Si no hay archivo o para mayor seguridad, retornar estado
        current_v = settings.APP_VERSION
        if not manifest or "version" not in manifest:
            return {
                "has_update": False,
                "current_version": current_v,
                "latest_version": current_v,
                "message": "Sistema al día. No hay actualizaciones pendientes."
            }

        latest_v = manifest.get("version", current_v)
        is_newer = self._is_version_newer(latest_v, current_v)
        update_file_exists = self.update_zip_file.exists()

        return {
            "has_update": is_newer and update_file_exists,
            "current_version": current_v,
            "latest_version": latest_v,
            "changelog": manifest.get("changelog", ""),
            "released_at": manifest.get("released_at", ""),
            "file_size_mb": manifest.get("file_size_mb", 0),
            "update_file_ready": update_file_exists
        }

    def apply_update(self) -> Dict[str, Any]:
        """
        Para OTRAS PCs: Aplica la actualización descargando y sobreescribiendo los archivos de código,
        preservando intacta la base de datos (data/despachos.db), uploads y credenciales (.env).
        """
        if not self.update_zip_file.exists():
            return {
                "success": False,
                "error": "No se encontró el archivo 'update_latest.zip' en la carpeta de actualizaciones."
            }

        # 1. Realizar un RESPALDO PREVENTIVO de la base de datos antes de tocar código
        backup_svc = BackupService()
        backup_res = backup_svc.create_system_backup(reason="PRE_ACTUALIZACION", include_uploads=False, include_code=False)

        # 2. Leer versión a instalar
        check = self.check_for_updates()
        new_version = check.get("latest_version", settings.APP_VERSION)
        old_version = settings.APP_VERSION

        # 3. Lista negra de archivos que JAMÁS se deben sobreescribir
        protected_paths = {
            "data/despachos.db", "service_account.json", ".env"
        }

        try:
            with zipfile.ZipFile(self.update_zip_file, 'r') as zipf:
                for member in zipf.infolist():
                    # Normalizar ruta
                    norm_path = member.filename.replace("\\", "/").strip("/")
                    
                    # Evitar path traversal
                    if ".." in norm_path or norm_path.startswith("/"):
                        continue

                    # Proteger base de datos y credenciales
                    if any(norm_path.startswith(prot) or norm_path == prot for prot in protected_paths):
                        continue

                    if norm_path.startswith("uploads/") or norm_path.startswith("backups/"):
                        continue

                    # Extraer de forma segura
                    dest_file = BASE_DIR / norm_path
                    if member.is_dir():
                        dest_file.mkdir(parents=True, exist_ok=True)
                    else:
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                        with zipf.open(member) as src, open(dest_file, "wb") as dst:
                            shutil.copyfileobj(src, dst)

            # 4. Actualizar versión en .env
            self._update_env_version(new_version)
            settings.APP_VERSION = new_version

            logger.info(f"[UpdaterService] ¡Sistema actualizado exitosamente de v{old_version} a v{new_version}!")

            return {
                "success": True,
                "old_version": old_version,
                "new_version": new_version,
                "backup_file": backup_res.get("filename"),
                "message": f"¡Sistema actualizado con éxito a la versión v{new_version}!"
            }

        except Exception as e:
            logger.error(f"[UpdaterService] Error al aplicar actualización: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Error al extraer la actualización: {str(e)}"
            }

    def _is_version_newer(self, latest: str, current: str) -> bool:
        """Compara cadenas de versiones semánticas (ej: '1.1.0' > '1.0.0' o '1.0.9-test' > '1.0.0')."""
        import re
        def parse_v(v_str):
            nums = re.findall(r'\d+', str(v_str))
            return tuple(int(n) for n in nums) if nums else (0,)

        try:
            return parse_v(latest) > parse_v(current)
        except Exception:
            return str(latest) != str(current)

    def _update_env_version(self, new_version: str):
        """Actualiza la variable APP_VERSION en el archivo .env si existe."""
        env_path = BASE_DIR / ".env"
        if not env_path.exists():
            return

        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
            new_lines = []
            found = False
            for line in lines:
                if line.startswith("APP_VERSION="):
                    new_lines.append(f"APP_VERSION={new_version}")
                    found = True
                else:
                    new_lines.append(line)

            if not found:
                new_lines.append(f"APP_VERSION={new_version}")

            env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        except Exception as e:
            logger.warning(f"[UpdaterService] No se pudo actualizar APP_VERSION en .env: {e}")

    def get_git_status(self) -> Dict[str, Any]:
        """
        Inspecciona el repositorio Git local (si existe) y reporta rama, commit y estado.
        """
        git_dir = BASE_DIR / ".git"
        if not git_dir.exists():
            return {
                "has_git": False,
                "message": "No se detectó un repositorio Git local (.git no encontrado)."
            }

        import subprocess
        try:
            # 1. Rama actual
            branch_proc = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=5
            )
            branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else "desconocida"

            # 2. Hash corto del commit
            hash_proc = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=5
            )
            commit_hash = hash_proc.stdout.strip() if hash_proc.returncode == 0 else ""

            # 3. Mensaje del último commit
            msg_proc = subprocess.run(
                ["git", "log", "-1", "--format=%s"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=5
            )
            commit_msg = msg_proc.stdout.strip() if msg_proc.returncode == 0 else ""

            # 4. Fecha del último commit
            date_proc = subprocess.run(
                ["git", "log", "-1", "--format=%cd", "--date=relative"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=5
            )
            commit_date = date_proc.stdout.strip() if date_proc.returncode == 0 else ""

            # 5. Remote URL
            remote_proc = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=5
            )
            remote_url = remote_proc.stdout.strip() if remote_proc.returncode == 0 else ""

            return {
                "has_git": True,
                "branch": branch,
                "commit_hash": commit_hash,
                "commit_msg": commit_msg,
                "commit_date": commit_date,
                "remote_url": remote_url,
                "message": f"Rama: {branch} ({commit_hash}) - {commit_msg}"
            }
        except Exception as e:
            return {
                "has_git": True,
                "error": str(e),
                "message": f"Git detectado pero ocurrió un error al consultar estado: {str(e)}"
            }

    def git_pull(self) -> Dict[str, Any]:
        """
        Ejecuta git pull para descargar y aplicar cambios desde el repositorio remoto.
        Realiza un respaldo preventivo de la base de datos antes de proceder.
        """
        git_dir = BASE_DIR / ".git"
        if not git_dir.exists():
            return {
                "success": False,
                "error": "No se detectó un repositorio Git local (.git no encontrado)."
            }

        # 1. Respaldo preventivo
        backup_svc = BackupService()
        backup_res = backup_svc.create_system_backup(reason="PRE_GIT_PULL", include_uploads=False, include_code=False)

        import subprocess
        try:
            proc = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=30
            )

            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()

            if proc.returncode == 0:
                # Obtener nuevo commit
                status = self.get_git_status()
                return {
                    "success": True,
                    "output": stdout,
                    "status": status,
                    "backup_file": backup_res.get("filename"),
                    "message": f"Sincronización Git exitosa: {stdout}"
                }
            else:
                return {
                    "success": False,
                    "error": stderr or stdout or "Error al ejecutar git pull",
                    "output": stdout
                }
        except Exception as e:
            return {
                "success": False,
                "error": f"Excepción al ejecutar git pull: {str(e)}"
            }

    def connect_git_repo(self, repo_url: str = "https://github.com/alifarhat79/aduanadoc.git") -> Dict[str, Any]:
        """Inicializa y vincula el repositorio Git si está instalado, o descarga y aplica el código directamente desde GitHub."""
        import subprocess
        import shutil
        import httpx
        import zipfile
        import io

        repo_url = (repo_url or "https://github.com/alifarhat79/aduanadoc.git").strip()
        git_executable = shutil.which("git")

        # Respaldo preventivo de seguridad
        backup_svc = BackupService()
        backup_res = backup_svc.create_system_backup(reason="PRE_GITHUB_SYNC", include_uploads=False, include_code=False)

        # 1. Si Git está instalado en el sistema
        if git_executable:
            try:
                subprocess.run([git_executable, "init", "-q"], cwd=str(BASE_DIR), capture_output=True, timeout=10)
                subprocess.run([git_executable, "remote", "remove", "origin"], cwd=str(BASE_DIR), capture_output=True, timeout=10)
                subprocess.run([git_executable, "remote", "add", "origin", repo_url], cwd=str(BASE_DIR), capture_output=True, timeout=10)
                proc_fetch = subprocess.run([git_executable, "fetch", "origin", "main"], cwd=str(BASE_DIR), capture_output=True, text=True, timeout=30)
                if proc_fetch.returncode != 0:
                    subprocess.run([git_executable, "fetch", "origin"], cwd=str(BASE_DIR), capture_output=True, timeout=30)
                
                subprocess.run([git_executable, "branch", "-M", "main"], cwd=str(BASE_DIR), capture_output=True, timeout=10)
                subprocess.run([git_executable, "reset", "--mixed", "origin/main"], cwd=str(BASE_DIR), capture_output=True, timeout=15)
                status = self.get_git_status()
                return {
                    "success": True,
                    "mode": "git_cli",
                    "message": "Repositorio Git vinculado y sincronizado exitosamente con GitHub.",
                    "status": status,
                    "backup_file": backup_res.get("filename")
                }
            except Exception as e:
                logger.warning(f"[UpdaterService] Error con Git CLI, intentando fallback HTTP: {e}")

        # 2. Fallback Directo HTTP (Funciona SIN Git instalado en la máquina)
        try:
            headers = {}
            clean_url = repo_url.rstrip("/").removesuffix(".git")

            if "@github.com" in clean_url:
                parts = clean_url.split("@github.com")
                auth_part = parts[0].replace("https://", "").replace("http://", "")
                clean_url = f"https://github.com{parts[1]}"
                headers["Authorization"] = f"Bearer {auth_part}"

            zip_url = f"{clean_url}/archive/refs/heads/main.zip"

            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                resp = client.get(zip_url, headers=headers)
                if resp.status_code == 404:
                    return {
                        "success": False,
                        "error": "GitHub devolvió 404 Not Found porque el repositorio es PRIVADO. Hazlo público en GitHub (Settings -> Danger Zone -> Change visibility to Public) o usa el actualizador ZIP."
                    }
                elif resp.status_code != 200:
                    return {
                        "success": False,
                        "error": f"No se pudo descargar el repositorio desde GitHub ({resp.status_code}): {resp.text[:100]}"
                    }

                zip_bytes = io.BytesIO(resp.content)
                with zipfile.ZipFile(zip_bytes) as z:
                    names = z.namelist()
                    root_prefix = names[0].split("/")[0] if names and "/" in names[0] else ""
                    
                    for member in z.infolist():
                        if member.is_dir():
                            continue
                        rel_path = member.filename
                        if root_prefix and rel_path.startswith(f"{root_prefix}/"):
                            rel_path = rel_path[len(root_prefix) + 1:]
                        
                        if not rel_path or rel_path.startswith(".git") or rel_path.startswith("data/") or rel_path == ".env":
                            continue

                        target_path = BASE_DIR / rel_path
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(member) as source_file, open(target_path, "wb") as target_file:
                            shutil.copyfileobj(source_file, target_file)

            return {
                "success": True,
                "mode": "http_direct",
                "message": "¡Sistema actualizado exitosamente desde GitHub directamente (sin necesidad de instalar Git)!",
                "backup_file": backup_res.get("filename")
            }
        except Exception as e:
            logger.error(f"[UpdaterService] Error al actualizar vía HTTP directo: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Error al conectar con GitHub: {str(e)}"
            }
