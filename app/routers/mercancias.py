from fastapi import APIRouter, Depends, Request, Query, Response
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, or_
from typing import Optional, List
import io
import csv
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from app.database import get_db
from app.models import Despacho, DespachoItem
from app.templates_config import templates
from app.services.normalizer import parse_date

router = APIRouter(prefix="/mercancias", tags=["Mercancías"])

def get_filtered_items_query(
    db: Session,
    q: Optional[str] = None,
    marca: Optional[str] = None,
    ncm: Optional[str] = None,
    propietario: Optional[str] = None,
    despacho_id: Optional[int] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None
):
    query = db.query(DespachoItem, Despacho).join(Despacho, DespachoItem.despacho_id == Despacho.id)

    if q:
        query = query.filter(
            or_(
                DespachoItem.descripcion.ilike(f"%{q}%"),
                DespachoItem.marca.ilike(f"%{q}%"),
                DespachoItem.codigo_producto.ilike(f"%{q}%"),
                DespachoItem.codigo_ncm.ilike(f"%{q}%"),
                Despacho.numero_despacho.ilike(f"%{q}%"),
                Despacho.importador_nombre.ilike(f"%{q}%"),
                Despacho.propietario.ilike(f"%{q}%")
            )
        )
    if marca:
        query = query.filter(DespachoItem.marca.ilike(marca.strip()))
    if ncm:
        query = query.filter(DespachoItem.codigo_ncm == ncm)
    if propietario:
        query = query.filter(Despacho.propietario == propietario)
    if despacho_id:
        query = query.filter(DespachoItem.despacho_id == despacho_id)

    if fecha_desde:
        d_desde = parse_date(fecha_desde)
        if d_desde:
            query = query.filter(Despacho.fecha_despacho >= d_desde)

    if fecha_hasta:
        d_hasta = parse_date(fecha_hasta)
        if d_hasta:
            query = query.filter(Despacho.fecha_despacho <= d_hasta)

    return query.order_by(desc(Despacho.fecha_despacho), desc(Despacho.id), DespachoItem.numero_item, DespachoItem.numero_subitem)

