"""
Servicio Helper para NCM (Nomenclatura Común del MERCOSUR / Sistema Armonizado)
Proporciona traducción de códigos NCM a descripciones de rubros y capítulos arancelarios en español.
"""
import re
from typing import Dict, Optional

# Diccionario de Capítulos del Sistema Armonizado (2 dígitos)
CAPITULOS_SISTEMA_ARMONIZADO: Dict[str, str] = {
    "01": "Animales vivos",
    "02": "Carne y despojos comestibles",
    "03": "Pescados, crustáceos y moluscos",
    "04": "Leche, lácteos, huevos y miel",
    "05": "Otros productos de origen animal",
    "06": "Plantas vivas y floricultura",
    "07": "Hortalizas, plantas y tubérculos alimenticios",
    "08": "Frutas y frutos comestibles",
    "09": "Café, té, yerba mate y especias",
    "10": "Cereales",
    "11": "Productos de la molinería, malta y almidón",
    "12": "Semillas y frutos oleaginosos",
    "13": "Gomas, resinas y extractos vegetales",
    "14": "Materias trenzables y productos vegetales",
    "15": "Grasas y aceites animales o vegetales",
    "16": "Preparaciones de carne, pescado o mariscos",
    "17": "Azúcares y artículos de confitería",
    "18": "Cacao y sus preparaciones",
    "19": "Preparaciones a base de cereales, harina o leche",
    "20": "Preparaciones de hortalizas, frutas o frutos",
    "21": "Preparaciones alimenticias diversas",
    "22": "Bebidas, líquidos alcohólicos y vinagre",
    "23": "Residuos y desperdicios de la industria alimentaria",
    "24": "Tabaco y sucedáneos (incluye vapeadores)",
    "25": "Sal, azufre, tierras y piedras, yeso, cal y cemento",
    "26": "Minerales metalíferos, escorias y cenizas",
    "27": "Combustibles minerales, aceites minerales y ceras",
    "28": "Productos químicos inorgánicos",
    "29": "Productos químicos orgánicos",
    "30": "Productos farmacéuticos y medicamentos",
    "31": "Abonos y fertilizantes",
    "32": "Extractos curtientes o tintóreos, pinturas y tintas",
    "33": "Aceites esenciales, perfumería y cosmética",
    "34": "Jabones, agentes de superficie orgánicos y ceras",
    "35": "Materias albuminoideas, colas y enzimas",
    "36": "Pólvoras y explosivos, artículos de pirotecnia",
    "37": "Productos fotográficos o cinematográficos",
    "38": "Productos diversos de las industrias químicas",
    "39": "Plásticos y sus manufacturas",
    "40": "Caucho y sus manufacturas",
    "41": "Pieles (excepto la peletería) y cueros",
    "42": "Manufacturas de cuero, marroquinería y bolsos",
    "43": "Peletería y confecciones de peletería",
    "44": "Madera, carbón vegetal y manufacturas de madera",
    "45": "Corcho y sus manufacturas",
    "46": "Manufacturas de espartería o cestería",
    "47": "Pasta de madera o de otras materias celulósicas",
    "48": "Papel y cartón; manufacturas de pasta de celulosa",
    "49": "Productos editoriales, prensa, libros e impresos",
    "50": "Seda",
    "51": "Lana y pelo fino u ordinario",
    "52": "Algodón",
    "53": "Otras fibras textiles vegetales",
    "54": "Filamentos sintéticos o artificiales",
    "55": "Fibras sintéticas o artificiales discontinuas",
    "56": "Guata, fieltro y telas sin tejer",
    "57": "Alfombras y demás revestimientos para el suelo",
    "58": "Tejidos especiales, encajes y bordados",
    "59": "Telas impregnadas, recubiertas o estratificadas",
    "60": "Tejidos de punto",
    "61": "Prendas y complementos de vestir, de punto",
    "62": "Prendas y complementos de vestir, excepto los de punto",
    "63": "Los demás artículos textiles confeccionados y ropa usada",
    "64": "Calzado, polainas y artículos análogos",
    "65": "Sombreros y demás tocados",
    "66": "Paraguas, sombrillas, bastones y látigos",
    "67": "Plumas y plumón preparados, flores artificiales",
    "68": "Manufacturas de piedra, yeso fraguable, cemento o amianto",
    "69": "Productos cerámicos",
    "70": "Vidrio y sus manufacturas",
    "71": "Perlas finas, piedras preciosas, metales preciosos y joyería",
    "72": "Fundición, hierro y acero",
    "73": "Manufacturas de fundición, hierro o acero",
    "74": "Cobre y sus manufacturas",
    "75": "Níquel y sus manufacturas",
    "76": "Aluminio y sus manufacturas",
    "78": "Plomo y sus manufacturas",
    "79": "Cinc y sus manufacturas",
    "80": "Estaño y sus manufacturas",
    "81": "Los demás metales comunes y sus manufacturas",
    "82": "Herramientas, cuchillería y cubiertos de metal",
    "83": "Manufacturas diversas de metal común",
    "84": "Reactores nucleares, calderas, máquinas y computación",
    "85": "Máquinas, aparatos y material eléctrico, audio, TV y electrónica",
    "86": "Vehículos y material para vías férreas",
    "87": "Vehículos automóviles, tractores, velocípedos y partes",
    "88": "Aeronaves, vehículos espaciales y sus partes",
    "89": "Barcos y demás estructuras flotantes",
    "90": "Instrumentos y aparatos de óptica, fotografía, médicos y precisión",
    "91": "Relojería y sus partes",
    "92": "Instrumentos musicales y sus partes",
    "93": "Armas, municiones y sus partes",
    "94": "Muebles, iluminación, colchones y construcciones prefabricadas",
    "95": "Juguetes, juegos y artículos para recreo o deporte",
    "96": "Manufacturas diversas (bolígrafos, encendedores, etc.)",
    "97": "Objetos de arte, de colección o de antigüedad"
}

