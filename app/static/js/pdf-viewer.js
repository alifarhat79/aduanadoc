/**
 * AduanaDoc - Visor PDF Interactivo de Alta Resolución (PDF.js)
 * Proporciona controles completos de navegación, zoom, rotación, modo continuo y ajuste automático.
 */

class AduanaPDFViewer {
    constructor(options) {
        this.containerId = options.containerId || 'pdfViewerContainer';
        this.pdfUrl = options.pdfUrl;
        this.downloadUrl = options.downloadUrl || options.pdfUrl;
        this.totalPageCount = 0;
        this.currentPageNum = 1;
        this.scale = options.initialScale || 1.15;
        this.rotation = 0;
        this.pdfDoc = null;
        this.pageRendering = false;
        this.pageNumPending = null;
        this.viewMode = options.viewMode || 'all'; // 'all' (continuo) | 'single' (página única)
        this.pageRenderTasks = {};

        // Inicializar worker
        if (typeof pdfjsLib !== 'undefined') {
            pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/js/pdf.worker.min.js';
        }

        this.initDOM();
        if (this.pdfUrl) {
            this.loadDocument(this.pdfUrl);
        }
    }

    initDOM() {
        const container = document.getElementById(this.containerId);
        if (!container) return;

        container.innerHTML = `
            <div class="pdf-viewer-card shadow-sm border-0">
                <!-- Barra de herramientas superior -->
                <div class="pdf-toolbar">
                    <div class="pdf-toolbar-group">
                        <span class="small fw-bold text-white me-2 d-none d-md-inline">
                            <i class="bi bi-file-earmark-pdf-fill text-danger me-1"></i> Documento
                        </span>
                        <button type="button" class="pdf-btn" id="${this.containerId}_prevPage" title="Página Anterior">
                            <i class="bi bi-chevron-left"></i>
                        </button>
                        <div class="pdf-page-indicator">
                            <input type="number" id="${this.containerId}_pageInput" class="pdf-page-input" value="1" min="1">
                            <span>/ <span id="${this.containerId}_pageTotal">1</span></span>
                        </div>
                        <button type="button" class="pdf-btn" id="${this.containerId}_nextPage" title="Página Siguiente">
                            <i class="bi bi-chevron-right"></i>
                        </button>
                    </div>

                    <div class="pdf-toolbar-group">
                        <button type="button" class="pdf-btn" id="${this.containerId}_zoomOut" title="Reducir Zoom">
                            <i class="bi bi-dash-lg"></i>
                        </button>
                        <span class="pdf-zoom-badge" id="${this.containerId}_zoomBadge">115%</span>
                        <button type="button" class="pdf-btn" id="${this.containerId}_zoomIn" title="Aumentar Zoom">
                            <i class="bi bi-plus-lg"></i>
                        </button>
                        <button type="button" class="pdf-btn" id="${this.containerId}_fitWidth" title="Ajustar al Ancho">
                            <i class="bi bi-arrows-expand"></i> Ancho
                        </button>
                        <button type="button" class="pdf-btn" id="${this.containerId}_rotate" title="Rotar 90°">
                            <i class="bi bi-arrow-clockwise"></i>
                        </button>
                    </div>

                    <div class="pdf-toolbar-group">
                        <div class="btn-group btn-group-sm" role="group">
                            <button type="button" class="pdf-btn active" id="${this.containerId}_modeAll" title="Ver todas las páginas continuas">
                                <i class="bi bi-view-stacked"></i> Continuo
                            </button>
                            <button type="button" class="pdf-btn" id="${this.containerId}_modeSingle" title="Ver página por página">
                                <i class="bi bi-file-earmark"></i> Simple
                            </button>
                        </div>
                        <a href="${this.pdfUrl}" target="_blank" class="pdf-btn btn-primary" title="Abrir en pestaña nueva">
                            <i class="bi bi-box-arrow-up-right"></i>
                        </a>
                        <a href="${this.downloadUrl}" download class="pdf-btn" title="Descargar PDF">
                            <i class="bi bi-download"></i>
                        </a>
                    </div>
                </div>

                <!-- Viewport contenedor del PDF -->
                <div class="pdf-viewport" id="${this.containerId}_viewport">
                    <div class="pdf-loading-overlay" id="${this.containerId}_loading">
                        <div class="spinner-border text-light mb-2" role="status">
                            <span class="visually-hidden">Cargando PDF...</span>
                        </div>
                        <div class="small fw-semibold">Cargando documento aduanero...</div>
                    </div>
                    <div id="${this.containerId}_pagesContainer" class="w-100 d-flex flex-column align-items-center"></div>
                </div>
            </div>
        `;

        this.bindEvents();
    }

