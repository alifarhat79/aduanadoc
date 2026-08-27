import os
import sys
import io
import re
import hashlib
from datetime import datetime, date
import pandas as pd
from sqlalchemy.orm import Session

# Configurar encoding UTF-8 en consola
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from app.database import SessionLocal, engine, Base
from app.models import Despacho, DespachoItem
from app.services.backup_service import BackupService
from app.services.turso_service import TursoService
from app.services.normalizer import normalize_company_name
import asyncio

def parse_excel_date(v):
    if pd.isna(v):
        return None
    if isinstance(v, (int, float)):
        if 2000 <= v <= 2035:
            return date(int(v), 1, 1)
        # Fecha serial de Excel
        dt = pd.to_datetime('1899-12-30') + pd.to_timedelta(v, 'D')
        return dt.date()
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.date()
    
    s = str(v).strip()
    # Arreglar errores tipográficos comunes en el año (ej: /205 -> /2025)
    if re.search(r'[\/-]205$', s):
        s = re.sub(r'[\/-]205$', '/2025', s)
    elif re.search(r'[\/-]206$', s):
        s = re.sub(r'[\/-]206$', '/2026', s)

    try:
        dt = pd.to_datetime(s, dayfirst=True, errors='coerce')
        if pd.notna(dt):
            return dt.date()
    except Exception:
        pass
    return None

def parse_tax_pct(v):
    if pd.isna(v):
        return None
    s = str(v).strip().replace('%', '')
    try:
        val = float(s)
        # Si fue escrito como 6 -> 0.06, 14 -> 0.14, 1.5 -> 0.015
        if val > 1.0:
            return round(val / 100.0, 4)
        return round(val, 4)
    except Exception:
        return None

def clean_num(v):
    if pd.isna(v):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(',', '')
    try:
        return float(s)
    except Exception:
        return 0.0

