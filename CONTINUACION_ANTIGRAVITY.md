# 📦 AduanaDoc - Sistema de Gestión de Despachos Aduaneros
> **Documento de Continuación y Traspaso para Antigravity AI**
> *Generado el 26/08/2026 - Actualizado para traspaso y continuación en otra computadora.*

---

## 🎯 1. Resumen Ejecutivo del Proyecto

**AduanaDoc** es una plataforma web completa de alto rendimiento para la importación, extracción inteligente, auditoría, consulta, búsqueda consolidada y sincronización en la nube de **Despachos Aduaneros (Sistema SOFIA / Zona Franca / Mercosur)**.

### 🌟 Capacidades Clave del Sistema:
1. **Extracción Automatizada de PDFs**:
   - Detección de texto vectorial y tablas de alta densidad (`pdfplumber` + `PyMuPDF`).
   - Extracción de cabeceras, RUC/Documento, valores FOB/CIF, flete, seguro, liquidaciones y canal (Verde / Naranja / Rojo).
   - **Extracción de Mercancías e Ítems**: Extracción de todas las filas con NCM, cantidad, unidad, valores unitarios y totales.
   - **Separación Inteligente de Marcas y Códigos EAN**: Detecta y separa códigos de barras de 8 a 14 dígitos (EAN/UPC/SKU) y marcas, limpiando la descripción del producto y eliminando secuencias aduaneras como `COD: ITEM NRO.X`.
2. **Catálogo Consolidado de Mercancías (`/mercancias`)**:
   - Vista unificada de todos los ítems de todos los despachos con paginación de 35 filas por página.
   - Filtros dinámicos por marca (con mini KPI badges clicables), búsqueda por texto, NCM y dueño/cliente.
   - Exportación unificada directa a Excel (.xlsx) y CSV.
   - Navegación con resaltado amarillo suave (`highlight_item`) hacia la ficha técnica del despacho.
3. **Menú Contextual de Clic Derecho en Tablas**:
   - Clic derecho desktop-style sobre cualquier fila de despacho para abrir modal de auditoría limpia, ficha técnica, PDF original, exportación o eliminación.
4. **Sincronización en la Nube con Turso Database (libSQL / SQLite Distribuido)**:
   - Sincronización bidireccional (`Push` / `Pull`) a través de la API HTTPS Pipeline v2 de libSQL (sin necesidad de compiladores C++ / MSVC en Windows).
   - Conexión: `libsql://despachos-alifarhat.aws-us-east-1.turso.io`.
   - Permite que cualquier otra PC con la app descargue y consulte toda la base de datos al instante.
5. **Integración con Google Drive & Auto-Vigilante (Daemon Background Task)**:
   - Conectado a la carpeta: `1NP6zJHL9w_bV0W1BysIDRIZ5FXZzc5Kv` (`DESPACHOS FINIQUITADOS`).
   - Autenticación con Service Account: `service_account.json` (`despachos-bot@despadudiario.iam.gserviceaccount.com`).
   - **Doble Control Anti-Duplicados**: Comprobación rápida por Nombre de Archivo antes de descargar + Hash criptográfico SHA-256.
   - **Auto-Vigilante en Segundo Plano (`GDriveWatcher`)**: Revisa la carpeta cada 60 segundos y procesa cualquier PDF nuevo de forma automática.
6. **Visor PDF Nativo Integrado (Chrome / Edge / Firefox)**:
   - Visualización embebida directa con `Content-Disposition: inline` y cabeceras `Accept-Ranges: bytes`.
   - Integrado en la pantalla de revisión dual (`/revisar/{id}`) y en modal interactivo en el detalle del despacho (`/despachos/{id}`).
   - Controles nativos del navegador: Zoom fluido, miniaturas de páginas, búsqueda de texto dentro del documento, rotación, impresión y descarga.
   - Manejo amigable con mensaje explicativo si un despacho fue descargado de la nube y el PDF físico aún no está en esa PC.
7. **Sistema de Backup Automático & Manual (.ZIP a Google Drive)**:
   - **Backup Automático tras cada despacho**: Cada vez que se procesa o importa un despacho nuevo (manual o por Auto-Vigilante), se genera un `.ZIP` completo con timestamp en `backups/` sincronizado a Google Drive.
   - **Botón de Backup en Navbar y Configuración**: Permite generar copias de seguridad inmediatas y descargar los archivos `.ZIP` con 1 clic.
   - Rotación inteligente de los últimos 20 respaldos locales.