    bindEvents() {
        const id = this.containerId;

        document.getElementById(`${id}_prevPage`)?.addEventListener('click', () => this.prevPage());
        document.getElementById(`${id}_nextPage`)?.addEventListener('click', () => this.nextPage());
        
        const pageInput = document.getElementById(`${id}_pageInput`);
        pageInput?.addEventListener('change', (e) => {
            const num = parseInt(e.target.value, 10);
            if (num >= 1 && num <= this.totalPageCount) {
                this.goToPage(num);
            } else {
                e.target.value = this.currentPageNum;
            }
        });

        document.getElementById(`${id}_zoomIn`)?.addEventListener('click', () => this.zoom(0.15));
        document.getElementById(`${id}_zoomOut`)?.addEventListener('click', () => this.zoom(-0.15));
        document.getElementById(`${id}_fitWidth`)?.addEventListener('click', () => this.fitWidth());
        document.getElementById(`${id}_rotate`)?.addEventListener('click', () => this.rotate());

        const modeAllBtn = document.getElementById(`${id}_modeAll`);
        const modeSingleBtn = document.getElementById(`${id}_modeSingle`);

        modeAllBtn?.addEventListener('click', () => {
            if (this.viewMode !== 'all') {
                this.viewMode = 'all';
                modeAllBtn.classList.add('active');
                modeSingleBtn.classList.remove('active');
                this.renderAllPages();
            }
        });

        modeSingleBtn?.addEventListener('click', () => {
            if (this.viewMode !== 'single') {
                this.viewMode = 'single';
                modeSingleBtn.classList.add('active');
                modeAllBtn.classList.remove('active');
                this.renderSinglePage(this.currentPageNum);
            }
        });

        // Detectar página actual al hacer scroll en modo continuo
        const viewport = document.getElementById(`${id}_viewport`);
        viewport?.addEventListener('scroll', () => {
            if (this.viewMode === 'all') {
                this.detectCurrentPageOnScroll();
            }
        });
    }

    async loadDocument(url) {
        const id = this.containerId;
        const loading = document.getElementById(`${id}_loading`);
        if (loading) loading.style.display = 'flex';

        try {
            if (typeof pdfjsLib === 'undefined') {
                throw new Error('PDF.js library is not loaded');
            }

            const loadingTask = pdfjsLib.getDocument({
                url: url,
                cMapUrl: 'https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/cmaps/',
                cMapPacked: true,
            });

            this.pdfDoc = await loadingTask.promise;
            this.totalPageCount = this.pdfDoc.numPages;

            const pageTotalEl = document.getElementById(`${id}_pageTotal`);
            if (pageTotalEl) pageTotalEl.textContent = this.totalPageCount;

            const pageInputEl = document.getElementById(`${id}_pageInput`);
            if (pageInputEl) pageInputEl.max = this.totalPageCount;

            this.updateZoomBadge();

            if (this.viewMode === 'all') {
                await this.renderAllPages();
            } else {
                await this.renderSinglePage(1);
            }

            if (loading) loading.style.display = 'none';

        } catch (error) {
            console.error('Error al cargar PDF:', error);
            if (loading) loading.style.display = 'none';
            this.showErrorState(error);
        }
    }

    showErrorState(error) {
        const id = this.containerId;
        const container = document.getElementById(`${id}_pagesContainer`);
        if (!container) return;

        container.innerHTML = `
            <div class="pdf-error-state my-5 text-center shadow">
                <i class="bi bi-file-earmark-x text-warning fs-1 d-block mb-3"></i>
                <h6 class="fw-bold text-white mb-2">No se pudo visualizar el PDF directamente</h6>
                <p class="small text-muted mb-3">
                    Es posible que el archivo físico no esté en esta computadora (sincronizado vía nube) o el formato requiera apertura externa.
                </p>
                <div class="d-flex justify-content-center gap-2">
                    <a href="${this.pdfUrl}" target="_blank" class="btn btn-sm btn-primary">
                        <i class="bi bi-box-arrow-up-right me-1"></i> Abrir en Pestaña
                    </a>
                    <a href="${this.downloadUrl}" download class="btn btn-sm btn-outline-light">
                        <i class="bi bi-download me-1"></i> Descargar
                    </a>
                </div>
            </div>
        `;
    }

    async renderSinglePage(num) {
        if (!this.pdfDoc) return;
        const id = this.containerId;
        const container = document.getElementById(`${id}_pagesContainer`);
        if (!container) return;

        container.innerHTML = '';
        this.currentPageNum = num;
        this.updatePageIndicator(num);

        const pageWrapper = this.createPageWrapper(num);
        container.appendChild(pageWrapper);

        await this.renderPageCanvas(num, pageWrapper);
    }

    async renderAllPages() {
        if (!this.pdfDoc) return;
        const id = this.containerId;
        const container = document.getElementById(`${id}_pagesContainer`);
        if (!container) return;

        container.innerHTML = '';

        for (let i = 1; i <= this.totalPageCount; i++) {
            const pageWrapper = this.createPageWrapper(i);
            container.appendChild(pageWrapper);
            await this.renderPageCanvas(i, pageWrapper);
        }
    }

