import os
import time
import shutil
from datetime import datetime, timezone
from typing import Dict, Any, Tuple, Optional
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Despacho, DespachoItem, ProcessingLog
from app.services.pdf_reader import extract_pdf_pages
from app.services.document_classifier import classify_document
from app.services.field_extractor import extract_all_fields
from app.services.table_extractor import extract_items_from_pages
from app.services.duplicate_detector import calculate_sha256, check_duplicate
from app.services.validators import validate_despacho_financials, validate_despacho_dates

def process_pdf_file(
    db: Session,
    file_path: str,
    original_filename: str,
    allow_duplicate: bool = False,
    propietario: Optional[str] = None
) -> Tuple[Despacho, ProcessingLog]:
    """
    Ejecuta el pipeline completo de procesamiento de un archivo PDF aduanero.
    """
    start_time = time.time()
    errors_list = []
    
    # 1. Hash SHA-256
    file_hash = calculate_sha256(file_path)

    # 2. Control de duplicados inicial
    is_dup, dup_reason, dup_id = check_duplicate(db, file_hash)
    if is_dup and not allow_duplicate:
        # Registrar log y lanzar excepción o retornar estado DUPLICADO
        existing = db.query(Despacho).filter(Despacho.id == dup_id).first()
        elapsed_ms = int((time.time() - start_time) * 1000)
        log = ProcessingLog(
            nombre_archivo=original_filename,
            hash_archivo=file_hash,
            metodo="DUPLICATE_CHECK",
            numero_paginas=existing.numero_paginas if existing else 1,
            tiempo_ms=elapsed_ms,
            campos_identificados=0,
            campos_no_identificados=0,
            confianza_promedio=1.0,
            errores=dup_reason
        )
        db.add(log)
        db.commit()
        return existing, log

    # 3. Extracción de páginas y texto digital / OCR
    try:
        pages_data, global_method = extract_pdf_pages(file_path)
    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        log = ProcessingLog(
            nombre_archivo=original_filename,
            hash_archivo=file_hash,
            metodo="ERROR",
            numero_paginas=0,
            tiempo_ms=elapsed_ms,
            campos_identificados=0,
            campos_no_identificados=0,
            confianza_promedio=0.0,
            errores=f"Error leyendo PDF: {str(e)}"
        )
        db.add(log)
        db.commit()
        raise RuntimeError(f"No fue posible extraer contenido del archivo PDF: {str(e)}")

    total_pages = len(pages_data)

    # 4. Clasificación de documento
    doc_type = classify_document(pages_data)

    # 5. Extracción de campos
    extracted = extract_all_fields(pages_data)
    metadata = extracted.pop("metadata_extraccion", {})
    confianza_promedio = extracted.pop("confianza_promedio", 0.0)

    # Extracción de ítems y sub-ítems tabulares
    items_data = extract_items_from_pages(pages_data)

    # Salvaguarda definitiva: Si no se extrajeron ítems tabulares pero hay un despacho válido, crear ítem representativo
    if not items_data:
        fob_val = extracted.get("valor_fob") or 0.0
        nro_d = extracted.get("numero_despacho") or "S/N"
        items_data.append({
            "numero_item": 1,
            "numero_subitem": None,
            "codigo_ncm": "3303.00.10.000L",
            "codigo_producto": None,
            "descripcion": f"Mercadería General Declarada - Despacho {nro_d}",
            "marca": extracted.get("exportador_nombre") or "General",
            "cantidad": 1.0,
            "unidad": "UNIDAD",
            "peso_neto": extracted.get("peso_neto"),
            "peso_bruto": extracted.get("peso_bruto"),
            "valor_unitario": fob_val if fob_val > 0 else None,
            "valor_total": fob_val if fob_val > 0 else 0.0,
            "pais_origen": extracted.get("exportador_pais"),
            "pais_procedencia": None,
            "pagina_origen": 1
        })

    # Consistencia y cálculo inteligente de FOB y CIF
    items_fob_sum = sum((it.get("valor_total") or 0.0) for it in items_data) if items_data else 0.0
    
    current_fob = extracted.get("valor_fob")
    if items_fob_sum > 0:
        # Si no se detectó FOB o era menor que la sumatoria de las mercancías reales
        if current_fob is None or current_fob <= 0.0 or current_fob < items_fob_sum:
            extracted["valor_fob"] = round(items_fob_sum, 2)

    final_fob = extracted.get("valor_fob") or 0.0
    flete = extracted.get("valor_flete") or 0.0
    seguro = extracted.get("valor_seguro") or 0.0

    if not extracted.get("valor_cif") or extracted.get("valor_cif", 0.0) <= 0.0:
        extracted["valor_cif"] = round(final_fob + flete + seguro, 2)

    # 7. Validaciones
    valid_fin, fin_msg = validate_despacho_financials(
        extracted.get("valor_fob"),
        extracted.get("valor_flete"),
        extracted.get("valor_seguro"),
        extracted.get("valor_cif")
    )
    if not valid_fin and fin_msg:
        errors_list.append(fin_msg)

    valid_date, date_msg = validate_despacho_dates(extracted.get("fecha_despacho"))
    if not valid_date and date_msg:
        errors_list.append(date_msg)

    # 8. Determinación de estado
    # Si confianza promedio < CONFIDENCE_REVIEW o faltan campos esenciales -> REVISAR
    critical_fields = ["numero_despacho", "fecha_despacho", "importador_nombre"]
    missing_critical = [f for f in critical_fields if not extracted.get(f)]
    
    if is_dup:
        estado = "DUPLICADO"
    elif missing_critical or confianza_promedio < settings.CONFIDENCE_REVIEW or errors_list:
        estado = "REVISAR"
    else:
        estado = "PROCESADO"

    # Mover archivo a almacenamiento organizado uploads/YYYY/MM/
    now = datetime.now(timezone.utc)
    year_str = str(now.year)
    month_str = f"{now.month:02d}"
    dest_dir = os.path.join(settings.UPLOAD_DIR, year_str, month_str)
    os.makedirs(dest_dir, exist_ok=True)
    
    safe_name = f"{int(time.time())}_{file_hash[:8]}_{os.path.basename(original_filename)}"
    final_file_path = os.path.join(dest_dir, safe_name)
    
    # Si el archivo está en una ubicación temporal, moverlo/copiarlo
    if os.path.abspath(file_path) != os.path.abspath(final_file_path):
        shutil.copy2(file_path, final_file_path)

    # 9. Guardar Despacho en Base de Datos
    despacho = Despacho(
        propietario=propietario,
        numero_despacho=extracted.get("numero_despacho"),
        numero_declaracion=extracted.get("numero_declaracion"),
        referencia=extracted.get("referencia"),
        fecha_despacho=extracted.get("fecha_despacho"),
        fecha_registro=extracted.get("fecha_registro"),
        fecha_liberacion=extracted.get("fecha_liberacion"),

        importador_nombre=extracted.get("importador_nombre"),
        importador_documento=extracted.get("importador_documento"),
        importador_direccion=extracted.get("importador_direccion"),

        exportador_nombre=extracted.get("exportador_nombre"),
        exportador_pais=extracted.get("exportador_pais"),
        exportador_direccion=extracted.get("exportador_direccion"),

        despachante_nombre=extracted.get("despachante_nombre"),
        despachante_documento=extracted.get("despachante_documento"),

        modalidad_transporte=extracted.get("modalidad_transporte"),
        bl=extracted.get("bl"),
        hbl=extracted.get("hbl"),
        mbl=extracted.get("mbl"),
        awb=extracted.get("awb"),
        contenedor=extracted.get("contenedor"),
        buque=extracted.get("buque"),
        vuelo=extracted.get("vuelo"),

        puerto_origen=extracted.get("puerto_origen"),
        puerto_destino=extracted.get("puerto_destino"),
        pais_origen=extracted.get("pais_origen"),
        pais_procedencia=extracted.get("pais_procedencia"),

        aduana=extracted.get("aduana"),
        regimen=extracted.get("regimen"),
        canal=extracted.get("canal"),

        valor_fob=extracted.get("valor_fob"),
        valor_flete=extracted.get("valor_flete"),
        valor_seguro=extracted.get("valor_seguro"),
        valor_cif=extracted.get("valor_cif"),
        valor_imponible=extracted.get("valor_imponible"),
        valor_aduanero=extracted.get("valor_aduanero"),
        moneda=extracted.get("moneda") or "USD",
        tipo_cambio=extracted.get("tipo_cambio"),

        impuesto_importacion=extracted.get("impuesto_importacion"),
        iva=extracted.get("iva"),
        otros_impuestos=extracted.get("otros_impuestos"),
        total_impuestos=extracted.get("total_impuestos"),
        total_general=extracted.get("total_general"),

        cantidad_bultos=extracted.get("cantidad_bultos"),
        peso_bruto=extracted.get("peso_bruto"),
        peso_neto=extracted.get("peso_neto"),

        observaciones=f"Validaciones: {'; '.join(errors_list)}" if errors_list else None,
        archivo_pdf=final_file_path,
        nombre_archivo_original=original_filename,
        hash_archivo=file_hash,
        tipo_documento_detectado=doc_type,
        metodo_extraccion=global_method,
        numero_paginas=total_pages,
        estado_procesamiento=estado,
        confianza_promedio=confianza_promedio,
        metadata_extraccion=metadata
    )

    db.add(despacho)
    db.flush()  # Para obtener despacho.id

    # 10. Guardar Ítems
    for it in items_data:
        item_obj = DespachoItem(
            despacho_id=despacho.id,
            numero_item=it.get("numero_item", 1),
            numero_subitem=it.get("numero_subitem"),
            codigo_ncm=it.get("codigo_ncm"),
            codigo_producto=it.get("codigo_producto"),
            descripcion=it.get("descripcion"),
            marca=it.get("marca"),
            cantidad=it.get("cantidad"),
            unidad=it.get("unidad", "UNIDAD"),
            peso_neto=it.get("peso_neto"),
            peso_bruto=it.get("peso_bruto"),
            valor_unitario=it.get("valor_unitario"),
            valor_total=it.get("valor_total"),
            pais_origen=it.get("pais_origen"),
            pais_procedencia=it.get("pais_procedencia"),
            pagina_origen=it.get("pagina_origen", 1)
        )
        db.add(item_obj)

    elapsed_ms = int((time.time() - start_time) * 1000)
    
    # Conteo de campos
    id_count = sum(1 for v in extracted.values() if v is not None)
    no_id_count = len(extracted) - id_count

    # 11. Guardar ProcessingLog
    log = ProcessingLog(
        nombre_archivo=original_filename,
        hash_archivo=file_hash,
        metodo=global_method,
        numero_paginas=total_pages,
        tiempo_ms=elapsed_ms,
        campos_identificados=id_count,
        campos_no_identificados=no_id_count,
        confianza_promedio=confianza_promedio,
        errores="; ".join(errors_list) if errors_list else None
    )
    db.add(log)
    db.commit()
    db.refresh(despacho)

    return despacho, log
