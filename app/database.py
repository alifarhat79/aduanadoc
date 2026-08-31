from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings

# Conexión SQLite optimizada para alta concurrencia con WAL y timeout extendido
is_sqlite = settings.DATABASE_URL.startswith("sqlite")

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 60.0} if is_sqlite else {},
    pool_pre_ping=True,
    echo=False
)

if is_sqlite:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=60000;")
        cursor.execute("PRAGMA cache_size=-64000;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

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

            # Comprobar si existe la columna familia en marca_subitem_etiquetas
            res_et = conn.exec_driver_sql("PRAGMA table_info(marca_subitem_etiquetas);").fetchall()
            cols_et = [row[1] for row in res_et]
            if "familia" not in cols_et:
                conn.exec_driver_sql("ALTER TABLE marca_subitem_etiquetas ADD COLUMN familia VARCHAR(150);")

            # Índices de alto rendimiento para búsquedas y filtros rápidos
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_despacho_items_marca ON despacho_items(marca);")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_despacho_items_ncm ON despacho_items(codigo_ncm);")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_despacho_items_desc ON despacho_items(descripcion);")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_despacho_items_marca_desc ON despacho_items(marca, descripcion);")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_despachos_fecha ON despachos(fecha_despacho);")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_despachos_prop ON despachos(propietario);")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_despachos_num ON despachos(numero_despacho);")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_marca_subitem_marca ON marca_subitem_etiquetas(marca);")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_marca_subitem_familia ON marca_subitem_etiquetas(familia);")

            conn.commit()
    except Exception:
        pass
