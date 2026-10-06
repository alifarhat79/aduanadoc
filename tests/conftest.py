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
settings.CONFIG_ADMIN_PASSWORD = TEST_ADMIN_PASSWORD
