"""
Candado compartido entre PROCESOS de la misma PC.

El servidor web (vigilante dentro de FastAPI) y el vigilante en segundo plano
(vigilante_background.py) son programas distintos que escanean la misma carpeta de Google Drive.
El candado de hilos (threading.Lock) solo funciona dentro de un mismo proceso, así que ambos podían
ver el mismo PDF como "nuevo" y guardarlo dos veces. Este candado de archivo los pone en fila.

- El archivo vive en la carpeta temporal del sistema (no dentro de Google Drive, para no sincronizarlo).
- Su nombre depende de la base de datos: dos instalaciones en la misma PC (p. ej. el proyecto y
  C:\\aduanadoc) tienen candados distintos y no se bloquean entre sí.
- Si el proceso que lo tiene se cierra o se cuelga, el sistema operativo lo libera solo.
"""
import hashlib
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt

    def _bloquear(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _desbloquear(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _bloquear(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _desbloquear(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def ruta_candado(nombre: str) -> Path:
    from app.config import settings

    instalacion = hashlib.sha1(settings.DATABASE_URL.encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"aduanadoc_{instalacion}_{nombre}.lock"


@contextmanager
def candado_entre_procesos(nombre: str, espera_max: float = 900.0, intervalo: float = 0.5):
    """
    Espera hasta `espera_max` segundos a que ningún otro proceso tenga el candado.
    Entrega True si se obtuvo y False si se agotó la espera (el llamador decide qué hacer).
    """
    f = open(ruta_candado(nombre), "a+b")
    obtenido = False
    try:
        limite = time.monotonic() + max(0.0, espera_max)
        while True:
            try:
                _bloquear(f)
                obtenido = True
                break
            except OSError:
                if time.monotonic() >= limite:
                    break
                time.sleep(intervalo)
        yield obtenido
    finally:
        if obtenido:
            try:
                _desbloquear(f)
            except OSError:
                pass
        f.close()
