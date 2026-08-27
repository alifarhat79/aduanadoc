# Sistema de Extracción y Gestión de Despachos Aduaneros

Sistema empresarial en Python (FastAPI + SQLAlchemy + SQLite + PyMuPDF/OCR + Bootstrap 5) diseñado para la lectura automatizada de documentos PDF de despachos aduaneros, extracción estructurada de cabeceras y listas de mercancías, control de duplicados, revisión humana dual interactiva y exportación a Excel/CSV.

---

## Características Principales

1. **Detección y Lectura Inteligente de PDFs**:
   - Lectura digital directa y ultra rápida con **PyMuPDF**.
   - Detección automática de páginas escaneadas o digitalizadas como imagen.
   - Procesamiento de imagen con **OpenCV** (escala de grises, umbralización Otsu, denoising) y OCR con **Tesseract** solo cuando es estrictamente necesario.

2. **Extracción Determinística sin Invención de Datos**:
   - Catálogo ampliable de alias y expresiones regulares multilingües (Español, Inglés, Portugués).
   - Mapeo semántico para declaraciones aduaneras (Sistema SOFIA Paraguay, DUA Mercosur y formatos genéricos).
   - Extracción tabular jerárquica de ítems y subítems de mercancías (NCM, descripción, marca, cantidad, FOB unitario y total).
   - Cumplimiento estricto de la regla: si un dato no está en el documento, se registra como `null` y nunca se inventa.

3. **Trazabilidad y Nivel de Confianza por Campo**:
   - Cada campo almacena: `valor`, `valor_original`, `confidence` (0.0 a 1.0), `pagina`, `texto_origen` y `metodo`.
   - Clasificación visual en la interfaz:
     - Verde (>= 0.90): Alta confianza.
     - Amarillo (0.70 - 0.89): Destacado para revisión.
     - Rojo (< 0.70): Exige revisión obligatoria.

4. **Pantalla de Revisión Humana Dual**:
   - Lado izquierdo: Visor interactivo del PDF original.
   - Lado derecho: Formulario de edición con semáforo de confianza.
   - Registro de auditoría por cada modificación manual (`campo_modificado`, `valor_anterior`, `valor_nuevo`, `usuario`, `fecha`).

5. **Importación Masiva (1 a 100+ PDFs)**:
   - Zona Drag & Drop interactiva con barra de progreso en vivo.
   - Procesamiento aislado por archivo: un error en un documento no interrumpe el lote.
   - Detección y control de duplicados por hash **SHA-256** y clave de negocio (`Nº despacho + Fecha + RUC`).

6. **Dashboard y Exportación Multi-Hoja**:
   - Panel de control con KPIs (Total despachos, FOB/CIF total, liquidación de tributos, peso total y pendientes de revisión).
   - Exportación a **Excel** (`.xlsx`) con 3 pestañas estructuradas: *Despachos*, *Ítems y Mercancías*, y *Resumen Ejecutivo*.
   - Exportación a **CSV** compatible.

---

## Estructura del Proyecto

```
despacho/
├── app/
│   ├── main.py                     # Entrypoint FastAPI
│   ├── config.py                   # Configuración y variables de entorno
│   ├── database.py                 # Conexión SQLAlchemy y sesiones
│   ├── models.py                   # Modelos ORM (Despacho, Items, Auditoría, Logs)
│   ├── schemas.py                  # Validaciones Pydantic
│   ├── routers/
│   │   ├── dashboard.py            # Panel de control y métricas
│   │   ├── despachos.py            # Listado, filtros, detalle y PDF
│   │   ├── upload.py               # Ingesta masiva Drag & Drop
│   │   ├── revisar.py              # Pantalla de revisión dual
│   │   └── exportacion.py          # Generador Excel (3 hojas) y CSV
│   ├── services/
│   │   ├── pdf_reader.py           # Extractor de texto digital PyMuPDF
│   │   ├── ocr_service.py          # Preprocesamiento CV2 y OCR
│   │   ├── document_classifier.py  # Clasificador de plantilla
│   │   ├── field_extractor.py      # Extracción por alias y reglas
│   │   ├── table_extractor.py      # Extractor de ítems y subítems
│   │   ├── normalizer.py           # Normalizador de fechas, monedas y RUC
│   │   ├── duplicate_detector.py   # Control de duplicados SHA-256
│   │   ├── validators.py           # Validaciones de coherencia financiera
│   │   ├── ai_extractor.py         # Interfaz para LLM estructurada
│   │   └── pipeline.py             # Orquestador del flujo
│   ├── templates/                  # Plantillas Jinja2 (HTML5 / Bootstrap 5)
│   └── static/                     # CSS corporativo y JavaScript
├── data/                           # Base de datos SQLite
├── uploads/                        # Almacenamiento seguro de PDFs (Año/Mes)
├── tests/                          # Suite de pruebas unitarias y de integración
├── requirements.txt
├── .env.example
└── README.md
```

---

## Instalación y Puesta en Marcha

### 1. Requisitos Previos
- Python 3.12 o superior.

### 2. Instalar Dependencias
```bash
pip install -r requirements.txt
```

### 3. Iniciar el Servidor
```bash
python -m uvicorn app.main:app --reload --port 8000
```

Acceder desde el navegador a:
`http://127.0.0.1:8000`

---

## Ejecutar Pruebas Automatizadas

```bash
python -m pytest -v
```