8. **Distribución y Actualizaciones Multi-PC Sin Pendrive**:
   - **Para el Desarrollador**: Publica nuevas versiones (`/api/updates/publish`) generando el paquete `update_latest.zip` y el manifiesto `version.json` en Google Drive / carpeta compartida.
   - **Para Otras Computadoras**: Banner global automático y botón *"Actualizar Sistema en esta PC"*. Descarga la versión nueva, realiza un respaldo preventivo de `despachos.db` y actualiza el código sin tocar la base de datos ni credenciales.
   - **Siguiente paso acordado**: Integración con **Git / GitHub** para que el programador haga `git push` y las otras PCs hagan `git pull` automático al abrir `iniciar_app.bat` o desde la web.
9. **Ejecutable en 1 Clic (`iniciar_app.bat`)**:
   - Auto-instalador de dependencias (`pip install -r requirements.txt`) si se abre en una PC nueva.
   - Codificación UTF-8 universal (`chcp 65001` y `PYTHONUTF8=1`).
   - Abre automáticamente el navegador en `http://127.0.0.1:8000`.

---

## 🏗️ 2. Arquitectura del Código

```
despacho/
├── app/
│   ├── main.py                  # Entrada FastAPI + Lifespan (Inicia SQLite y GDriveWatcher)
│   ├── config.py                # Configuración Pydantic Settings, BACKUP_DIR, UPDATES_DIR
│   ├── database.py              # Engine SQLite local, SessionLocal y Base ORM
│   ├── models.py                # Modelos ORM: Despacho, DespachoItem, DespachoAuditoria, ProcessingLog
│   ├── templates_config.py      # Configuración de Jinja2 Templates
│   ├── routers/
│   │   ├── dashboard.py         # Vista principal / (KPIs, estadísticas, últimos despachos)
│   │   ├── despachos.py         # Listado /despachos, detalle /despachos/{id}, edición, PDF inline y APIs
│   │   ├── mercancias.py        # Catálogo consolidado /mercancias y exportación unificada
│   │   ├── configuracion.py     # Pantalla /configuracion (Turso, GDrive, Backups, Updates)
│   │   ├── upload.py            # Carga manual Drag & Drop /upload + Auto-Sync + Auto-Backup
│   │   ├── revisar.py           # Bandeja de despachos pendientes de revisión humana con visor PDF
│   │   ├── exportacion.py       # Endpoints de exportación (Excel, CSV, PDF, HTML, Sheets)
│   │   ├── backup_updater.py    # Router API de Backup (.ZIP) y Actualizaciones Multi-PC
│   │   └── turso.py             # Router API de Turso Cloud
│   ├── services/
│   │   ├── backup_service.py    # Generador y gestor de respaldos ZIP y Google Drive
│   │   ├── updater_service.py   # Publicación y auto-actualización entre computadoras
│   │   ├── pipeline.py          # Orquestador del flujo de extracción de PDFs
│   │   ├── field_extractor.py   # Extracción de metadatos, cabeceras y tributos
│   │   ├── table_extractor.py   # Extracción de ítems, marcas, EANs y limpieza de COD: ITEM NRO.X
│   │   ├── turso_service.py     # Cliente libSQL HTTP Pipeline v2 (Push / Pull a Turso Cloud)
│   │   ├── gdrive_service.py    # Google Drive API v3 (Escaneo, descarga, deduplicación y auto-backup)
│   │   ├── gdrive_watcher.py    # Demonio en segundo plano de vigilancia de Google Drive
│   │   ├── normalizer.py        # Normalización de textos, monedas y fechas
│   │   └── export_service.py    # Generación de Excel estilizado (openpyxl), CSV, PDF y Sheets
│   ├── static/                  # CSS y JS personalizados (custom.css, pdf-viewer.js)
│   └── templates/               # Plantillas HTML con Bootstrap 5 y Bootstrap Icons
│       ├── base.html            # Layout maestro con sidebar, modal de auditoría, banner updater
│       ├── dashboard.html       # Dashboard con KPIs y menú contextual de clic derecho
│       ├── despachos.html       # Tabla de despachos con menú contextual
│       ├── despacho_detalle.html# Ficha técnica con auditoría y modal visor PDF interactivo
│       ├── mercancias.html      # Catálogo de mercancías con mini KPIs y paginación de 35 filas
│       ├── configuracion.html   # Configuración de Turso, GDrive, Backups ZIP y Actualizaciones
│       ├── upload.html          # Subida manual de archivos
│       └── revisar.html         # Bandeja de revisión dual con visor PDF interactivo
├── backups/                     # Carpeta de respaldos .ZIP (sincronizada en Google Drive)
├── updates/                     # Paquetes de actualización (update_latest.zip, version.json)
├── data/
│   └── despachos.db             # Base de datos SQLite local
├── tests/                       # Suite de 31 pruebas automatizadas (100% pasando)
│   ├── test_backup_updater.py   # Pruebas de Backups y Actualizaciones Multi-PC
│   ├── test_field_extractor.py
│   ├── test_http_endpoints.py
│   ├── test_normalizer.py
│   └── test_pipeline.py
├── .env                         # Variables de entorno (Turso Token, Drive Folder ID, etc.)
├── service_account.json         # Credenciales de Google Cloud Service Account
├── requirements.txt             # Dependencias del proyecto
├── iniciar_app.bat              # Launcher en 1 clic auto-instalable con apertura de navegador
├── iniciar.bat                  # Acceso directo rápido
└── CONTINUACION_ANTIGRAVITY.md  # Este archivo de handover
```