# Partidas arancelarias comunes (4 a 6 dígitos)
PARTIDAS_FRECUENTES: Dict[str, str] = {
    "2404.12": "Dispositivos para vapear / e-líquidos",
    "2404": "Sucedáneos de tabaco y vapeadores",
    "3303.00": "Perfumes y aguas de tocador",
    "3303": "Perfumes y aguas de tocador",
    "3304.99": "Cremas, maquillaje y cuidado de piel",
    "3304.91": "Polvos de maquillaje y talcos",
    "3304.10": "Pintalabios y labiales",
    "3304.20": "Maquillaje de ojos (sombras, rímel)",
    "3304.30": "Manicura y pedicura",
    "3304": "Preparaciones de belleza y maquillaje",
    "3305.10": "Champús / Shampoos",
    "3305.20": "Preparaciones para ondulación/desrizado",
    "3305.30": "Lacas para el cabello",
    "3305.90": "Lociones y tratamientos capilares",
    "3305": "Preparaciones capilares",
    "3307.10": "Afeitado y after-shave",
    "3307.20": "Desodorantes corporales y antitranspirantes",
    "3307": "Artículos de tocador y desodorantes",
    "3401": "Jabones y productos de tocador",
    "3926": "Manufacturas de plástico",
    "4202": "Bolsos, mochilas, carteras y valijas",
    "6109": "T-shirts y camisetas de punto",
    "6203": "Trajes, pantalones y ropa masculina",
    "6204": "Vestidos, faldas y ropa femenina",
    "6402": "Calzado deportivo / plástico / caucho",
    "6403": "Calzado de cuero natural",
    "8471.30": "Laptops y computadoras portátiles",
    "8471": "Computadoras y máquinas de datos",
    "8504": "Cargadores y transformadores",
    "8517.13": "Smartphones / Teléfonos móviles",
    "8517.62": "Routers, módems y redes",
    "8517": "Teléfonos, smartphones y equipos de red",
    "8518.10": "Micrófonos y accesorios de audio",
    "8518.21": "Altavoces / Parlantes individuales",
    "8518.22": "Altavoces / Parlantes múltiples",
    "8518.29": "Parlantes y cajas acústicas",
    "8518.30": "Auriculares y audífonos",
    "8518": "Micrófonos, parlantes y auriculares",
    "8527.21": "Autorradios y reproductores de auto",
    "8527.99": "Radios y equipos receptores de sonido",
    "8527": "Equipos receptores de radio y sonido",
    "8528.52": "Monitores de computadora",
    "8528.72": "Televisores y Smart TVs",
    "8528": "Monitores, proyectores y televisores",
    "8543.70": "Disp. vaporizadores / Electrónica específica",
    "8543": "Aparatos eléctricos con función propia",
    "9004.10": "Gafas / Anteojos de sol",
    "9004": "Gafas y anteojos",
    "9102": "Relojes de pulsera y de bolsillo",
    "9503": "Juguetes y modelos reducidos",
    "9504": "Consolas y videojuegos",
    "9617": "Termos y recipientes isotérmicos"
}


