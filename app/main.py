import os
import sys

# Forzar codificación UTF-8 en Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.config import settings
from app.database import init_db
from app.services.gdrive_watcher import GDriveWatcher
from app.routers import dashboard, despachos, mercancias, turso, configuracion, upload, revisar, exportacion, backup_updater

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicializar Base de Datos
    init_db()
    # Iniciar Vigilante de Google Drive en segundo plano
    watcher = GDriveWatcher.get_instance()
    await watcher.start()
    yield
    # Detener vigilante al cerrar
    await watcher.stop()

app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    lifespan=lifespan
)

# Servir archivos estáticos
static_dir = Path(__file__).resolve().parent / "static"
os.makedirs(static_dir / "css", exist_ok=True)
os.makedirs(static_dir / "js", exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Incluir routers
app.include_router(dashboard.router)
app.include_router(despachos.router)
app.include_router(mercancias.router)
app.include_router(turso.router)
app.include_router(configuracion.router)
app.include_router(upload.router)
app.include_router(revisar.router)
app.include_router(exportacion.router)
app.include_router(backup_updater.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