def run_import(excel_path: str = "Planilla-Partida-Precio.xlsx", max_year: int = 2025, dry_run: bool = False):
    print("=" * 70)
    print(f"[*] INICIANDO IMPORTACIÓN HISTÓRICA DE DESPACHOS (< 2026)")
    print(f"[*] Archivo fuente: {excel_path}")
    print(f"[*] Modo: {'DRY RUN (Simulación)' if dry_run else 'IMPORTACIÓN REAL EN BASE DE DATOS'}")
    print("=" * 70)

    if not os.path.exists(excel_path):
        print(f"[!] ERROR: No se encontró el archivo {excel_path}")
        return

    # 1. Leer Excel
    print("[*] Leyendo hoja de cálculo...")
    df = pd.read_excel(excel_path, sheet_name=0)
    print(f"[*] Filas leídas: {len(df)}")

    # 2. Normalizar columnas clave
    print("[*] Normalizando fechas y agrupadores...")
    df['Fecha_parsed'] = df['Fecha '].apply(parse_excel_date).ffill()
    df['N_Despacho_clean'] = df['N_Despacho'].astype(str).str.strip()
    df['N_Despacho_clean'] = df['N_Despacho_clean'].replace(['nan', 'None', ''], None).ffill()
    df['Importadora_clean'] = df['Importadora'].apply(lambda x: normalize_company_name(str(x)) if pd.notna(x) and str(x).strip() not in ['nan', 'None', ''] else None).ffill()

    # 3. Filtrar años < 2026
    df['Anio'] = df['Fecha_parsed'].apply(lambda d: d.year if d else None)
    df_filtered = df[df['Anio'].notna() & (df['Anio'] <= max_year)].copy()

    print(f"[*] Filas históricas válidas (<= {max_year}): {len(df_filtered)}")
    print(f"[*] Distribución por año:")
    for anio, cnt in df_filtered['Anio'].value_counts().sort_index().items():
        print(f"    - Año {int(anio)}: {cnt} mercancías")

    # 4. Agrupar por despacho
    db: Session = SessionLocal()

    # Despachos existentes en DB para no duplicar
    existing_lookups = set(
        (d.numero_despacho, d.fecha_despacho)
        for d in db.query(Despacho.numero_despacho, Despacho.fecha_despacho).all()
    )
    print(f"[*] Despachos ya existentes en base de datos: {len(existing_lookups)}")

    groups = df_filtered.groupby(['N_Despacho_clean', 'Fecha_parsed', 'Importadora_clean'], sort=False)
    total_groups = len(groups)
    print(f"[*] Total de despachos históricos a procesar: {total_groups}")

    if dry_run:
        print("[*] Simulación completada con éxito. No se hicieron cambios.")
        db.close()
        return

    # Respaldo preventivo antes de insertar
    print("[*] Creando respaldo preventivo de la base de datos...")
    try:
        backup_res = BackupService().create_system_backup()
        print(f"[*] Respaldo preventivo creado: {backup_res.get('filename')}")
    except Exception as e:
        print(f"[!] Advertencia al crear backup: {e}")

    despachos_creados = 0
    items_creados = 0
    despachos_omitidos = 0

    batch_despachos = []

    for (nro_desp, f_desp, imp_nom), rows in groups:
        if (nro_desp, f_desp) in existing_lookups:
            despachos_omitidos += 1
            continue

        fob_acumulado = 0.0
        despacho_items = []
        origenes_detectados = set()

        for idx, (_, r) in enumerate(rows.iterrows(), start=1):
            cant = clean_num(r.get('Cantidade', 1))
            v_unit = clean_num(r.get('Valor Ajustado', 0))
            v_tot = clean_num(r.get('Total', 0))
            if v_tot == 0.0 and cant > 0 and v_unit > 0:
                v_tot = round(cant * v_unit, 2)
            fob_acumulado += v_tot

            ncm = str(r.get('Partida_Aranc', '') or '').strip()
            if ncm in ['nan', 'None']: ncm = None

            marca = str(r.get('Marca', '') or '').strip()
            if marca in ['nan', 'None', '']: marca = None

            desc = str(r.get('Descripcion', '') or '').strip()
            if desc in ['nan', 'None']: desc = "MERCADERIA HISTORICA"

            origen = str(r.get('ORIGEN', '') or '').strip()
            if origen in ['nan', 'None', '']: origen = None
            if origen: origenes_detectados.add(origen)

            iva_val = parse_tax_pct(r.get('Iva'))
            aranc_val = parse_tax_pct(r.get('%'))

            # Extraer posible código de producto / EAN de la descripción
            ean_match = re.search(r'\b(\d{8,14})\b', desc)
            codigo_prod = ean_match.group(1) if ean_match else None

            item = DespachoItem(
                numero_item=idx,
                codigo_ncm=ncm,
                codigo_producto=codigo_prod,
                descripcion=desc,
                marca=marca,
                cantidad=cant,
                unidad="UNIDAD",
                valor_unitario=v_unit,
                valor_total=v_tot,
                tasa_iva=iva_val,
                tasa_arancel=aranc_val,
                pais_origen=origen,
                pagina_origen=1
            )
            despacho_items.append(item)

        # Hash determinístico para despacho histórico
        hash_seed = f"HISTORICO_{f_desp.year}_{nro_desp}_{imp_nom}_{fob_acumulado}"
        hash_val = hashlib.sha256(hash_seed.encode('utf-8')).hexdigest()

        despacho = Despacho(
            numero_despacho=nro_desp,
            fecha_despacho=f_desp,
            importador_nombre=imp_nom,
            propietario=imp_nom,
            canal="VERDE",
            pais_origen=", ".join(sorted(origenes_detectados)) if origenes_detectados else None,
            valor_fob=round(fob_acumulado, 2),
            valor_flete=0.0,
            valor_seguro=0.0,
            valor_cif=round(fob_acumulado, 2),
            moneda="USD",
            archivo_pdf="HISTORICO_EXCEL",
            nombre_archivo_original="Planilla-Partida-Precio.xlsx",
            hash_archivo=hash_val,
            tipo_documento_detectado="PLANILLA_EXCEL_HISTORICA",
            metodo_extraccion="EXCEL_HISTORICO",
            numero_paginas=1,
            estado_procesamiento="PROCESADO",
            confianza_promedio=1.0,
            metadata_extraccion={
                "origen": "Planilla-Partida-Precio.xlsx",
                "items_count": len(despacho_items),
                "importado_el": datetime.now().isoformat()
            }
        )
        despacho.items = despacho_items
        batch_despachos.append(despacho)
        existing_lookups.add((nro_desp, f_desp))

        despachos_creados += 1
        items_creados += len(despacho_items)

        # Guardar en lotes de 50 despachos
        if len(batch_despachos) >= 50:
            db.add_all(batch_despachos)
            db.commit()
            batch_despachos = []
            print(f"[*] Progreso: {despachos_creados}/{total_groups} despachos insertados ({items_creados} mercancías)...")

    if batch_despachos:
        db.add_all(batch_despachos)
        db.commit()

    db.close()

    print("=" * 70)
    print(f"[✅] IMPORTACIÓN HISTÓRICA COMPLETADA CON ÉXITO")
    print(f"    - Despachos creados: {despachos_creados}")
    print(f"    - Mercancías/Ítems creados: {items_creados}")
    print(f"    - Despachos omitidos (ya existían): {despachos_omitidos}")
    print("=" * 70)

    # 5. Sincronizar automáticamente con Turso Cloud
    print("[*] Sincronizando nuevos registros con Turso Cloud...")
    try:
        db_sync = SessionLocal()
        turso = TursoService()
        if turso.is_configured():
            res_turso = asyncio.run(turso.push_all_to_turso(db_sync))
            print(f"[✅] Sincronización con Turso Cloud exitosa: {res_turso}")
        else:
            print("[!] Turso no configurado, sincronización en la nube omitida.")
        db_sync.close()
    except Exception as e:
        print(f"[!] Error al sincronizar con Turso: {e}")

if __name__ == "__main__":
    run_import(excel_path="Planilla-Partida-Precio.xlsx", max_year=2025, dry_run=False)
