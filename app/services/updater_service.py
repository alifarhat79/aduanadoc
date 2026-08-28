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

    def _get_installed_commit(self) -> Dict[str, Any]:
        """Obtiene la información del commit instalado actualmente en esta PC."""
        ver_file = BASE_DIR / "data" / "installed_version.json"
        if ver_file.exists():
            try:
                return json.loads(ver_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Si existe .git
        git_dir = BASE_DIR / ".git"
        if git_dir.exists():
            import subprocess
            try:
                h_proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(BASE_DIR), capture_output=True, text=True, timeout=5)
                m_proc = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=str(BASE_DIR), capture_output=True, text=True, timeout=5)
                d_proc = subprocess.run(["git", "log", "-1", "--format=%cd", "--date=relative"], cwd=str(BASE_DIR), capture_output=True, text=True, timeout=5)
                if h_proc.returncode == 0 and h_proc.stdout.strip():
                    return {
                        "commit_hash": h_proc.stdout.strip(),
                        "commit_msg": m_proc.stdout.strip() if m_proc.returncode == 0 else "",
                        "commit_date": d_proc.stdout.strip() if d_proc.returncode == 0 else "",
                        "updated_at": datetime.now().isoformat()
                    }
            except Exception:
                pass

        return {
            "commit_hash": settings.APP_VERSION,
            "commit_msg": "Versión instalada",
            "commit_date": "",
            "updated_at": datetime.now().isoformat()
        }

    def _save_installed_commit(self, commit_hash: str, commit_msg: str = "", commit_date: str = ""):
        """Guarda la versión y commit instalado en data/installed_version.json."""
        ver_file = BASE_DIR / "data" / "installed_version.json"
        ver_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "commit_hash": commit_hash,
            "commit_msg": commit_msg,
            "commit_date": commit_date,
            "updated_at": datetime.now().isoformat()
        }
        ver_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def check_github_updates(self, repo_url: str = "https://github.com/alifarhat79/aduanadoc.git") -> Dict[str, Any]:
        """
        Consulta la API de GitHub para verificar si existen nuevos commits en la rama main.
        Funciona en cualquier PC con o sin Git instalado.
        """
        import httpx
        clean_url = repo_url.rstrip("/").removesuffix(".git")
        parts = clean_url.split("github.com/")
        if len(parts) < 2:
            return {"has_update": False, "error": "URL de GitHub inválida"}

        repo_slug = parts[1]
        api_url = f"https://api.github.com/repos/{repo_slug}/commits/main"

        try:
            with httpx.Client(timeout=6.0, follow_redirects=True) as client:
                resp = client.get(api_url, headers={"User-Agent": "AduanaDoc-Updater/1.0"})
                if resp.status_code == 200:
                    gh_data = resp.json()
                    remote_sha = gh_data.get("sha", "")[:7]
                    remote_full_sha = gh_data.get("sha", "")
                    commit_obj = gh_data.get("commit", {})
                    remote_msg = (commit_obj.get("message") or "").split("\n")[0]
                    committer = commit_obj.get("committer", {})
                    remote_date = committer.get("date", "")

                    local_info = self._get_installed_commit()
                    local_hash = (local_info.get("commit_hash") or "").strip()

                    # Comparar si hay nuevo commit
                    has_update = bool(remote_sha and local_hash and not local_hash.startswith(remote_sha) and not remote_full_sha.startswith(local_hash))

                    return {
                        "has_update": has_update,
                        "latest_commit": remote_sha,
                        "latest_full_commit": remote_full_sha,
                        "latest_message": remote_msg,
                        "latest_date": remote_date,
                        "current_commit": local_hash,
                        "message": f"¡Nueva actualización disponible en GitHub! ({remote_sha}: {remote_msg})" if has_update else "El sistema está actualizado a la última versión de GitHub."
                    }
                elif resp.status_code == 404:
                    return {"has_update": False, "error": "Repositorio privado o no encontrado en GitHub"}
                else:
                    return {"has_update": False, "error": f"Respuesta de GitHub HTTP {resp.status_code}"}
        except Exception as e:
            logger.debug(f"[UpdaterService] No se pudo verificar GitHub (modo offline o timeout): {e}")
            return {"has_update": False, "error": str(e)}

    def check_for_updates(self) -> Dict[str, Any]:
        """
        Para OTRAS PCs: Consulta si existe una versión más reciente disponible
        ya sea en archivo ZIP local / Google Drive o en GitHub Cloud.
        """
        # 1. Comprobación por archivo ZIP en updates/ (Google Drive o publicación manual)
        manifest = None
        if self.version_file.exists():
            try:
                with open(self.version_file, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except Exception as e:
                logger.warning(f"[UpdaterService] Error al leer version.json: {e}")

        current_v = settings.APP_VERSION
        if manifest and "version" in manifest:
            latest_v = manifest.get("version", current_v)
            is_newer = self._is_version_newer(latest_v, current_v)
            update_file_exists = self.update_zip_file.exists()

            if is_newer and update_file_exists:
                return {
                    "has_update": True,
                    "source": "zip",
                    "current_version": current_v,
                    "latest_version": latest_v,
                    "changelog": manifest.get("changelog", ""),
                    "released_at": manifest.get("released_at", ""),
                    "file_size_mb": manifest.get("file_size_mb", 0),
                    "update_file_ready": True,
                    "message": f"¡Nueva actualización disponible en archivo ZIP: v{latest_v}!"
                }

        # 2. Comprobación de GitHub en tiempo real
        gh_check = self.check_github_updates()
        if gh_check.get("has_update"):
            return {
                "has_update": True,
                "source": "github",
                "current_version": gh_check.get("current_commit") or settings.APP_VERSION,
                "latest_version": gh_check.get("latest_commit"),
                "changelog": gh_check.get("latest_message"),
                "released_at": gh_check.get("latest_date"),
                "message": gh_check.get("message")
            }

        local_info = self._get_installed_commit()
        return {
            "has_update": False,
            "current_version": local_info.get("commit_hash") or current_v,
            "latest_version": gh_check.get("latest_commit") or local_info.get("commit_hash") or current_v,
            "message": "Sistema al día. No hay actualizaciones pendientes."
        }

    def apply_update(self) -> Dict[str, Any]:
        """
        Para OTRAS PCs: Aplica la actualización automáticamente desde GitHub o ZIP,
        preservando intacta la base de datos (data/despachos.db), uploads y credenciales (.env).
        """
        # Si hay archivo ZIP local preparado, aplicarlo
        if self.update_zip_file.exists():
            return self._apply_zip_update()

        # Si no hay ZIP local, descargar directamente desde GitHub
        return self.connect_git_repo()

    def _apply_zip_update(self) -> Dict[str, Any]:
        """Aplica actualización desde el archivo update_latest.zip."""
        if not self.update_zip_file.exists():
            return {
                "success": False,
                "error": "No se encontró el archivo 'update_latest.zip' en la carpeta de actualizaciones."
            }

        backup_svc = BackupService()
        backup_res = backup_svc.create_system_backup(reason="PRE_ACTUALIZACION", include_uploads=False, include_code=False)

        check = self.check_for_updates()
        new_version = check.get("latest_version", settings.APP_VERSION)
        old_version = settings.APP_VERSION

        protected_paths = {
            "data/despachos.db", "service_account.json", ".env"
        }

        try:
            with zipfile.ZipFile(self.update_zip_file, 'r') as zipf:
                for member in zipf.infolist():
                    norm_path = member.filename.replace("\\", "/").strip("/")
                    if ".." in norm_path or norm_path.startswith("/"):
                        continue
                    if any(norm_path.startswith(prot) or norm_path == prot for prot in protected_paths):
                        continue
                    if norm_path.startswith("uploads/") or norm_path.startswith("backups/"):
                        continue

                    dest_file = BASE_DIR / norm_path
                    if member.is_dir():
                        dest_file.mkdir(parents=True, exist_ok=True)
                    else:
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                        with zipf.open(member) as src, open(dest_file, "wb") as dst:
                            shutil.copyfileobj(src, dst)

            self._update_env_version(new_version)
            settings.APP_VERSION = new_version
            self._save_installed_commit(new_version, f"Actualización ZIP v{new_version}")

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
            inst_info = self._get_installed_commit()
            return {
                "has_git": False,
                "mode": "http_cloud",
                "branch": "main",
                "commit_hash": inst_info.get("commit_hash", settings.APP_VERSION),
                "commit_msg": inst_info.get("commit_msg", ""),
                "commit_date": inst_info.get("commit_date", ""),
                "remote_url": "https://github.com/alifarhat79/aduanadoc.git",
                "message": "Conectado a GitHub Cloud (Sincronización directa sin cliente Git local)."
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

    def git_pull(self, repo_url: str = "https://github.com/alifarhat79/aduanadoc.git") -> Dict[str, Any]:
        """
        Ejecuta git pull para descargar y aplicar cambios desde el repositorio remoto.
        Si la PC no tiene .git inicializado, ejecuta automáticamente la conexión/sincronización con GitHub.
        Realiza un respaldo preventivo de la base de datos antes de proceder.
        """
        git_dir = BASE_DIR / ".git"
        if not git_dir.exists():
            return self.connect_git_repo(repo_url=repo_url)

        # 1. Respaldo preventivo
        backup_svc = BackupService()
        backup_res = backup_svc.create_system_backup(reason="PRE_GIT_PULL", include_uploads=False, include_code=False)

        import subprocess
        try:
            # Intentar git pull
            proc = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=30
            )

            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()

            if proc.returncode != 0:
                # Intentar git pull origin main
                proc = subprocess.run(
                    ["git", "pull", "origin", "main"],
                    cwd=str(BASE_DIR),
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                stdout = proc.stdout.strip()
                stderr = proc.stderr.strip()

            if proc.returncode == 0:
                status = self.get_git_status()
                return {
                    "success": True,
                    "output": stdout,
                    "status": status,
                    "backup_file": backup_res.get("filename"),
                    "message": f"Sincronización Git exitosa: {stdout}"
                }
            else:
                # Fallback a reconexión con GitHub
                return self.connect_git_repo(repo_url=repo_url)
        except Exception as e:
            logger.warning(f"[UpdaterService] Error en git pull, usando fallback: {e}")
            return self.connect_git_repo(repo_url=repo_url)

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
                if status.get("commit_hash"):
                    self._save_installed_commit(status["commit_hash"], status.get("commit_msg", ""), status.get("commit_date", ""))
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

            # Consultar y guardar commit instalado
            gh_info = self.check_github_updates(repo_url=repo_url)
            installed_sha = gh_info.get("latest_commit") or "main"
            installed_msg = gh_info.get("latest_message") or "Actualización desde GitHub"
            installed_date = gh_info.get("latest_date") or ""
            self._save_installed_commit(installed_sha, installed_msg, installed_date)

            return {
                "success": True,
                "mode": "http_direct",
                "message": f"¡Sistema actualizado exitosamente a la última versión de GitHub ({installed_sha})!",
                "commit_hash": installed_sha,
                "commit_msg": installed_msg,
                "backup_file": backup_res.get("filename")
            }
        except Exception as e:
            logger.error(f"[UpdaterService] Error al actualizar vía HTTP directo: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Error al conectar con GitHub: {str(e)}"
            }
