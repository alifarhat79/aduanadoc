"""
Configuración común de pruebas.

Las pruebas se ejecutan en un entorno 100% AISLADO de los datos reales de la PC:
- Base SQLite temporal (antes se escribía en data/despachos.db real).
- Carpetas temporales de uploads, backups y updates (antes la rotación de backups de
  las pruebas borraba los respaldos reales y se publicaba un update_latest.zip de prueba).
- El actualizador trabaja sobre una carpeta temporal (antes reescribía .env y
  data/installed_version.json reales).
- Sin Telegram, Webhook, Turso ni Google Drive reales (antes se enviaban mensajes reales).

Las claves reales ya no están en el código (repositorio público). Para las pruebas se usa
una contraseña de programador ficticia, definida aquí en memoria.

IMPORTANTE: este archivo debe ajustar `settings` ANTES de que se importe `app.database`,
porque el motor de SQLAlchemy se crea al importar ese módulo.
"""
import os
import tempfile
from pathlib import Path

os.environ["ADUANADOC_SKIP_SECRETS_SYNC"] = "1"

from app.config import settings  # noqa: E402  (carga .env primero, luego lo sobrescribimos en memoria)

# --- 1. Carpetas y base de datos temporales -------------------------------------------
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="aduanadoc_pruebas_"))
for _sub in ("data", "uploads", "backups", "updates"):
    (_TMP_ROOT / _sub).mkdir(parents=True, exist_ok=True)

settings.DATA_DIR = str(_TMP_ROOT / "data")
settings.UPLOAD_DIR = str(_TMP_ROOT / "uploads")
settings.BACKUP_DIR = str(_TMP_ROOT / "backups")
settings.UPDATES_DIR = str(_TMP_ROOT / "updates")
settings.DATABASE_URL = f"sqlite:///{(_TMP_ROOT / 'data' / 'despachos.db').as_posix()}"

# --- 2. Servicios externos desactivados ------------------------------------------------
TEST_ADMIN_PASSWORD = "clave_de_prueba_123"
os.environ["CONFIG_ADMIN_PASSWORD"] = TEST_ADMIN_PASSWORD
settings.CONFIG_ADMIN_PASSWORD = TEST_ADMIN_PASSWORD

for _key in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "WEBHOOK_URL"):
    os.environ[_key] = ""
    setattr(settings, _key, "")

# Google Drive: credenciales inexistentes para que nada llegue a la cuenta real
os.environ["GDRIVE_CREDENTIALS_FILE"] = str(_TMP_ROOT / "service_account_inexistente.json")
settings.GDRIVE_CREDENTIALS_FILE = os.environ["GDRIVE_CREDENTIALS_FILE"]

# --- 3. Crear el esquema en la base temporal --------------------------------------------
from app.database import init_db, engine  # noqa: E402

assert str(engine.url).endswith("despachos.db") and str(_TMP_ROOT.as_posix()) in str(engine.url), (
    "Las pruebas NO deben usar la base de datos real"
)
init_db()

# --- 4. Archivos que algunos servicios escriben fuera de settings -----------------------
# Los endpoints de configuración guardan claves con set_key(ENV_PATH): se usa un .env temporal.
from app.routers import configuracion as _configuracion  # noqa: E402

_TEST_ENV = _TMP_ROOT / "aduanadoc_pruebas.env"
_TEST_ENV.write_text("", encoding="utf-8")
_configuracion.ENV_PATH = _TEST_ENV

# El actualizador publica/aplica paquetes y escribe .env e installed_version.json en BASE_DIR:
# en pruebas se redirige a la carpeta temporal para no tocar el código ni el .env reales.
from app.services import updater_service as _updater_service  # noqa: E402

_updater_service.BASE_DIR = _TMP_ROOT
