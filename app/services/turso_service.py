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

        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(self.pipeline_url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise Exception(f"Error de Turso ({resp.status_code}): {resp.text}")
            return resp.json()

    async def test_connection(self) -> Dict[str, Any]:
        """Prueba la conectividad ejecutando un SELECT 1."""
        res = await self.execute_raw([{"sql": "SELECT 1 as connected"}])
        return {"success": True, "message": "Conexión a Turso establecida exitosamente", "response": res}

    async def init_turso_schema(self):
        """Crea las tablas e índices en Turso con exactamente las mismas columnas que el modelo local."""
        def build_create_table(model, table_name):
            cols_def = []
            for col in model.__table__.columns:
                cname = col.name
                ctype = "INTEGER" if "int" in str(col.type).lower() else ("REAL" if ("float" in str(col.type).lower() or "real" in str(col.type).lower()) else "TEXT")
                if col.primary_key:
                    cols_def.append(f"{cname} {ctype} PRIMARY KEY")
                else:
                    cols_def.append(f"{cname} {ctype}")
            return f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(cols_def)});"

        schema_stmts = [
            {"sql": build_create_table(Despacho, "despachos")},
            {"sql": build_create_table(DespachoItem, "despacho_items")},
            {"sql": "CREATE INDEX IF NOT EXISTS idx_turso_despachos_num ON despachos(numero_despacho);"},
            {"sql": "CREATE INDEX IF NOT EXISTS idx_turso_despachos_hash ON despachos(hash_archivo);"},
            {"sql": "CREATE INDEX IF NOT EXISTS idx_turso_items_despacho_id ON despacho_items(despacho_id);"}
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

    async def push_despacho_to_turso(self, despacho_id: int, db: Session) -> Dict[str, Any]:
        """
        Sube o actualiza un despacho específico y todos sus ítems a Turso.
        Identifica canónicamente el despacho en Turso por numero_despacho / hash_archivo
        para evitar duplicados o discrepancias de ID entre PCs.
        """
        if not self.is_configured():
            return {"success": False, "error": "Turso no configurado"}

        await self.init_turso_schema()

        despacho = db.query(Despacho).filter(Despacho.id == despacho_id).first()
        if not despacho:
            return {"success": False, "error": "Despacho no encontrado"}

        items = db.query(DespachoItem).filter(DespachoItem.despacho_id == despacho_id).all()

        # 1. Verificar si ya existe en Turso por numero_despacho o hash_archivo
        turso_target_id = None
        check_stmts = []
        if despacho.numero_despacho and despacho.numero_despacho.strip() not in ("", "S/N", "None"):
            check_stmts.append({
                "sql": "SELECT id FROM despachos WHERE numero_despacho = ? LIMIT 1;",
                "args": [{"type": "text", "value": despacho.numero_despacho.strip()}]
            })
        elif despacho.hash_archivo:
            check_stmts.append({
                "sql": "SELECT id FROM despachos WHERE hash_archivo = ? LIMIT 1;",
                "args": [{"type": "text", "value": despacho.hash_archivo.strip()}]
            })

        if check_stmts:
            try:
                res_check = await self.execute_raw(check_stmts)
                rows = res_check.get("results", [])[0].get("response", {}).get("result", {}).get("rows", [])
                if rows and len(rows) > 0:
                    val_obj = rows[0][0]
                    turso_target_id = int(val_obj.get("value") if isinstance(val_obj, dict) else val_obj)
            except Exception as e:
                logger.debug(f"[TursoService] Error comprobando existencia en Turso: {e}")

        # Si no existía en Turso, usamos el ID local como sugerencia
        if turso_target_id is None:
            turso_target_id = despacho.id

        despacho_cols = [c.name for c in Despacho.__table__.columns]
        item_cols = [c.name for c in DespachoItem.__table__.columns if c.name != "id"]

        stmts = []

        # 2. Despacho: UPSERT / REPLACE con turso_target_id
        cols_sql = ", ".join(despacho_cols)
        placeholders = ", ".join(["?"] * len(despacho_cols))
        desp_sql = f"INSERT OR REPLACE INTO despachos ({cols_sql}) VALUES ({placeholders})"
        
        args = []
        for col in despacho_cols:
            if col == "id":
                args.append(self._convert_value_to_arg(turso_target_id))
            else:
                args.append(self._convert_value_to_arg(getattr(despacho, col, None)))
        stmts.append({"sql": desp_sql, "args": args})

        # 3. Eliminar items anteriores en Turso para este despacho
        stmts.append({
            "sql": "DELETE FROM despacho_items WHERE despacho_id = ?",
            "args": [{"type": "integer", "value": str(turso_target_id)}]
        })

        # 4. Insertar items
        if items:
            item_cols_with_desp = ["despacho_id"] + [c for c in item_cols if c != "despacho_id"]
            item_cols_sql = ", ".join(item_cols_with_desp)
            item_placeholders = ", ".join(["?"] * len(item_cols_with_desp))
            item_sql = f"INSERT INTO despacho_items ({item_cols_sql}) VALUES ({item_placeholders})"
            for it in items:
                it_args = [self._convert_value_to_arg(turso_target_id)]
                for col in item_cols_with_desp[1:]:
                    it_args.append(self._convert_value_to_arg(getattr(it, col, None)))
                stmts.append({"sql": item_sql, "args": it_args})

        # Enviar en bloques si supera 40 sentencias
        chunk_size = 40
        for i in range(0, len(stmts), chunk_size):
            chunk = stmts[i:i + chunk_size]
            await self.execute_raw(chunk)

        logger.info(f"[TursoService] Despacho '{despacho.numero_despacho}' (ID local: {despacho_id}, ID Turso: {turso_target_id}) sincronizado a Turso Cloud ({len(items)} ítems).")
        return {"success": True, "despacho_id": despacho_id, "turso_id": turso_target_id, "items": len(items)}

    def sync_push_despacho(self, despacho_id: int, db: Session) -> Dict[str, Any]:
        """Versión sincrónica para ser llamada desde hilos o procesos de fondo."""
        if not self.is_configured():
            return {"success": False, "error": "Turso no configurado"}
        try:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, self.push_despacho_to_turso(despacho_id, db))
                    return future.result()
            else:
                return asyncio.run(self.push_despacho_to_turso(despacho_id, db))
        except Exception as e:
            logger.warning(f"[TursoService] Error en sync_push_despacho ID {despacho_id}: {e}")
            return {"success": False, "error": str(e)}

    async def push_all_to_turso(self, db: Session) -> Dict[str, Any]:
        """Sube todos los despachos y mercancías locales a Turso asegurando consistencia canónica."""
        await self.init_turso_schema()

        despachos = db.query(Despacho).all()
        pushed_count = 0
        total_items = 0

        for d in despachos:
            res = await self.push_despacho_to_turso(d.id, db)
            if res.get("success"):
                pushed_count += 1
                total_items += res.get("items", 0)

        return {
            "success": True,
            "despachos_subidos": pushed_count,
            "items_subidos": total_items
        }

    async def pull_despachos_quick(self, db: Session) -> Dict[str, Any]:
        """
        Sincronización rápida y canónica de despachos y mercancías desde Turso Cloud hacia esta PC.
        Mapea por 'numero_despacho' o 'hash_archivo' para que las alteraciones de Dueño (propietario),
        canales, datos y mercancías se apliquen inmediatamente al despacho correcto en cualquier PC.
        """
        if not self.is_configured():
            return {"success": False, "error": "Turso no configurado"}

        # Consultar todos los despachos y mercancías de Turso
        query_stmts = [
            {"sql": "SELECT * FROM despachos ORDER BY id DESC;"},
            {"sql": "SELECT * FROM despacho_items;"}
        ]
        res = await self.execute_raw(query_stmts)

        results = res.get("results", [])
        if not results:
            return {"success": False, "error": "No se recibieron datos de Turso"}

        despachos_result = results[0].get("response", {}).get("result", {})
        items_result = results[1].get("response", {}).get("result", {}) if len(results) > 1 else {}

        desp_cols = [c["name"] for c in despachos_result.get("cols", [])]
        desp_rows = despachos_result.get("rows", [])

        item_cols = [c["name"] for c in items_result.get("cols", [])]
        item_rows = items_result.get("rows", [])

        turso_id_to_local_id: Dict[int, int] = {}
        updated_count = 0
        inserted_count = 0
        inserted_despachos = []

        for row in desp_rows:
            row_dict = {}
            for col, val_obj in zip(desp_cols, row):
                val = val_obj.get("value") if isinstance(val_obj, dict) else val_obj
                row_dict[col] = val

            num_desp = (row_dict.get("numero_despacho") or "").strip()
            hash_arch = (row_dict.get("hash_archivo") or "").strip()
            nombre_arch = (row_dict.get("nombre_archivo_original") or "").strip()
            turso_id = int(row_dict.get("id")) if row_dict.get("id") is not None else None

            # 1. Búsqueda canónica en la base local:
            existing = None
            if num_desp and num_desp not in ("", "S/N", "None"):
                existing = db.query(Despacho).filter(Despacho.numero_despacho == num_desp).first()

            if not existing and hash_arch:
                existing = db.query(Despacho).filter(Despacho.hash_archivo == hash_arch).first()

            if not existing and nombre_arch:
                existing = db.query(Despacho).filter(Despacho.nombre_archivo_original == nombre_arch).first()

            if not existing and turso_id is not None:
                existing = db.query(Despacho).filter(Despacho.id == turso_id).first()

            is_new = False
            if not existing:
                existing = Despacho()
                db.add(existing)
                is_new = True

            for col in Despacho.__table__.columns:
                col_name = col.name
                if col_name == "id" and existing.id:
                    continue
                if col_name in row_dict:
                    val = row_dict[col_name]
                    try:
                        if val is None or val == "":
                            setattr(existing, col_name, None)
                        elif "float" in str(col.type).lower() or "real" in str(col.type).lower():
                            setattr(existing, col_name, float(val))
                        elif "int" in str(col.type).lower():
                            setattr(existing, col_name, int(val))
                        elif "date" in str(col.type).lower():
                            if "T" in str(val):
                                setattr(existing, col_name, datetime.fromisoformat(str(val)))
                            else:
                                setattr(existing, col_name, date.fromisoformat(str(val)))
                        elif "json" in str(col.type).lower():
                            setattr(existing, col_name, json.loads(str(val)) if isinstance(val, str) else val)
                        else:
                            setattr(existing, col_name, str(val))
                    except Exception:
                        setattr(existing, col_name, str(val) if val is not None else None)

            db.flush()
            if turso_id is not None:
                turso_id_to_local_id[turso_id] = existing.id

            if is_new:
                inserted_despachos.append(existing)
                inserted_count += 1
            else:
                updated_count += 1

        db.commit()

        # 2. Agrupar Items por turso_despacho_id e insertarlos
        items_by_turso_desp: Dict[int, List[Dict[str, Any]]] = {}
        for row in item_rows:
            row_dict = {}
            for col, val_obj in zip(item_cols, row):
                val = val_obj.get("value") if isinstance(val_obj, dict) else val_obj
                row_dict[col] = val

            t_desp_id = int(row_dict.get("despacho_id")) if row_dict.get("despacho_id") is not None else None
            if t_desp_id is not None:
                if t_desp_id not in items_by_turso_desp:
                    items_by_turso_desp[t_desp_id] = []
                items_by_turso_desp[t_desp_id].append(row_dict)

        imported_items_count = 0
        for t_desp_id, t_items in items_by_turso_desp.items():
            loc_id = turso_id_to_local_id.get(t_desp_id)
            if not loc_id:
                continue

            # Reemplazar ítems locales con los de la nube
            db.query(DespachoItem).filter(DespachoItem.despacho_id == loc_id).delete()
            for i_data in t_items:
                item_obj = DespachoItem(despacho_id=loc_id)
                for col in DespachoItem.__table__.columns:
                    col_name = col.name
                    if col_name in ("id", "despacho_id"):
                        continue
                    if col_name in i_data:
                        val = i_data[col_name]
                        try:
                            if val is None or val == "":
                                setattr(item_obj, col_name, None)
                            elif "float" in str(col.type).lower() or "real" in str(col.type).lower():
                                setattr(item_obj, col_name, float(val))
                            elif "int" in str(col.type).lower():
                                setattr(item_obj, col_name, int(val))
                            else:
                                setattr(item_obj, col_name, str(val))
                        except Exception:
                            setattr(item_obj, col_name, str(val) if val is not None else None)
                db.add(item_obj)
                imported_items_count += 1

        db.commit()

        # Enviar notificación a Telegram por cada despacho nuevo traído de la nube
        if inserted_despachos:
            try:
                from app.services.notification_service import NotificationService
                noti = NotificationService()
                if noti.enabled and (noti.telegram_token or noti.webhook_url):
                    for d_new in inserted_despachos:
                        desp_dict = {
                            "numero_despacho": d_new.numero_despacho or "S/N",
                            "importador_nombre": d_new.importador_nombre or "No identificado",
                            "propietario": d_new.propietario or "Sin Asignar",
                            "canal": d_new.canal or "VERDE",
                            "valor_fob": d_new.valor_fob or 0.0,
                            "valor_cif": d_new.valor_cif or 0.0,
                            "fecha_despacho": d_new.fecha_despacho.strftime("%d/%m/%Y") if d_new.fecha_despacho else "-",
                            "nombre_archivo_original": d_new.nombre_archivo_original or "despacho.pdf",
                        }
                        noti.notify_new_despacho(
                            despacho_dict=desp_dict,
                            items_count=len(d_new.items) if hasattr(d_new, 'items') and d_new.items else 0,
                            source="Sincronización Nube (Turso)"
                        )
            except Exception as n_err:
                logger.warning(f"Error enviando notificación tras sync nube: {n_err}")

        return {
            "success": True,
            "despachos_actualizados": updated_count,
            "despachos_nuevos": inserted_count,
            "items_sincronizados": imported_items_count,
            "message": f"Sincronizados {updated_count + inserted_count} despachos y {imported_items_count} mercancías desde Turso Cloud."
        }

    async def pull_all_from_turso(self, db: Session) -> Dict[str, Any]:
        """Descarga todos los despachos y mercancías desde Turso a la base de datos local SQLite con integridad relacional."""
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

        turso_id_to_local_id: Dict[int, int] = {}
        imported_despachos = 0
        imported_items = 0
        new_despachos_inserted = []

        # 1. Importar y reconciliar Despachos
        for row in desp_rows:
            row_dict = {}
            for col, val_obj in zip(desp_cols, row):
                val = val_obj.get("value") if isinstance(val_obj, dict) else val_obj
                row_dict[col] = val

            num_desp = (row_dict.get("numero_despacho") or "").strip()
            hash_arch = (row_dict.get("hash_archivo") or "").strip()
            nombre_arch = (row_dict.get("nombre_archivo_original") or "").strip()
            turso_id = int(row_dict.get("id")) if row_dict.get("id") is not None else None

            existing = None
            if num_desp and num_desp not in ("", "S/N", "None"):
                existing = db.query(Despacho).filter(Despacho.numero_despacho == num_desp).first()

            if not existing and hash_arch:
                existing = db.query(Despacho).filter(Despacho.hash_archivo == hash_arch).first()

            if not existing and nombre_arch:
                existing = db.query(Despacho).filter(Despacho.nombre_archivo_original == nombre_arch).first()

            if not existing and turso_id is not None:
                existing = db.query(Despacho).filter(Despacho.id == turso_id).first()

            if not existing:
                existing = Despacho()
                db.add(existing)
                new_despachos_inserted.append(existing)

            for col in Despacho.__table__.columns:
                col_name = col.name
                if col_name == "id" and existing.id:
                    continue
                if col_name in row_dict:
                    val = row_dict[col_name]
                    try:
                        if val is None or val == "":
                            setattr(existing, col_name, None)
                        elif "float" in str(col.type).lower() or "real" in str(col.type).lower():
                            setattr(existing, col_name, float(val))
                        elif "int" in str(col.type).lower():
                            setattr(existing, col_name, int(val))
                        elif "date" in str(col.type).lower():
                            if "T" in str(val):
                                setattr(existing, col_name, datetime.fromisoformat(str(val)))
                            else:
                                setattr(existing, col_name, date.fromisoformat(str(val)))
                        elif "json" in str(col.type).lower():
                            setattr(existing, col_name, json.loads(str(val)) if isinstance(val, str) else val)
                        else:
                            setattr(existing, col_name, str(val))
                    except Exception:
                        setattr(existing, col_name, str(val) if val is not None else None)

            db.flush()
            if turso_id is not None:
                turso_id_to_local_id[turso_id] = existing.id
            imported_despachos += 1

        db.commit()

        # 2. Agrupar Items por turso_despacho_id
        items_by_turso_desp: Dict[int, List[Dict[str, Any]]] = {}
        for row in item_rows:
            row_dict = {}
            for col, val_obj in zip(item_cols, row):
                val = val_obj.get("value") if isinstance(val_obj, dict) else val_obj
                row_dict[col] = val

            t_desp_id = int(row_dict.get("despacho_id")) if row_dict.get("despacho_id") is not None else None
            if t_desp_id is not None:
                if t_desp_id not in items_by_turso_desp:
                    items_by_turso_desp[t_desp_id] = []
                items_by_turso_desp[t_desp_id].append(row_dict)

        # 3. Insertar Items asociados correctamente a cada despacho local
        for t_desp_id, t_items in items_by_turso_desp.items():
            loc_id = turso_id_to_local_id.get(t_desp_id)
            if not loc_id:
                continue

            # Eliminar items locales existentes para este despacho
            db.query(DespachoItem).filter(DespachoItem.despacho_id == loc_id).delete()

            for item_row in t_items:
                new_item = DespachoItem(despacho_id=loc_id)
                for col in DespachoItem.__table__.columns:
                    col_name = col.name
                    if col_name in ("id", "despacho_id"):
                        continue
                    if col_name in item_row:
                        val = item_row[col_name]
                        try:
                            if val is None or val == "":
                                setattr(new_item, col_name, None)
                            elif "float" in str(col.type).lower() or "real" in str(col.type).lower():
                                setattr(new_item, col_name, float(val))
                            elif "int" in str(col.type).lower():
                                setattr(new_item, col_name, int(val))
                            else:
                                setattr(new_item, col_name, str(val))
                        except Exception:
                            setattr(new_item, col_name, str(val) if val is not None else None)
                db.add(new_item)
                imported_items += 1

        db.commit()

        # Enviar notificación a Telegram por cada despacho nuevo traído de la nube
        if new_despachos_inserted:
            try:
                from app.services.notification_service import NotificationService
                noti = NotificationService()
                if noti.enabled and (noti.telegram_token or noti.webhook_url):
                    for d_new in new_despachos_inserted:
                        desp_dict = {
                            "numero_despacho": d_new.numero_despacho or "S/N",
                            "importador_nombre": d_new.importador_nombre or "No identificado",
                            "propietario": d_new.propietario or "Sin Asignar",
                            "canal": d_new.canal or "VERDE",
                            "valor_fob": d_new.valor_fob or 0.0,
                            "valor_cif": d_new.valor_cif or 0.0,
                            "fecha_despacho": d_new.fecha_despacho.strftime("%d/%m/%Y") if d_new.fecha_despacho else "-",
                            "nombre_archivo_original": d_new.nombre_archivo_original or "despacho.pdf",
                        }
                        noti.notify_new_despacho(
                            despacho_dict=desp_dict,
                            items_count=len(d_new.items) if hasattr(d_new, 'items') and d_new.items else 0,
                            source="Sincronización Nube Completa (Turso)"
                        )
            except Exception as n_err:
                logger.warning(f"Error enviando notificación tras pull completo: {n_err}")

        return {
            "success": True,
            "despachos_descargados": imported_despachos,
            "items_descargados": imported_items
        }

    async def delete_despacho_from_turso(self, numero_despacho: Optional[str] = None, hash_archivo: Optional[str] = None, despacho_id: Optional[int] = None) -> Dict[str, Any]:
        """Elimina un despacho y sus ítems de Turso Cloud."""
        if not self.is_configured():
            return {"success": False, "error": "Turso no configurado"}

        stmts = []
        if numero_despacho and numero_despacho.strip() not in ("", "S/N"):
            stmts.append({
                "sql": "DELETE FROM despacho_items WHERE despacho_id IN (SELECT id FROM despachos WHERE numero_despacho = ?);",
                "args": [{"type": "text", "value": numero_despacho.strip()}]
            })
            stmts.append({
                "sql": "DELETE FROM despachos WHERE numero_despacho = ?;",
                "args": [{"type": "text", "value": numero_despacho.strip()}]
            })
        elif hash_archivo:
            stmts.append({
                "sql": "DELETE FROM despacho_items WHERE despacho_id IN (SELECT id FROM despachos WHERE hash_archivo = ?);",
                "args": [{"type": "text", "value": hash_archivo.strip()}]
            })
            stmts.append({
                "sql": "DELETE FROM despachos WHERE hash_archivo = ?;",
                "args": [{"type": "text", "value": hash_archivo.strip()}]
            })
        elif despacho_id:
            stmts.append({
                "sql": "DELETE FROM despacho_items WHERE despacho_id = ?;",
                "args": [{"type": "integer", "value": str(despacho_id)}]
            })
            stmts.append({
                "sql": "DELETE FROM despachos WHERE id = ?;",
                "args": [{"type": "integer", "value": str(despacho_id)}]
            })

        if stmts:
            try:
                await self.execute_raw(stmts)
                return {"success": True}
            except Exception as e:
                logger.warning(f"[TursoService] Error al eliminar despacho en Turso: {e}")
                return {"success": False, "error": str(e)}

        return {"success": False, "error": "Sin identificadores válidos"}
