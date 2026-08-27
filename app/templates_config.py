from pathlib import Path
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

def format_smart_value(val, campo=""):
    """Formatea valores según su tipo y nombre de campo (0,000.00 para decimales, 0,000 para enteros, DD/MM/YYYY para fechas)."""
    if val is None or val == "":
        return "-"
    if isinstance(val, float):
        if any(k in campo.lower() for k in ["imponible", "general", "tributos", "impuesto", "iva", "bultos"]):
            return f"{val:,.0f}"
        return f"{val:,.2f}"
    if isinstance(val, int):
        return f"{val:,.0f}"
    if hasattr(val, "strftime"):
        return val.strftime("%d/%m/%Y")
    return str(val)

# Registrar funciones y filtros auxiliares globales en Jinja2
templates.env.globals.update(
    getattr=getattr,
    hasattr=hasattr,
    str=str,
    format_smart_value=format_smart_value
)

templates.env.filters["smart_val"] = format_smart_value
