import os
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, date
import httpx
from sqlalchemy.orm import Session
from sqlalchemy import inspect

from app.models import Despacho, DespachoItem, DespachoAuditoria
from app.config import settings

logger = logging.getLogger(__name__)

class TursoService:
    def __init__(self, db_url: Optional[str] = None, auth_token: Optional[str] = None):
        raw_url = (db_url or os.getenv("TURSO_DATABASE_URL", "libsql://despachos-alifarhat.aws-us-east-1.turso.io") or "").strip().strip("'").strip('"')
        self.auth_token = (auth_token or os.getenv("TURSO_AUTH_TOKEN", "") or "").strip().strip("'").strip('"')

        # Normalizar URL a HTTPS
        if raw_url.startswith("libsql://"):
            self.http_url = raw_url.replace("libsql://", "https://")
        elif raw_url.startswith("http://") or raw_url.startswith("https://"):
            self.http_url = raw_url
        else:
            self.http_url = f"https://{raw_url}" if raw_url else ""

        if self.http_url and not self.http_url.endswith("/v2/pipeline"):
            self.pipeline_url = f"{self.http_url.rstrip('/')}/v2/pipeline"
        else:
            self.pipeline_url = self.http_url

    def is_configured(self) -> bool:
        return bool(self.pipeline_url and self.auth_token)

    async def execute_raw(self, statements: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Ejecuta una lista de sentencias SQL en lote en Turso a través de su API HTTP Pipeline v2."""
        if not self.is_configured():
            raise ValueError("Turso no está configurado. Por favor provee la URL de la base de datos y el Auth Token.")

        headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json"
        }

        requests = []
        for stmt in statements:
            requests.append({
                "type": "execute",
                "stmt": stmt
            })
        requests.append({"type": "close"})

        payload = {"requests": requests}

        async with httpx.AsyncClient(timeout=40.0) as client:
            resp = await client.post(self.pipeline_url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise Exception(f"Error de Turso ({resp.status_code}): {resp.text}")
            return resp.json()

    async def test_connection(self) -> Dict[str, Any]:
        """Prueba la conectividad ejecutando un SELECT 1."""
        res = await self.execute_raw([{"sql": "SELECT 1 as connected"}])
        return {"success": True, "message": "Conexión a Turso establecida exitosamente", "response": res}

    async def init_turso_schema(self):
        """Crea las tablas en Turso si aún no existen."""
        schema_stmts = [
            {
                "sql": """
                CREATE TABLE IF NOT EXISTS despachos (
                    id INTEGER PRIMARY KEY,
                    propietario TEXT,
                    numero_despacho TEXT,
                    numero_declaracion TEXT,
                    referencia TEXT,
                    fecha_despacho TEXT,
                    fecha_registro TEXT,
                    fecha_liberacion TEXT,
                    importador_nombre TEXT,
                    importador_documento TEXT,
                    importador_direccion TEXT,
                    exportador_nombre TEXT,
                    exportador_pais TEXT,
                    exportador_direccion TEXT,
                    despachante_nombre TEXT,
                    despachante_documento TEXT,
                    despachante_empresa TEXT,
                    modalidad_transporte TEXT,
                    bl TEXT,
                    hbl TEXT,
                    mbl TEXT,
                    awb TEXT,
                    contenedor TEXT,
                    buque TEXT,
                    vuelo TEXT,
                    matricula TEXT,
                    empresa_transporte TEXT,
                    puerto_origen TEXT,
                    puerto_destino TEXT,
                    pais_origen TEXT,
                    pais_procedencia TEXT,
                    aduana TEXT,
                    regimen TEXT,
                    canal TEXT,
                    tipo_cambio REAL,
                    moneda TEXT,
                    valor_fob REAL,
                    valor_flete REAL,
                    valor_seguro REAL,
                    valor_cif REAL,
                    valor_imponible REAL,
                    total_general REAL,
                    total_tributos_moneda_nacional REAL,
                    cantidad_bultos REAL,
                    tipo_bultos TEXT,
                    peso_bruto REAL,
                    peso_neto REAL,
                    observaciones TEXT,
                    archivo_pdf TEXT,
                    nombre_archivo_original TEXT,
                    hash_archivo TEXT,
                    tipo_documento_detectado TEXT,
                    metodo_extraccion TEXT,
                    numero_paginas INTEGER,
                    estado_procesamiento TEXT,
                    confianza_promedio REAL,
                    metadata_extraccion TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );
                """
            },
            {
                "sql": """
                CREATE TABLE IF NOT EXISTS despacho_items (
                    id INTEGER PRIMARY KEY,
                    despacho_id INTEGER,
                    numero_item INTEGER,
                    numero_subitem INTEGER,
                    codigo_ncm TEXT,
                    codigo_producto TEXT,
                    descripcion TEXT,
                    marca TEXT,
                    cantidad REAL,
                    unidad TEXT,
                    peso_neto REAL,
                    peso_bruto REAL,
                    valor_unitario REAL,
                    valor_total REAL,
                    tasa_iva REAL,
                    tasa_arancel REAL,
                    pais_origen TEXT,
                    pais_procedencia TEXT,
                    pagina_origen INTEGER,
                    FOREIGN KEY (despacho_id) REFERENCES despachos(id) ON DELETE CASCADE
                );
                """
            }
        ]
        return await self.execute_raw(schema_stmts)

    def _convert_value_to_arg(self, val: Any) -> Dict[str, Any]:
        """Convierte un valor de Python al formato tipado de libSQL HTTP API."""
        if val is None:
            return {"type": "null"}
        elif isinstance(val, bool):
            return {"type": "integer", "value": str(int(val))}
        elif isinstance(val, int):
            return {"type": "integer", "value": str(val)}
        elif isinstance(val, float):
            return {"type": "float", "value": float(val)}
        elif isinstance(val, (datetime, date)):
            return {"type": "text", "value": val.isoformat()}
        elif isinstance(val, (dict, list)):
            return {"type": "text", "value": json.dumps(val)}
        else:
            return {"type": "text", "value": str(val)}

    async def push_all_to_turso(self, db: Session) -> Dict[str, Any]:
        """Sube todos los despachos y mercancías locales a Turso."""
        await self.init_turso_schema()

        despachos = db.query(Despacho).all()
        items = db.query(DespachoItem).all()

        despacho_cols = [c.name for c in Despacho.__table__.columns]
        item_cols = [c.name for c in DespachoItem.__table__.columns]

        stmts = []

        # 1. Sentencias para Despachos
        if despachos:
            cols_sql = ", ".join(despacho_cols)
            placeholders = ", ".join(["?"] * len(despacho_cols))
            desp_sql = f"INSERT OR REPLACE INTO despachos ({cols_sql}) VALUES ({placeholders})"

            for d in despachos:
                args = []
                for col in despacho_cols:
                    val = getattr(d, col, None)
                    args.append(self._convert_value_to_arg(val))
                stmts.append({"sql": desp_sql, "args": args})

        # 2. Sentencias para Items
        if items:
            cols_sql = ", ".join(item_cols)
            placeholders = ", ".join(["?"] * len(item_cols))
            item_sql = f"INSERT OR REPLACE INTO despacho_items ({cols_sql}) VALUES ({placeholders})"

            for it in items:
                args = []
                for col in item_cols:
                    val = getattr(it, col, None)
                    args.append(self._convert_value_to_arg(val))
                stmts.append({"sql": item_sql, "args": args})

        # Enviar en bloques de 40 sentencias
        chunk_size = 40
        for i in range(0, len(stmts), chunk_size):
            chunk = stmts[i:i + chunk_size]
            await self.execute_raw(chunk)

        return {
            "success": True,
            "despachos_subidos": len(despachos),
            "items_subidos": len(items)
        }

    async def pull_all_from_turso(self, db: Session) -> Dict[str, Any]:
        """Descarga todos los despachos y mercancías desde Turso a la base de datos local SQLite."""
        query_stmts = [
            {"sql": "SELECT * FROM despachos;"},
            {"sql": "SELECT * FROM despacho_items;"}
        ]
        res = await self.execute_raw(query_stmts)

        despachos_result = res.get("results", [])[0].get("response", {}).get("result", {})
        items_result = res.get("results", [])[1].get("response", {}).get("result", {})

        desp_cols = [c["name"] for c in despachos_result.get("cols", [])]
        desp_rows = despachos_result.get("rows", [])

        item_cols = [c["name"] for c in items_result.get("cols", [])]
        item_rows = items_result.get("rows", [])

        imported_despachos = 0
        imported_items = 0

        # Importar Despachos
        for row in desp_rows:
            row_dict = {}
            for col, val_obj in zip(desp_cols, row):
                val = val_obj.get("value") if isinstance(val_obj, dict) else val_obj
                row_dict[col] = val

            desp_id = int(row_dict.get("id"))
            existing = db.query(Despacho).filter(Despacho.id == desp_id).first()
            if not existing:
                existing = Despacho(id=desp_id)
                db.add(existing)

            for col in Despacho.__table__.columns:
                col_name = col.name
                if col_name in row_dict:
                    val = row_dict[col_name]
                    if val is None or val == "":
                        setattr(existing, col_name, None)
                    elif "float" in str(col.type).lower() or "real" in str(col.type).lower():
                        setattr(existing, col_name, float(val))
                    elif "int" in str(col.type).lower():
                        setattr(existing, col_name, int(val))
                    elif "date" in str(col.type).lower():
                        try:
                            if "T" in str(val):
                                setattr(existing, col_name, datetime.fromisoformat(str(val)))
                            else:
                                setattr(existing, col_name, date.fromisoformat(str(val)))
                        except Exception:
                            pass
                    elif "json" in str(col.type).lower():
                        try:
                            setattr(existing, col_name, json.loads(str(val)) if isinstance(val, str) else val)
                        except Exception:
                            setattr(existing, col_name, {})
                    else:
                        setattr(existing, col_name, str(val))

            imported_despachos += 1

        db.commit()

        # Importar Items
        for row in item_rows:
            row_dict = {}
            for col, val_obj in zip(item_cols, row):
                val = val_obj.get("value") if isinstance(val_obj, dict) else val_obj
                row_dict[col] = val

            item_id = int(row_dict.get("id"))
            existing_item = db.query(DespachoItem).filter(DespachoItem.id == item_id).first()
            if not existing_item:
                existing_item = DespachoItem(id=item_id)
                db.add(existing_item)

            for col in DespachoItem.__table__.columns:
                col_name = col.name
                if col_name in row_dict:
                    val = row_dict[col_name]
                    if val is None or val == "":
                        setattr(existing_item, col_name, None)
                    elif "float" in str(col.type).lower() or "real" in str(col.type).lower():
                        setattr(existing_item, col_name, float(val))
                    elif "int" in str(col.type).lower():
                        setattr(existing_item, col_name, int(val))
                    else:
                        setattr(existing_item, col_name, str(val))

            imported_items += 1

        db.commit()

        return {
            "success": True,
            "despachos_descargados": imported_despachos,
            "items_descargados": imported_items
        }