@router.get("", response_class=HTMLResponse)
async def mercancias_view(
    request: Request,
    db: Session = Depends(get_db),
    q: Optional[str] = Query(None),
    marca: Optional[str] = Query(None),
    ncm: Optional[str] = Query(None),
    propietario: Optional[str] = Query(None),
    fecha_desde: Optional[str] = Query(None),
    fecha_hasta: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200)
):
    import math

    # Consulta base filtrada
    items_query = get_filtered_items_query(db, q, marca, ncm, propietario, fecha_desde=fecha_desde, fecha_hasta=fecha_hasta)

    # Conteo total y páginas
    total_items = items_query.count()
    total_pages = max(1, math.ceil(total_items / per_page))
    current_page = min(page, total_pages)

    # Obtener solo la página solicitada (LIMIT / OFFSET)
    results = items_query.offset((current_page - 1) * per_page).limit(per_page).all()

    # Formatear lista de items con objeto despacho padre
    items_list = []
    for item, desp in results:
        item.despacho = desp
        items_list.append(item)

    # Agregados calculados eficientemente en base de datos
    aggregates = (
        db.query(
            func.coalesce(func.sum(DespachoItem.valor_total), 0.0),
            func.coalesce(func.sum(DespachoItem.cantidad), 0.0)
        )
        .join(Despacho, DespachoItem.despacho_id == Despacho.id)
    )
    if q:
        search_pattern = f"%{q}%"
        aggregates = aggregates.filter(
            or_(
                DespachoItem.descripcion.ilike(search_pattern),
                DespachoItem.marca.ilike(search_pattern),
                DespachoItem.codigo_producto.ilike(search_pattern),
                DespachoItem.codigo_ncm.ilike(search_pattern),
                Despacho.numero_despacho.ilike(search_pattern),
                Despacho.importador_nombre.ilike(search_pattern),
                Despacho.propietario.ilike(search_pattern)
            )
        )
    if marca:
        aggregates = aggregates.filter(DespachoItem.marca.ilike(marca.strip()))
    if ncm:
        aggregates = aggregates.filter(DespachoItem.codigo_ncm == ncm)
    if propietario:
        aggregates = aggregates.filter(Despacho.propietario == propietario)
    if fecha_desde:
        d_desde = parse_date(fecha_desde)
        if d_desde:
            aggregates = aggregates.filter(Despacho.fecha_despacho >= d_desde)
    if fecha_hasta:
        d_hasta = parse_date(fecha_hasta)
        if d_hasta:
            aggregates = aggregates.filter(Despacho.fecha_despacho <= d_hasta)

    agg_result = aggregates.first()
    total_fob_filtrado = agg_result[0] if agg_result else 0.0
    total_cantidad_filtrada = agg_result[1] if agg_result else 0.0

    # Estadísticas para mini KPIs de Marcas (Top 15)
    marca_counts = (
        db.query(DespachoItem.marca, func.count(DespachoItem.id).label("total_items"), func.sum(DespachoItem.valor_total).label("total_fob"))
        .filter(DespachoItem.marca.isnot(None), DespachoItem.marca != "")
        .group_by(DespachoItem.marca)
        .order_by(desc("total_items"))
        .limit(15)
        .all()
    )

    # Listas para filtros dropdown
    todas_marcas = [r[0] for r in db.query(DespachoItem.marca).distinct().filter(DespachoItem.marca.isnot(None), DespachoItem.marca != "").order_by(DespachoItem.marca).all()]
    todos_ncms = [r[0] for r in db.query(DespachoItem.codigo_ncm).distinct().filter(DespachoItem.codigo_ncm.isnot(None), DespachoItem.codigo_ncm != "").order_by(DespachoItem.codigo_ncm).all()]
    todos_propietarios = [r[0] for r in db.query(Despacho.propietario).distinct().filter(Despacho.propietario.isnot(None), Despacho.propietario != "").order_by(Despacho.propietario).all()]

    return templates.TemplateResponse(
        request=request,
        name="mercancias.html",
        context={
            "items": items_list,
            "total_items": total_items,
            "total_fob_filtrado": total_fob_filtrado,
            "total_cantidad_filtrada": total_cantidad_filtrada,
            "marca_kpis": marca_counts,
            "todas_marcas": todas_marcas,
            "todos_ncms": todos_ncms,
            "todos_propietarios": todos_propietarios,
            "q": q or "",
            "selected_marca": marca or "",
            "selected_ncm": ncm or "",
            "selected_propietario": propietario or "",
            "selected_fecha_desde": fecha_desde or "",
            "selected_fecha_hasta": fecha_hasta or "",
            "page": current_page,
            "per_page": per_page,
            "total_pages": total_pages,
            "has_prev": current_page > 1,
            "has_next": current_page < total_pages,
            "prev_page": current_page - 1,
            "next_page": current_page + 1
        }
    )

