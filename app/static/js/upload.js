// upload.js - Manejador de carga masiva de PDFs y feedback en tiempo real

document.addEventListener('DOMContentLoaded', () => {
    const dropzone = document.getElementById('dropzone');
    const fileInput = document.getElementById('fileInput');
    const allowDuplicate = document.getElementById('allowDuplicate');
    const progressContainer = document.getElementById('progressContainer');
    const progressBar = document.getElementById('progressBar');
    const progressStatus = document.getElementById('progressStatus');
    const progressPercent = document.getElementById('progressPercent');
    const resultsContainer = document.getElementById('resultsContainer');
    const resultsTableBody = document.getElementById('resultsTableBody');

    if (!dropzone || !fileInput) return;

    // Prevenir comportamientos por defecto del navegador en Drag & Drop
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropzone.addEventListener(eventName, preventDefaults, false);
        document.body.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }

    // Efectos visuales de arrastrar
    ['dragenter', 'dragover'].forEach(eventName => {
        dropzone.addEventListener(eventName, () => dropzone.classList.add('dragover'), false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropzone.addEventListener(eventName, () => dropzone.classList.remove('dragover'), false);
    });

    // Manejar archivo soltado
    dropzone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        const files = dt.files;
        if (files.length > 0) {
            handleFiles(files);
        }
    });

    // Manejar selección por explorador
    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFiles(e.target.files);
        }
    });

    async function handleFiles(fileList) {
        const files = Array.from(fileList).filter(f => f.name.toLowerCase().endsWith('.pdf'));
        if (files.length === 0) {
            alert('Por favor selecciona al menos un archivo con formato .pdf');
            return;
        }

        // Mostrar progreso
        progressContainer.classList.remove('d-none');
        resultsContainer.classList.remove('d-none');
        resultsTableBody.innerHTML = '';
        progressBar.style.width = '10%';
        progressPercent.textContent = '10%';
        progressStatus.textContent = `Enviando ${files.length} archivo(s) al servidor...`;

        const formData = new FormData();
        files.forEach(file => {
            formData.append('files', file);
        });
        formData.append('allow_duplicate', allowDuplicate.checked);
        const propVal = document.getElementById('propietarioInput')?.value?.trim();
        if (propVal) {
            formData.append('propietario', propVal);
        }

        try {
            progressBar.style.width = '50%';
            progressPercent.textContent = '50%';
            progressStatus.textContent = 'Extrayendo texto y procesando datos aduaneros...';

            const response = await fetch('/upload/process-batch', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                throw new Error(`Error en el servidor: ${response.statusText}`);
            }

            const data = await response.json();
            
            progressBar.style.width = '100%';
            progressPercent.textContent = '100%';
            progressStatus.textContent = '¡Procesamiento completado!';

            renderResults(data.results);

        } catch (error) {
            progressStatus.textContent = `Error: ${error.message}`;
            progressBar.classList.add('bg-danger');
        }
    }

    function renderResults(results) {
        resultsTableBody.innerHTML = '';

        results.forEach(res => {
            const tr = document.createElement('tr');
            
            let badgeClass = 'bg-primary-subtle text-primary border border-primary-subtle';
            if (res.status === 'CONFIRMADO') badgeClass = 'bg-success-subtle text-success border border-success-subtle';
            if (res.status === 'REVISAR') badgeClass = 'bg-warning-subtle text-warning border border-warning-subtle';
            if (res.status === 'ERROR') badgeClass = 'bg-danger-subtle text-danger border border-danger-subtle';
            if (res.status === 'DUPLICADO') badgeClass = 'bg-secondary-subtle text-secondary border border-secondary-subtle';

            let actionHtml = '-';
            if (res.despacho_id) {
                actionHtml = `
                    <div class="btn-group btn-group-sm">
                        <a href="/despachos/${res.despacho_id}" class="btn btn-light border" title="Ver Detalle">
                            <i class="bi bi-eye"></i>
                        </a>
                        <a href="/revisar/${res.despacho_id}" class="btn btn-light border text-warning" title="Revisar">
                            <i class="bi bi-pencil-square"></i>
                        </a>
                        <a href="/despachos/${res.despacho_id}/pdf" target="_blank" class="btn btn-light border text-danger" title="Ver PDF">
                            <i class="bi bi-file-earmark-pdf"></i>
                        </a>
                    </div>
                `;
            }

            tr.innerHTML = `
                <td class="fw-semibold small text-truncate" style="max-width: 200px;">${res.filename}</td>
                <td class="fw-bold text-primary">${res.numero_despacho || '-'}</td>
                <td class="small text-truncate" style="max-width: 180px;">${res.importador || '-'}</td>
                <td><span class="badge ${badgeClass}">${res.status}</span></td>
                <td class="small text-muted">${res.message}</td>
                <td class="text-end">${actionHtml}</td>
            `;

            resultsTableBody.appendChild(tr);
        });
    }
});
