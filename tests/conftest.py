"""
Configuración común de pruebas.

Las claves reales ya no están en el código (repositorio público). Para las pruebas
se usa una contraseña de programador ficticia, definida aquí en memoria, sin tocar
el .env real ni el archivo de claves de Google Drive.
"""
import os

os.environ["ADUANADOC_SKIP_SECRETS_SYNC"] = "1"

from app.config import settings  # noqa: E402  (carga .env primero, luego lo sobrescribimos en memoria)

TEST_ADMIN_PASSWORD = "clave_de_prueba_123"

os.environ["CONFIG_ADMIN_PASSWORD"] = TEST_ADMIN_PASSWORD
# Nunca tocar la base Turso real desde las pruebas (antes se subían despachos de prueba a la nube).
os.environ["TURSO_DATABASE_URL"] = ""
os.environ["TURSO_AUTH_TOKEN"] = ""
settings.CONFIG_ADMIN_PASSWORD = TEST_ADMIN_PASSWORD

# Los endpoints de configuración guardan claves con set_key(ENV_PATH): en pruebas se usa
# un .env temporal para no pisar el .env real de la PC.
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402
from app.routers import configuracion as _configuracion  # noqa: E402

_TEST_ENV = Path(tempfile.gettempdir()) / "aduanadoc_pruebas.env"
_TEST_ENV.write_text("", encoding="utf-8")
_configuracion.ENV_PATH = _TEST_ENV