def clean_ncm_code(ncm: Optional[str]) -> str:
    """Retorna una versión limpia del código NCM eliminando caracteres no numéricos o espacios."""
    if not ncm:
        return ""
    return str(ncm).strip()


def get_ncm_info(ncm: Optional[str]) -> Dict[str, str]:
    """
    Analiza un código NCM y retorna su descripción amigable, rubro y capítulo del Sistema Armonizado.
    Ejemplo:
        get_ncm_info("3303.00.10.000L") -> {
            "codigo": "3303.00.10.000L",
            "codigo_corto": "3303.00.10",
            "rubro": "Perfumes y aguas de tocador",
            "capitulo_num": "33",
            "capitulo_nombre": "Aceites esenciales, perfumería y cosmética",
            "resumen_display": "3303.00.10 (Perfumes y aguas de tocador)"
        }
    """
    if not ncm:
        return {
            "codigo": "",
            "codigo_corto": "",
            "rubro": "Sin Clasificar",
            "capitulo_num": "",
            "capitulo_nombre": "Sin Clasificar",
            "resumen_display": "Sin NCM asignado"
        }

    raw = clean_ncm_code(ncm)
    digits_only = re.sub(r"[^0-9]", "", raw)
    
    capitulo_num = digits_only[:2] if len(digits_only) >= 2 else ""
    partida_4 = digits_only[:4] if len(digits_only) >= 4 else ""
    partida_6 = f"{digits_only[:4]}.{digits_only[4:6]}" if len(digits_only) >= 6 else ""

    # 1. Buscar en partidas de 6 dígitos
    rubro = PARTIDAS_FRECUENTES.get(partida_6)
    
    # 2. Buscar en partidas de 4 dígitos
    if not rubro:
        rubro = PARTIDAS_FRECUENTES.get(partida_4)

    # 3. Buscar en el capítulo de 2 dígitos
    capitulo_nombre = CAPITULOS_SISTEMA_ARMONIZADO.get(capitulo_num, "Mercancías diversas")
    if not rubro:
        rubro = capitulo_nombre

    # Formatear código corto visible
    codigo_corto = raw
    if "." in raw:
        parts = raw.split(".")
        if len(parts) >= 3:
            codigo_corto = f"{parts[0]}.{parts[1]}.{parts[2][:2]}"
        else:
            codigo_corto = raw[:10]
    elif len(digits_only) >= 8:
        codigo_corto = f"{digits_only[:4]}.{digits_only[4:6]}.{digits_only[6:8]}"

    return {
        "codigo": raw,
        "codigo_corto": codigo_corto,
        "rubro": rubro,
        "capitulo_num": capitulo_num,
        "capitulo_nombre": capitulo_nombre,
        "resumen_display": f"{codigo_corto} ({rubro})"
    }
