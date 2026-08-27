from datetime import date
from typing import Dict, Any, Tuple, Optional

def validate_despacho_financials(fob: Optional[float], flete: Optional[float], seguro: Optional[float], cif: Optional[float]) -> Tuple[bool, Optional[str]]:
    """
    Verifica coherencia entre FOB, Flete, Seguro y CIF cuando están presentes.
    Permite una tolerancia del 2% por posibles ajustes o redondeos aduaneros.
    """
    if fob is not None and flete is not None and seguro is not None and cif is not None:
        calculado = fob + flete + seguro
        if calculado > 0:
            diferencia = abs(cif - calculado)
            porcentaje = (diferencia / cif) * 100
            if porcentaje > 5.0:
                return False, f"El valor CIF ({cif:,.2f}) difiere significativamente de la suma FOB+Flete+Seguro ({calculado:,.2f}). Diferencia: {porcentaje:.1f}%"
    return True, None

def validate_despacho_dates(fecha_despacho: Optional[date]) -> Tuple[bool, Optional[str]]:
    """Verifica que la fecha de despacho sea lógica (no en un futuro lejano ni anterior al año 2000)."""
    if fecha_despacho:
        hoy = date.today()
        if fecha_despacho.year < 2000:
            return False, f"Fecha de despacho ({fecha_despacho}) demasiado antigua (anterior al año 2000)."
        if fecha_despacho.year > hoy.year + 2:
            return False, f"Fecha de despacho ({fecha_despacho}) en el futuro no permitida."
    return True, None
