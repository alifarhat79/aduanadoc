"""
Servicio de Limpieza y Gestión de Etiquetas y Familias Jerárquicas de Subítems por Marca.
Ultra-optimizado con agregación en memoria y caché para renderizado instantáneo (<1ms).
Soporta estructura jerárquica de 3 niveles: Marca > Familia > Variantes (Sub-subítems).
"""
import re
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models import DespachoItem, MarcaSubitemEtiqueta

STOPWORDS = {
    'AGUAS', 'AGUA', 'DE', 'DEL', 'LA', 'LAS', 'EL', 'LOS', 'Y', 'O', 'CON', 'SIN', 'EN', 'POR', 'PARA',
    'PERFUMES', 'PERFUME', 'SPRAY', 'SPARY', 'SPR', 'EAU', 'PARFUM', 'TOILETTE', 'EDP', 'EDT', 'COLOGNE', 'COLONIA',
    'PZAS', 'PZA', 'UNID', 'UNIDADES', 'UNIDAD', 'SET', 'KIT', 'PACK', 'COMPACT', 'PIEZAS', 'PIEZA',
    'MCA', 'MARCA', 'MOD', 'MODELO', 'REF', 'COD', 'ART', 'TIPO', 'ORIGINAL', 'GENUINE',
    'EXTRACT', 'EXTRAIT', 'SERUM', 'CREMA', 'CREMAS', 'LOCION', 'LOCIONES', 'TRATAMIENTO',
    'DISPOSITIVO', 'DISPOSITIVOS', 'VAPEADOR', 'VAPEADORES', 'DESECHABLE', 'RECARGABLE', 'POD', 'PODS',
    'PARES', 'PAR', 'AUTOMOVIL', 'AUTO', 'PORTABLE', 'AMPLIFICADO', 'BLUETOOTH', 'INALAMBRICO',
    'SUBWOOFER', 'AUTORADIOS', 'AUTORADIO', 'MICROFONOS', 'MICROFONO', 'PARLANTES', 'PARLANTE',
    'CABLES', 'CABLE', 'AURICULARES', 'AUDIFONOS', 'HEADSET', 'HEADPHONES', 'SPEAKER', 'SPEAKERS',
    'BATERIA', 'BATERIAS', 'CARGADOR', 'CARGADORES', 'ADAPTADOR', 'ADAPTADORES', 'CONTROLADOR', 'DJ',
    'CORPORAL', 'CORP', 'UNISEX', 'SAMPLE', 'MINI', 'FEM', 'MASC', 'HOMME', 'FEMME'
}

COMPOUND_PREFIXES = {
    'BADEE AL OUD', 'SKY SOLO', 'ZERO PORE', 'TRIPLE COLLAGEN', 'DEEP VITA',
    'TXA NIACINAMIDE', 'THE KINGDOM', 'OUD MOOD', 'QAED AL FURSAN', 'QAAED AL FURSAN',
    'AL NOBLE', 'ISHQ AL SHUYUKH', 'CONFIDENTIAL PRIVATE', 'STAGE 1', 'STAGE 2',
    'STAGE 3', 'GTX GO', 'CLUB 6520', 'SM 58', 'SM 57', 'SM 7B', 'BETA 58',
    'BETA 52', 'BETA 87', 'BETA 91', 'BETA 98', 'BLX 14', 'BLX 24', 'BLX 288',
    'GLX D', 'SLX D', 'DMH A', 'DMH G', 'DMH Z', 'AVH A', 'AVH G', 'AVH Z',
    'MVH S', 'DEH S', 'TS A', 'TS G'
}

# Caché en memoria para respuesta inmediata (<1ms)
_HIERARCHICAL_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def invalidate_brand_cache(marca: Optional[str] = None):
    """Invalida la caché de una marca específica o de todas si no se especifica."""
    global _HIERARCHICAL_CACHE
    if marca:
        _HIERARCHICAL_CACHE.pop(marca.strip().upper(), None)
    else:
        _HIERARCHICAL_CACHE.clear()


