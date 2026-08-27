from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings

# Conexión SQLite con check_same_thread=False para FastAPI
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Generador de sesión de base de datos para inyección de dependencias."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Inicializa todas las tablas en la base de datos y migra columnas si es necesario."""
    import app.models  # Asegurar registro de modelos
    Base.metadata.create_all(bind=engine)

    # Migración ligera automática para SQLite
    try:
        with engine.connect() as conn:
            # Comprobar si existe la columna propietario en despachos
            res_desp = conn.exec_driver_sql("PRAGMA table_info(despachos);").fetchall()
            cols_desp = [row[1] for row in res_desp]
            if "propietario" not in cols_desp:
                conn.exec_driver_sql("ALTER TABLE despachos ADD COLUMN propietario VARCHAR(150);")

            # Comprobar si existe la columna codigo_producto en despacho_items
            res_items = conn.exec_driver_sql("PRAGMA table_info(despacho_items);").fetchall()
            cols_items = [row[1] for row in res_items]
            if "codigo_producto" not in cols_items:
                conn.exec_driver_sql("ALTER TABLE despacho_items ADD COLUMN codigo_producto VARCHAR(100);")

            conn.commit()
    except Exception:
        pass