@router.get("/exportar/excel")
async def export_mercancias_excel(
    db: Session = Depends(get_db),
    q: Optional[str] = Query(None),
    marca: Optional[str] = Query(None),
    ncm: Optional[str] = Query(None),
    propietario: Optional[str] = Query(None),
    fecha_desde: Optional[str] = Query(None),
    fecha_hasta: Optional[str] = Query(None)
):
    results = get_filtered_items_query(db, q, marca, ncm, propietario, fecha_desde=fecha_desde, fecha_hasta=fecha_hasta).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Mercancías Consolidadas"
    ws.views.sheetView[0].showGridLines = True

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center")
    right_align = Alignment(horizontal="right", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center")
    thin_border = Border(
        left=Side(style="thin", color="E5E7EB"),
        right=Side(style="thin", color="E5E7EB"),
        top=Side(style="thin", color="E5E7EB"),
        bottom=Side(style="thin", color="E5E7EB")
    )

    headers = [
        "ID Despacho", "Nº Despacho", "Fecha Despacho", "Dueño / Cliente", "Importador",
        "Ítem", "Subítem", "NCM / Posición", "MARCA", "CÓDIGO / EAN",
        "Descripción del Producto", "Cantidad", "Unidad", "FOB Unitario ($)", "FOB Total ($)",
        "IVA (%)", "Arancel (%)", "País Origen", "Pág. Origen"
    ]
    ws.append(headers)

    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center_align
    ws.row_dimensions[1].height = 26

    for item, desp in results:
        fecha_str = desp.fecha_despacho.strftime("%d/%m/%Y") if desp.fecha_despacho else ""
        row = [
            desp.id,
            desp.numero_despacho or "",
            fecha_str,
            desp.propietario or "",
            desp.importador_nombre or "",
            item.numero_item,
            item.numero_subitem or "",
            item.codigo_ncm or "",
            item.marca or "Sin Marca",
            item.codigo_producto or "",
            item.descripcion or "",
            item.cantidad or 0.0,
            item.unidad or "UNIDAD",
            item.valor_unitario or 0.0,
            item.valor_total or 0.0,
            f"{item.tasa_iva * 100:.1f}%" if item.tasa_iva is not None else "",
            f"{item.tasa_arancel * 100:.1f}%" if item.tasa_arancel is not None else "",
            item.pais_origen or "",
            item.pagina_origen or 1
        ]
        ws.append(row)

    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.border = thin_border
            if isinstance(cell.value, float):
                cell.number_format = "#,##0.00"
                cell.alignment = right_align
            elif isinstance(cell.value, int):
                cell.alignment = center_align

    # Ajustar anchos
    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"mercancias_consolidadas_{len(results)}_items.xlsx"
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.get("/exportar/csv")
@router.get("/exportar/google-sheet")
async def export_mercancias_csv(
    db: Session = Depends(get_db),
    q: Optional[str] = Query(None),
    marca: Optional[str] = Query(None),
    ncm: Optional[str] = Query(None),
    propietario: Optional[str] = Query(None),
    fecha_desde: Optional[str] = Query(None),
    fecha_hasta: Optional[str] = Query(None)
):
    results = get_filtered_items_query(db, q, marca, ncm, propietario, fecha_desde=fecha_desde, fecha_hasta=fecha_hasta).all()

    output = io.StringIO()
    output.write("\ufeff")  # BOM UTF-8 para Excel y Google Sheets
    writer = csv.writer(output, delimiter=",", quoting=csv.QUOTE_MINIMAL)

    writer.writerow([
        "ID_DESPACHO", "NUMERO_DESPACHO", "FECHA_DESPACHO", "DUENO_CLIENTE", "IMPORTADOR",
        "ITEM", "SUBITEM", "NCM_POSICION", "MARCA", "CODIGO_EAN",
        "DESCRIPCION", "CANTIDAD", "UNIDAD", "FOB_UNITARIO", "FOB_TOTAL",
        "IVA_PCT", "ARANCEL_PCT", "PAIS_ORIGEN", "PAGINA"
    ])

    for item, desp in results:
        fecha_str = desp.fecha_despacho.strftime("%d/%m/%Y") if desp.fecha_despacho else ""
        writer.writerow([
            desp.id,
            desp.numero_despacho or "",
            fecha_str,
            desp.propietario or "",
            desp.importador_nombre or "",
            item.numero_item,
            item.numero_subitem or "",
            item.codigo_ncm or "",
            item.marca or "Sin Marca",
            item.codigo_producto or "",
            item.descripcion or "",
            item.cantidad or 0.0,
            item.unidad or "UNIDAD",
            item.valor_unitario or 0.0,
            item.valor_total or 0.0,
            f"{item.tasa_iva * 100:.1f}%" if item.tasa_iva is not None else "",
            f"{item.tasa_arancel * 100:.1f}%" if item.tasa_arancel is not None else "",
            item.pais_origen or "",
            item.pagina_origen or 1
        ])

    return Response(
        content=output.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=mercancias_filtradas_{len(results)}_items.csv"}
    )