def clean_subitem_name(raw_desc: Optional[str], marca: Optional[str] = None) -> str:
    """Extrae un nombre de modelo/variante corto, limpio y legible a partir del texto aduanero crudo."""
    if not raw_desc:
        return "Línea General"

    text = str(raw_desc).upper()

    # 1. Eliminar la marca si está en el texto
    if marca:
        text = re.sub(r'\b' + re.escape(marca.upper()) + r'\b', ' ', text)

    # 2. Eliminar unidades y medidas (ej: 100ML, 50G, 200W, etc.)
    text = re.sub(r'\b\d+[\.,]?\d*\s*(ML|G|KG|L|OZ|W|V|MM|CM|M|PZAS?|UNID?|PCS?)\b', ' ', text)

    # 3. Eliminar caracteres especiales
    text = re.sub(r'[\(\)\[\]/,\-\.:;"\'#]', ' ', text)

    # 4. Filtrar tokens no significativos
    tokens = []
    for t in text.split():
        t_clean = t.strip()
        if len(t_clean) > 1 and t_clean not in STOPWORDS and not t_clean.isdigit():
            tokens.append(t_clean)

    if not tokens:
        raw_fallback = re.sub(r'[\(\)\[\]/,\-\.:;]', ' ', raw_desc).strip()
        return raw_fallback[:22].title()

    clean = " ".join(tokens[:3]).title()
    return clean


NOISE_VARIANT_TOKENS = {
    'SP', 'SPR', 'SPRAY', 'SPARY', 'UAE', 'DOZ', 'DZN', 'FREIGHT', 'ROLL',
    '100ML', '50ML', '200ML', '30ML', 'LTF', 'BY', 'UNI', 'UNISEX', 'SET', 'KIT',
    'FEM', 'MASCU', 'MEN', 'WOMEN', 'WOMAN', 'WOMWN', 'EXTRAIR', 'SAMPLE'
}


def extract_family_and_variant(clean_name: str) -> Tuple[str, str, str]:
    """
    Separa un nombre limpio en (Familia, NombreVariante, PatronVariante).
    Ej: "Asad Bourbon" -> ("Asad", "Bourbon", "ASAD BOURBON")
        "Asad"         -> ("Asad", "Original / Clásico", "ASAD")
        "Badee Al Oud Honor Glory" -> ("Badee Al Oud", "Honor Glory", "BADEE AL OUD HONOR")
    """
    clean_upper = clean_name.upper()

    # 1. Prefijo compuesto (ej: Badee Al Oud, Sky Solo)
    for cp in COMPOUND_PREFIXES:
        if clean_upper == cp or clean_upper.startswith(cp + ' '):
            fam = cp.title()
            rest = clean_upper[len(cp):].strip()
            if not rest:
                return fam, "Original / Clásico", cp
            rest_tokens = [t for t in rest.split() if t not in NOISE_VARIANT_TOKENS]
            if not rest_tokens:
                return fam, "Original / Clásico", cp
            variant_name = " ".join(rest_tokens).title()
            pattern = f"{cp} {rest_tokens[0]}"
            return fam, variant_name, pattern

    # 2. Prefijo simple (primera palabra es la Familia, el resto la Variante)
    words = clean_name.split()
    if len(words) == 1:
        return words[0].title(), "Original / Clásico", words[0].upper()
    else:
        fam = words[0].title()
        rest_tokens = [t for t in words[1:] if t.upper() not in NOISE_VARIANT_TOKENS]
        if not rest_tokens:
            return fam, "Original / Clásico", fam.upper()
        variant_name = " ".join(rest_tokens).title()
        pattern = f"{words[0].upper()} {rest_tokens[0].upper()}"
        return fam, variant_name, pattern