---

## 🔐 3. Credenciales y Configuración Activa

### A. Archivo `.env`:
```ini
DATABASE_URL=sqlite:///./data/despachos.db
UPLOAD_DIR=./uploads
DATA_DIR=./data
OCR_ENABLED=true
OCR_LANGUAGE=spa+eng+por
TESSERACT_CMD=
MAX_UPLOAD_MB=50
CONFIDENCE_REVIEW=0.80
APP_TITLE=Sistema de Gestión de Despachos Aduaneros
APP_VERSION=1.0.0
DEBUG=true

# Turso Cloud Database
TURSO_DATABASE_URL=libsql://despachos-alifarhat.aws-us-east-1.turso.io
TURSO_AUTH_TOKEN=eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9... (Token JWT activo)

# Google Drive Sync
GDRIVE_FOLDER_ID=1NP6zJHL9w_bV0W1BysIDRIZ5FXZzc5Kv
GDRIVE_CREDENTIALS_FILE=./service_account.json
```

### B. Google Drive:
- **Carpeta Vinculada**: `1NP6zJHL9w_bV0W1BysIDRIZ5FXZzc5Kv` (`DESPACHOS FINIQUITADOS`).
- **Service Account Email**: `despachos-bot@despadudiario.iam.gserviceaccount.com` (Compartida con rol de Lector).
- **Archivo de credenciales**: `service_account.json` en la raíz.

---

## 🚀 4. Guía para Continuar Mañana desde Otra Computadora

Cuando abras este proyecto en otra PC con **Google Antigravity**:

1. **Abrir la carpeta en Antigravity**:
   - Abre la carpeta del proyecto `despacho` (sincronizada vía Google Drive o Git).
2. **Iniciar la Aplicación**:
   - Haz doble clic en `iniciar_app.bat` (o `iniciar.bat`).
   - El script verificará el repositorio Git y ejecutará `git pull --ff-only` automático si está conectado.
   - Verificará las dependencias (`pip install -r requirements.txt`) y abrirá el navegador en `http://127.0.0.1:8000`.
3. **Descargar los datos de Turso Cloud (si la base local está vacía)**:
   - Ve a [http://127.0.0.1:8000/configuracion](http://127.0.0.1:8000/configuracion) y presiona **"Descargar Datos a esta PC (Pull)"**.
4. **Verificar que todas las pruebas pasen al 100%**:
   ```bash
   python -m pytest -v
   # Pasan las 36 pruebas exitosamente (100% OK)
   ```

---

## 🎯 5. Tareas Implementadas y Estado Actual

- [x] **Configurar Flujo de Actualizaciones con Git / GitHub**:
  - `git pull` automático y no bloqueante en `iniciar_app.bat` al abrir la aplicación.
  - Endpoints `/api/updates/git/status` y `/api/updates/git/pull` en FastAPI.
  - Tarjeta en `/configuracion` con estado de la rama, hash de commit, fecha y botón de sincronización.
  - Respaldo preventivo automático de base de datos antes de aplicar cualquier `git pull`.
  - Fallback por ZIP (`update_latest.zip` y `version.json`) preservado para equipos sin Git instalado.
- [x] **Filtros por Rango de Fechas en Catálogo de Mercancías (`/mercancias`)**:
  - Inputs interactivos `Fecha Desde` y `Fecha Hasta` en la barra de filtros rápidos.
  - Columna de Fecha del Despacho en la tabla unificada.
  - Filtrado reactivo en JavaScript con actualización instantánea de KPIs (Cantidad y Total FOB).
  - Exportación unificada a Excel (.xlsx) y CSV/Sheets filtrada por fechas exactas.
- [x] **Notificaciones Webhook / Telegram**:
  - Módulo `NotificationService` con integración oficial a la Telegram Bot API y Webhooks genéricos (Discord, Make, Zapier, n8n).
  - Notificación automática con resumen completo (Nº Despacho, Importador, Dueño, Canal Verde/Naranja/Rojo, Totales FOB/CIF y mercancías extraídas) cuando el Auto-Vigilante procesa nuevos despachos.
  - Panel de configuración interactivo en `/configuracion` con switch de activación y botón para *"Enviar Notificación de Prueba"*.

---
*Fin del documento de traspaso.*
