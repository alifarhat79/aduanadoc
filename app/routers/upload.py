import os
import tempfile
import shutil
from typing import List, Optional
from fastapi import APIRouter, Depends, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.services.pipeline import process_pdf_file
from app.services.turso_service import TursoService
from app.templates_config import templates

router = APIRouter(prefix="/upload", tags=["Upload"])

@router.get("", response_class=HTMLResponse)
async def upload_view(request: Request):
    """Vista de carga individual y masiva con Drag & Drop."""
    return templates.TemplateResponse(
        request=request,
        name="upload.html",
        context={"max_mb": settings.MAX_UPLOAD_MB}
    )

@router.post("/process-batch")
async def process_batch_files(
    files: List[UploadFile] = File(...),
    allow_duplicate: bool = Form(False),
    propietario: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """
    Endpoint para procesar un lote de archivos PDF (1 a 100 archivos).
    Cada archivo se procesa de forma aislada para que un error en uno no interrumpa el lote.
    """
    results = []

    for file in files:
        filename = file.filename or "documento.pdf"
        
        # Validar extensión
        if not filename.lower().endswith(".pdf"):
            results.append({
                "filename": filename,
                "status": "ERROR",
                "message": "Formato no válido. Solo se admiten archivos .pdf",
                "despacho_id": None
            })
            continue

        # Guardar en archivo temporal para procesamiento seguro
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            content = await file.read()
            # Validar tamaño
            size_mb = len(content) / (1024 * 1024)
            if size_mb > settings.MAX_UPLOAD_MB:
                results.append({
                    "filename": filename,
                    "status": "ERROR",
                    "message": f"El archivo excede el tamaño máximo permitido ({settings.MAX_UPLOAD_MB} MB)",
                    "despacho_id": None
                })
                continue

            tmp_file.write(content)
            tmp_path = tmp_file.name

        try:
            despacho, log = process_pdf_file(
                db=db,
                file_path=tmp_path,
                original_filename=filename,
                allow_duplicate=allow_duplicate,
                propietario=propietario
            )
            
            # Auto-sincronización con Turso Cloud si está configurado
            turso_msg = ""
            turso = TursoService()
            if turso.is_configured():
                try:
                    await turso.push_despacho_to_turso(despacho.id, db)
                    turso_msg = " [Sincronizado a Turso Cloud ☁️]"
                except Exception as t_err:
                    turso_msg = f" (Alerta Turso: {str(t_err)})"

            # Notificación automática (Telegram / Webhook)
            try:
                from app.services.notification_service import NotificationService
                noti = NotificationService()
                if noti.enabled and (noti.telegram_token or noti.webhook_url):
                    desp_dict = {
                        "numero_despacho": despacho.numero_despacho or "S/N",
                        "importador_nombre": despacho.importador_nombre or "No identificado",
                        "propietario": despacho.propietario or "Sin Asignar",
                        "canal": despacho.canal or "VERDE",
                        "valor_fob": despacho.valor_fob or 0.0,
                        "valor_cif": despacho.valor_cif or 0.0,
                        "fecha_despacho": despacho.fecha_despacho.strftime("%d/%m/%Y") if despacho.fecha_despacho else "-",
                        "nombre_archivo_original": despacho.nombre_archivo_original or filename,
                    }
                    noti.notify_new_despacho(despacho_dict=desp_dict, items_count=len(despacho.items), source="Carga Manual")
            except Exception as n_err:
                logger.warning(f"No se pudo enviar notificación de nuevo despacho: {n_err}")

            results.append({
                "filename": filename,
                "status": despacho.estado_procesamiento,
                "message": f"Procesado exitosamente. Confianza: {int(despacho.confianza_promedio * 100)}%{turso_msg}",
                "despacho_id": despacho.id,
                "numero_despacho": despacho.numero_despacho or "S/N",
                "importador": despacho.importador_nombre or "No identificado",
                "fob": despacho.valor_fob,
                "cif": despacho.valor_cif
            })
        except Exception as e:
            results.append({
                "filename": filename,
                "status": "ERROR",
                "message": str(e),
                "despacho_id": None
            })
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    # Auto-Backup del sistema tras añadir despachos exitosamente
    exitosos = [r for r in results if r.get("status") not in ["ERROR", "DUPLICADO"]]
    if exitosos:
        try:
            from app.services.backup_service import BackupService
            backup_svc = BackupService()
            backup_svc.create_system_backup(reason=f"AUTO_UPLOAD_{len(exitosos)}_DESPACHOS")
        except Exception as b_err:
            pass

    return JSONResponse(content={"results": results})