def get_brand_hierarchical_subitems(db: Session, marca: str, use_cache: bool = True) -> List[Dict[str, Any]]:
    """
    Retorna la estructura jerárquica de 3 niveles:
    Marca -> Familias (A-Z) -> Variantes / Sub-subítems (A-Z).
    Prioriza las etiquetas personalizadas de la BD y auto-descubre las demás.
    """
    if not marca or not marca.strip():
        return []

    clean_marca = marca.strip()
    cache_key = clean_marca.upper()

    if use_cache and cache_key in _HIERARCHICAL_CACHE:
        return _HIERARCHICAL_CACHE[cache_key]

    # 1. Cargar etiquetas personalizadas guardadas por el usuario
    custom_labels = (
        db.query(MarcaSubitemEtiqueta)
        .filter(MarcaSubitemEtiqueta.marca.ilike(clean_marca))
        .order_by(MarcaSubitemEtiqueta.orden.asc(), MarcaSubitemEtiqueta.id.asc())
        .all()
    )

    # 2. Obtener todas las descripciones distintas de la marca con 1 sola consulta agrupada
    rows = (
        db.query(DespachoItem.descripcion, func.count(DespachoItem.id))
        .filter(DespachoItem.marca == clean_marca)
        .filter(DespachoItem.descripcion.isnot(None))
        .filter(DespachoItem.descripcion != "")
        .group_by(DespachoItem.descripcion)
        .all()
    )

    families_dict: Dict[str, Dict[str, Any]] = {}

    # Registrar primero etiquetas personalizadas
    for cl in custom_labels:
        fam_name = (cl.familia or "").strip().title()
        if not fam_name:
            fam_name, _, _ = extract_family_and_variant(cl.nombre_limpio.strip())
        
        if fam_name not in families_dict:
            families_dict[fam_name] = {
                "familia": fam_name,
                "patron_familia": fam_name.upper(),
                "total_items": 0,
                "variantes": {}
            }
        
        var_name = cl.nombre_limpio.strip()
        families_dict[fam_name]["variantes"][var_name] = {
            "id": cl.id,
            "familia": fam_name,
            "nombre": var_name,
            "patron": cl.patron_busqueda.strip(),
            "total_items": 0,
            "is_custom": True
        }

    # Sumar y auto-descubrir variantes desde los datos de los despachos
    for raw_desc, cnt in rows:
        clean = clean_subitem_name(raw_desc, clean_marca)
        if not clean:
            continue

        fam, var_name, pattern = extract_family_and_variant(clean)

        if fam not in families_dict:
            families_dict[fam] = {
                "familia": fam,
                "patron_familia": fam.upper(),
                "total_items": 0,
                "variantes": {}
            }

        families_dict[fam]["total_items"] += cnt

        # Si la variante ya existe, sumar conteo
        if var_name in families_dict[fam]["variantes"]:
            families_dict[fam]["variantes"][var_name]["total_items"] += cnt
        else:
            families_dict[fam]["variantes"][var_name] = {
                "id": None,
                "familia": fam,
                "nombre": var_name,
                "patron": pattern,
                "total_items": cnt,
                "is_custom": False
            }

    # 3. Formatear y ordenar alfabéticamente (A-Z)
    final_families: List[Dict[str, Any]] = []
    for fam_name, fam_data in sorted(families_dict.items(), key=lambda x: x[0].lower()):
        # Ordenar variantes de cada familia: 'Original / Clásico' primero si existe, luego A-Z
        sorted_vars = sorted(
            fam_data["variantes"].values(),
            key=lambda v: (0 if "Original" in v["nombre"] else 1, v["nombre"].lower())
        )
        # Recalcular el total_items real de la familia sumando las variantes
        sum_total = sum(v["total_items"] for v in sorted_vars)
        fam_data["total_items"] = max(fam_data["total_items"], sum_total)
        fam_data["variantes"] = sorted_vars
        final_families.append(fam_data)

    _HIERARCHICAL_CACHE[cache_key] = final_families
    return final_families


def get_brand_subitems(db: Session, marca: str, limit: int = 100, use_cache: bool = True) -> List[Dict[str, Any]]:
    """Función de compatibilidad: Retorna la lista plana de subítems."""
    hierarchical = get_brand_hierarchical_subitems(db, marca, use_cache=use_cache)
    flat = []
    for fam in hierarchical:
        for var in fam["variantes"]:
            display_nombre = f"{fam['familia']} - {var['nombre']}" if var['nombre'] != "Original / Clásico" else fam['familia']
            flat.append({
                "id": var["id"],
                "familia": fam["familia"],
                "nombre": display_nombre,
                "patron": var["patron"],
                "total_items": var["total_items"],
                "is_custom": var["is_custom"]
            })
    return flat[:limit] if limit and limit > 0 else flat