    createPageWrapper(pageNum) {
        const wrapper = document.createElement('div');
        wrapper.className = 'pdf-page-wrapper';
        wrapper.id = `${this.containerId}_page_wrapper_${pageNum}`;
        wrapper.dataset.pageNum = pageNum;

        const label = document.createElement('div');
        label.className = 'pdf-page-label';
        label.textContent = `Pág. ${pageNum}`;
        wrapper.appendChild(label);

        const canvas = document.createElement('canvas');
        canvas.className = 'pdf-canvas';
        canvas.id = `${this.containerId}_canvas_${pageNum}`;
        wrapper.appendChild(canvas);

        return wrapper;
    }

    async renderPageCanvas(pageNum, wrapper) {
        try {
            const page = await this.pdfDoc.getPage(pageNum);
            const canvas = wrapper.querySelector('canvas');
            const ctx = canvas.getContext('2d');

            const dpr = window.devicePixelRatio || 1;
            const viewport = page.getViewport({ scale: this.scale * dpr, rotation: this.rotation });

            canvas.width = viewport.width;
            canvas.height = viewport.height;
            canvas.style.width = `${viewport.width / dpr}px`;
            canvas.style.height = `${viewport.height / dpr}px`;

            const renderContext = {
                canvasContext: ctx,
                viewport: viewport
            };

            // Cancelar tarea previa si existe
            if (this.pageRenderTasks[pageNum]) {
                this.pageRenderTasks[pageNum].cancel();
            }

            const renderTask = page.render(renderContext);
            this.pageRenderTasks[pageNum] = renderTask;

            await renderTask.promise;
            delete this.pageRenderTasks[pageNum];

        } catch (err) {
            if (err.name !== 'RenderingCancelledException') {
                console.error(`Error renderizando página ${pageNum}:`, err);
            }
        }
    }

    zoom(delta) {
        const newScale = Math.min(Math.max(this.scale + delta, 0.5), 3.0);
        this.setScale(newScale);
    }

    setScale(newScale) {
        this.scale = Math.round(newScale * 100) / 100;
        this.updateZoomBadge();
        if (this.viewMode === 'all') {
            this.renderAllPages();
        } else {
            this.renderSinglePage(this.currentPageNum);
        }
    }

    fitWidth() {
        if (!this.pdfDoc) return;
        const viewportEl = document.getElementById(`${this.containerId}_viewport`);
        if (!viewportEl) return;

        this.pdfDoc.getPage(this.currentPageNum).then(page => {
            const defaultViewport = page.getViewport({ scale: 1.0, rotation: this.rotation });
            const availableWidth = viewportEl.clientWidth - 48; // padding
            if (availableWidth > 0 && defaultViewport.width > 0) {
                const targetScale = availableWidth / defaultViewport.width;
                this.setScale(targetScale);
            }
        });
    }

    rotate() {
        this.rotation = (this.rotation + 90) % 360;
        if (this.viewMode === 'all') {
            this.renderAllPages();
        } else {
            this.renderSinglePage(this.currentPageNum);
        }
    }

    prevPage() {
        if (this.currentPageNum <= 1) return;
        this.goToPage(this.currentPageNum - 1);
    }

    nextPage() {
        if (this.currentPageNum >= this.totalPageCount) return;
        this.goToPage(this.currentPageNum + 1);
    }

    goToPage(pageNum) {
        this.currentPageNum = pageNum;
        this.updatePageIndicator(pageNum);

        if (this.viewMode === 'single') {
            this.renderSinglePage(pageNum);
        } else {
            // Scroll hacia la página
            const pageEl = document.getElementById(`${this.containerId}_page_wrapper_${pageNum}`);
            if (pageEl) {
                pageEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }
    }

    updatePageIndicator(num) {
        const id = this.containerId;
        const input = document.getElementById(`${id}_pageInput`);
        if (input) input.value = num;

        const prevBtn = document.getElementById(`${id}_prevPage`);
        const nextBtn = document.getElementById(`${id}_nextPage`);
        if (prevBtn) prevBtn.disabled = (num <= 1);
        if (nextBtn) nextBtn.disabled = (num >= this.totalPageCount);
    }

    updateZoomBadge() {
        const badge = document.getElementById(`${this.containerId}_zoomBadge`);
        if (badge) {
            badge.textContent = `${Math.round(this.scale * 100)}%`;
        }
    }

    detectCurrentPageOnScroll() {
        const id = this.containerId;
        const viewport = document.getElementById(`${id}_viewport`);
        if (!viewport) return;

        const wrappers = viewport.querySelectorAll('.pdf-page-wrapper');
        const viewportRect = viewport.getBoundingClientRect();

        for (const wrapper of wrappers) {
            const rect = wrapper.getBoundingClientRect();
            if (rect.top <= viewportRect.top + 100 && rect.bottom >= viewportRect.top + 100) {
                const pageNum = parseInt(wrapper.dataset.pageNum, 10);
                if (pageNum && pageNum !== this.currentPageNum) {
                    this.currentPageNum = pageNum;
                    this.updatePageIndicator(pageNum);
                }
                break;
            }
        }
    }
}

// Helper global para inicializar
window.initAduanaPDFViewer = function(options) {
    return new AduanaPDFViewer(options);
};
