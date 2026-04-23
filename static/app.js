/* ─────────────────────────────────────────────────────────────────────────────
   Auditor ONPE 2026 — app.js
   ───────────────────────────────────────────────────────────────────────────── */

let mesaActual   = null;   // código de mesa activo
let actasActivas = [];     // lista de actas descargadas para la mesa actual
let currentPage  = 1;      // página PDF visible


// ── Inicialización ────────────────────────────────────────────────────────────
// (Ver DOMContentLoaded al final del archivo)

// ── Utilidades ────────────────────────────────────────────────────────────────

function toast(msg, tipo = "success") {
  const id = "t" + Date.now();
  const colorMap = { success:"bg-success", danger:"bg-danger",
                     warning:"bg-warning text-dark", info:"bg-info text-dark" };
  const html = `
    <div id="${id}" class="toast align-items-center text-white ${colorMap[tipo] || "bg-secondary"} border-0"
         role="alert" aria-live="assertive">
      <div class="d-flex">
        <div class="toast-body">${msg}</div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
      </div>
    </div>`;
  document.getElementById("toastContainer").insertAdjacentHTML("beforeend", html);
  const el = new bootstrap.Toast(document.getElementById(id), { delay: 4000 });
  el.show();
}

async function api(url, method = "GET", body = null) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(url, opts);
  return res.json();
}

function setProcessing(on) {
  document.getElementById("processingSpinner")
          .classList.toggle("d-none", !on);
}

function idEleccionFromSelect(selectId) {
  const val = document.getElementById(selectId).value;
  return val ? parseInt(val) : null;
}


// ── Stats ─────────────────────────────────────────────────────────────────────

async function refreshStats() {
  const r = await api("/api/db/stats");
  if (!r.ok) return;
  const d = r.data;
  document.getElementById("statActas").textContent    = d.total_actas || 0;
  document.getElementById("statOcr").textContent      = d.actas_con_ocr || 0;
  document.getElementById("statAnomalias").textContent= d.actas_con_anomalia || 0;
  const disc = d.discrepancias || 0;
  const discEl = document.getElementById("statDisc");
  if (discEl) discEl.textContent = disc;
  // Mini-stats en col-masivo
  const msA  = document.getElementById("msActas");
  const msO  = document.getElementById("msOcr");
  const msAn = document.getElementById("msAnom");
  const msD  = document.getElementById("msDisc");
  if (msA)  msA.textContent  = (d.total_actas || 0).toLocaleString();
  if (msO)  msO.textContent  = (d.actas_con_ocr || 0).toLocaleString();
  if (msAn) msAn.textContent = (d.actas_con_anomalia || 0).toLocaleString();
  if (msD)  msD.textContent  = disc.toLocaleString();
}


// ── Buscar / cargar mesa ──────────────────────────────────────────────────────

function buscarMesa() {
  const codigo = document.getElementById("inputMesa").value.trim().padStart(6, "0");
  if (!codigo || codigo.length > 6) { toast("Código inválido", "warning"); return; }
  cargarMesa(codigo);
}

async function cargarMesa(codigo) {
  mesaActual = codigo.padStart(6, "0");

  // Mostrar panel de resultados, ocultar placeholder
  document.getElementById("panelVacio")?.classList.add("d-none");
  document.getElementById("mesaHeader")?.classList.remove("d-none");
  document.getElementById("indTabs")?.classList.remove("d-none");

  // Header
  document.getElementById("mesaTitulo").innerHTML =
    `Mesa <span class="font-monospace">${mesaActual}</span>`;
  document.getElementById("mesaSubtitulo").textContent = "Consultando ONPE...";

  // Mostrar placeholder
  document.getElementById("actasContainer").innerHTML =
    `<div class="text-center py-5"><div class="spinner-border"></div><p class="mt-2">Consultando API ONPE...</p></div>`;

  // Buscar en API ONPE
  const r = await api(`/api/buscar_mesa?codigo_mesa=${mesaActual}`);
  if (!r.ok) {
    document.getElementById("mesaSubtitulo").textContent = "Error al consultar";
    document.getElementById("actasContainer").innerHTML =
      `<div class="alert alert-danger">${r.error}</div>`;
    return;
  }

  const actas = r.data || [];
  document.getElementById("mesaSubtitulo").textContent =
    `${actas.length} acta(s) encontrada(s)`;

  // Renderizar actas (botón de descarga)
  renderActas(actas);

  // Pre-populate selects con todas las actas de la API
  const selects = ["selectActaViewer","selectActaOcr","selectActaForense","selectActaComparacion"];
  selects.forEach(id => {
    const el = document.getElementById(id);
    el.innerHTML = `<option value="">-- Selecciona acta --</option>`;
    actas.forEach(a => {
      const opt = document.createElement("option");
      opt.value = a.idEleccion;
      opt.textContent = `${a.tipoEleccion || "Elección " + a.idEleccion} (ID ${a.idEleccion})`;
      el.appendChild(opt);
    });
  });
}

function renderActas(actas) {
  if (!actas.length) {
    document.getElementById("actasContainer").innerHTML =
      `<div class="alert alert-warning">No se encontraron actas para esta mesa en ONPE.</div>`;
    return;
  }

  let html = `
    <div class="d-flex gap-2 mb-3">
      <button class="btn btn-primary" onclick="descargarMesa()">
        <i class="bi bi-download"></i> Descargar PDFs
      </button>
      <button class="btn btn-outline-info btn-sm" onclick="verDiagnostico()">
        <i class="bi bi-bug"></i> Diagnóstico
      </button>
    </div>
    <div class="row g-3">`;

  for (const a of actas) {
    const estadoBadge = a.estado === "ENTREGADA"
      ? `<span class="badge bg-success">${a.estado}</span>`
      : `<span class="badge bg-secondary">${a.estado || "—"}</span>`;
    html += `
      <div class="col-md-6 col-xl-4">
        <div class="card h-100">
          <div class="card-header d-flex justify-content-between align-items-center">
            <strong>${a.tipoEleccion || "Elección " + a.idEleccion}</strong>
            ${estadoBadge}
          </div>
          <div class="card-body small">
            <div><b>ID Acta:</b> <code>${a.id || "—"}</code></div>
            <div><b>ID Elección:</b> ${a.idEleccion}</div>
          </div>
        </div>
      </div>`;
  }
  html += `</div>`;
  document.getElementById("actasContainer").innerHTML = html;
}

function renderResultadosDescarga(resultados) {
  let html = `<div class="mb-3">`;
  for (const r of resultados) {
    const icon = r.pdf_descargado
      ? `<i class="bi bi-check-circle-fill text-success"></i>`
      : `<i class="bi bi-x-circle-fill text-danger"></i>`;
    html += `
      <div class="d-flex align-items-center gap-2 mb-1 p-2 rounded border">
        ${icon}
        <span><b>${r.tipo}</b> (ID ${r.id_eleccion})</span>
        ${r.pdf_descargado
          ? `<span class="badge bg-success ms-auto">PDF descargado</span>`
          : `<span class="badge bg-danger ms-auto" title="${r.error || ''}">${r.tiene_url ? "Error descarga" : "Sin PDF"}</span>`}
      </div>`;
  }
  html += `</div>`;
  document.getElementById("actasContainer").innerHTML = html;

  // Actualizar selects con las actas que SÍ se descargaron
  const conPdf = resultados.filter(r => r.pdf_descargado);
  if (conPdf.length > 0) {
    const selects = ["selectActaViewer","selectActaOcr","selectActaForense","selectActaComparacion"];
    selects.forEach(id => {
      const el = document.getElementById(id);
      el.innerHTML = `<option value="">-- Selecciona acta --</option>`;
      conPdf.forEach(r => {
        const opt = document.createElement("option");
        opt.value = r.id_eleccion;
        opt.textContent = `${r.tipo} (ID ${r.id_eleccion})`;
        el.appendChild(opt);
      });
    });
  }
}

async function verDiagnostico() {
  if (!mesaActual) return;
  toast("Ejecutando diagnóstico...", "info");
  const r = await api(`/api/debug/mesa/${mesaActual}`);
  if (!r.ok) { toast(r.error, "danger"); return; }

  let html = `<h6>Diagnóstico mesa ${mesaActual}</h6>`;
  for (const acta of (r.data.actas || [])) {
    const accesible = acta.url_accesible === true
      ? `<span class="badge bg-success">URL OK (${acta.url_status})</span>`
      : acta.url_accesible === false
        ? `<span class="badge bg-danger">URL 403/error (${acta.url_status})</span>`
        : `<span class="badge bg-secondary">Sin URL</span>`;

    html += `
      <div class="card mb-2">
        <div class="card-header small d-flex gap-2 align-items-center">
          <b>ID ${acta.id_eleccion}</b> — ${acta.estado}
          <span class="badge bg-${acta.mongo_id_candidates?.length > 0 ? 'success' : 'danger'}">
            ${acta.mongo_id_candidates?.length > 0 ? 'MongoDB ID ✓' : 'Sin MongoDB ID'}
          </span>
          ${accesible}
        </div>
        <div class="card-body small">
          <div><b>Candidates:</b> ${(acta.mongo_id_candidates || []).join(', ') || 'ninguno'}</div>
          <div><b>file endpoint HTTP:</b> ${acta.file_endpoint_status || '—'}</div>
          <div><b>Respuesta:</b> <code>${escHtml(JSON.stringify(acta.file_endpoint_response || acta.file_endpoint_response_text || ''))}</code></div>
          ${acta.error ? `<div class="text-danger"><b>Error:</b> ${escHtml(acta.error)}</div>` : ''}
        </div>
      </div>`;
  }

  document.getElementById("actasContainer").innerHTML = html;
}


// ── Descargar acta individual ─────────────────────────────────────────────────

async function descargarMesa() {
  if (!mesaActual) return;
  setProcessing(true);

  // Buscar metadatos de la mesa activa
  const card = document.querySelector(`[data-mesa="${mesaActual}"]`);
  const cat  = card ? card.dataset.cat : "";

  document.getElementById("actasContainer").innerHTML =
    `<div class="text-center py-4"><div class="spinner-border"></div><p class="mt-2">Descargando PDFs desde ONPE...</p></div>`;

  const r = await api("/api/descargar_mesa", "POST", {
    codigo_mesa: mesaActual,
    categoria:   cat,
  });

  setProcessing(false);
  if (!r.ok) {
    document.getElementById("actasContainer").innerHTML =
      `<div class="alert alert-danger">${r.error}</div>`;
    return;
  }

  const data = r.data;
  const pdfs = data.pdfs_descargados || 0;
  if (pdfs > 0) {
    toast(`${pdfs} PDF(s) descargados correctamente`, "success");
  } else {
    toast("No se pudieron descargar PDFs. Ver diagnóstico.", "warning");
  }

  renderResultadosDescarga(data.resultados || []);
  refreshStats();
}


// ── Procesar mesa completa ────────────────────────────────────────────────────

async function procesarMesaCompleta() {
  if (!mesaActual) { toast("Selecciona una mesa primero", "warning"); return; }
  setProcessing(true);
  toast(`Procesando mesa ${mesaActual}...`, "info");

  const r = await api("/api/procesar_todo", "POST", { codigo_mesa: mesaActual });
  setProcessing(false);

  if (!r.ok) { toast("Error: " + r.error, "danger"); return; }

  const data = r.data;
  let alertCount = 0;
  for (const acta of (data.resultados || [])) {
    if (acta.comparacion && acta.comparacion.alertas) {
      alertCount += acta.comparacion.alertas.length;
    }
  }

  toast(`Mesa ${mesaActual}: ${data.actas_procesadas} actas procesadas${alertCount ? ", ⚠ " + alertCount + " alertas" : " ✓"}`,
        alertCount ? "warning" : "success");
  refreshStats();
}


// ── Procesar lote ─────────────────────────────────────────────────────────────

async function procesarLote() {
  if (!confirm("¿Procesar TODAS las mesas de prueba? Esto puede tardar varios minutos.")) return;
  toast("Procesando lote... (revisa la consola del servidor)", "info");
  const r = await api("/api/procesar_lote", "POST");
  if (r.ok) {
    toast(`Lote completado: ${r.data.length} mesas consultadas`, "success");
    refreshStats();
  } else {
    toast("Error en lote: " + r.error, "danger");
  }
}


// ── Visor PDF ─────────────────────────────────────────────────────────────────

async function loadPdfPage(page) {
  const idEleccion = idEleccionFromSelect("selectActaViewer");
  if (!idEleccion || !mesaActual) {
    toast("Selecciona un acta primero", "warning"); return;
  }
  if (page < 1) page = 1;
  currentPage = page;

  document.getElementById("pdfViewerContainer").innerHTML =
    `<div class="spinner-border"></div>`;

  const r = await api(`/api/pdf_preview/${mesaActual}/${idEleccion}/${page}`);
  if (!r.ok) {
    document.getElementById("pdfViewerContainer").innerHTML =
      `<div class="alert alert-danger">${r.error}</div>`;
    return;
  }
  const d = r.data;
  document.getElementById("pdfPageInfo").textContent = `${d.page}/${d.total_pages}`;
  document.getElementById("pdfViewerContainer").innerHTML =
    `<img src="data:image/png;base64,${d.image_b64}" alt="Página ${d.page}" class="img-fluid" />`;
}

function downloadPdf() {
  const idEleccion = idEleccionFromSelect("selectActaViewer");
  if (!idEleccion || !mesaActual) { toast("Selecciona un acta", "warning"); return; }
  window.location.href = `/api/pdf_download/${mesaActual}/${idEleccion}`;
}


// ── OCR ───────────────────────────────────────────────────────────────────────

async function ejecutarOcr() {
  const idEleccion = idEleccionFromSelect("selectActaOcr");
  if (!idEleccion || !mesaActual) { toast("Selecciona un acta", "warning"); return; }
  setProcessing(true);
  document.getElementById("ocrContainer").innerHTML =
    `<div class="spinner-border"></div> <span>Ejecutando OCR (puede tardar ~30s)...</span>`;

  const r = await api("/api/procesar_ocr", "POST", {
    codigo_mesa: mesaActual, id_eleccion: idEleccion,
  });
  setProcessing(false);

  if (!r.ok) {
    document.getElementById("ocrContainer").innerHTML =
      `<div class="alert alert-danger">${r.error}</div>`;
    return;
  }
  renderOcr(r.data);
}

async function cargarOcr() {
  const idEleccion = idEleccionFromSelect("selectActaOcr");
  if (!idEleccion || !mesaActual) { toast("Selecciona un acta", "warning"); return; }
  const r = await api(`/api/db/ocr/${mesaActual}/${idEleccion}`);
  if (!r.ok) { toast(r.error, "warning"); return; }
  renderOcr({ datos_extraidos: r.data.datos_json, metodo: r.data.metodo_ocr,
              es_escaneado: r.data.es_escaneado, texto_preview: r.data.texto_crudo?.substring(0,3000) });
}

function renderOcr(data) {
  const d = data.datos_extraidos || {};
  const votos = d.votos_por_partido || {};

  // Tabla de votos con colores alternados
  let filas = "";
  let totalVotos = 0;
  const entries = Object.entries(votos);
  entries.sort((a, b) => b[1] - a[1]); // ordenar por votos desc
  for (const [partido, vts] of entries) {
    const esEspecial = partido.startsWith("VOTOS") || partido.startsWith("TOTAL");
    const cls = esEspecial ? "table-secondary fw-bold" : "";
    const bar = !esEspecial && vts > 0
      ? `<div class="progress" style="height:4px;width:${Math.min(vts * 3, 100)}%"><div class="progress-bar bg-primary" style="width:100%"></div></div>`
      : "";
    filas += `<tr class="${cls}"><td>${escHtml(partido)}</td><td class="text-end fw-bold">${vts}</td><td>${bar}</td></tr>`;
    if (!esEspecial) totalVotos += vts;
  }

  let metaBadge = data.es_escaneado
    ? `<span class="badge bg-warning text-dark">Escaneado (OCR)</span>`
    : `<span class="badge bg-success">Electrónico (texto directo)</span>`;

  const metodo = data.metodo ? `<span class="badge bg-info ms-1">${data.metodo}</span>` : "";
  const dpiInfo = data.dpi_usado ? `<span class="badge bg-secondary ms-1">${data.dpi_usado} DPI</span>` : "";
  const txtLen = data.texto_completo_len ? `<span class="badge bg-dark ms-1">${data.texto_completo_len} chars</span>` : "";

  document.getElementById("ocrContainer").innerHTML = `
    <div class="row g-3">
      <div class="col-md-5">
        <div class="card">
          <div class="card-header d-flex flex-wrap gap-1 align-items-center">
            <strong>Datos extraídos</strong> ${metaBadge} ${metodo} ${dpiInfo} ${txtLen}
          </div>
          <div class="card-body small">
            <table class="table table-sm mb-0">
              <tr><td>Mesa</td><td><code>${d.mesa || "—"}</code></td></tr>
              <tr><td>Electores hábiles</td><td><strong>${d.electores_habiles ?? "—"}</strong></td></tr>
              <tr><td>Total votantes</td><td><strong>${d.total_votantes ?? "—"}</strong></td></tr>
              <tr><td>Votos válidos</td><td>${d.votos_validos ?? "—"}</td></tr>
              <tr><td>Votos nulos</td><td>${d.votos_nulos ?? "—"}</td></tr>
              <tr><td>Votos blancos</td><td>${d.votos_blanco ?? "—"}</td></tr>
              <tr><td>Votos impugnados</td><td>${d.votos_impugnados ?? "—"}</td></tr>
              <tr><td>Votos emitidos</td><td>${d.votos_emitidos ?? "—"}</td></tr>
            </table>
            <hr class="my-2">
            <div class="text-muted" style="font-size:.75rem">
              Firmas: ${(d.firmas_detectadas||[]).length} |
              DNIs: ${(d.dnis_detectados||[]).join(", ") || "ninguno"} |
              Horas: ${(d.horas_detectadas||[]).join(", ") || "—"}
            </div>
          </div>
        </div>
      </div>
      <div class="col-md-7">
        <div class="card">
          <div class="card-header d-flex justify-content-between">
            <strong>Votos por partido (OCR)</strong>
            <span class="badge bg-primary">${entries.length} partidos • Σ ${totalVotos}</span>
          </div>
          <div class="card-body p-0" style="max-height:400px;overflow-y:auto">
            <table class="table table-sm table-hover mb-0">
              <thead class="table-dark"><tr><th>Partido</th><th class="text-end" style="width:60px">Votos</th><th style="width:100px"></th></tr></thead>
              <tbody>${filas || "<tr><td colspan='3' class='text-muted text-center py-3'>Sin votos detectados por OCR</td></tr>"}</tbody>
            </table>
          </div>
        </div>
      </div>
      <div class="col-12">
        <div class="card">
          <div class="card-header d-flex justify-content-between">
            <strong>Texto extraído (preview)</strong>
            <button class="btn btn-outline-secondary btn-sm" onclick="this.parentElement.parentElement.querySelector('pre').classList.toggle('ocr-expanded')">
              Expandir/Colapsar
            </button>
          </div>
          <div class="card-body p-0">
            <pre id="ocrTexto" class="m-0 p-2" style="max-height:300px;overflow-y:auto;font-size:.7rem;white-space:pre-wrap">${escHtml(data.texto_preview || "Sin texto")}</pre>
          </div>
        </div>
      </div>
    </div>`;
}


// ── Forense ───────────────────────────────────────────────────────────────────

async function ejecutarForense() {
  const idEleccion = idEleccionFromSelect("selectActaForense");
  if (!idEleccion || !mesaActual) { toast("Selecciona un acta", "warning"); return; }
  setProcessing(true);
  document.getElementById("forenseContainer").innerHTML =
    `<div class="spinner-border"></div> <span>Analizando...</span>`;

  const r = await api("/api/analisis_forense", "POST", {
    codigo_mesa: mesaActual, id_eleccion: idEleccion,
  });
  setProcessing(false);

  if (!r.ok) {
    document.getElementById("forenseContainer").innerHTML =
      `<div class="alert alert-danger">${r.error}</div>`;
    return;
  }
  renderForense(r.data);
}

async function cargarForense() {
  const idEleccion = idEleccionFromSelect("selectActaForense");
  if (!idEleccion || !mesaActual) { toast("Selecciona un acta", "warning"); return; }
  const r = await api(`/api/db/forense/${mesaActual}/${idEleccion}`);
  if (!r.ok) { toast(r.error, "warning"); return; }
  renderForense(r.data.reporte_json);
}

function renderForense(rpt) {
  // Soporta tanto el nuevo formato (reporte_tecnico) como el legado
  const tech    = rpt.reporte_tecnico   || {};
  const meta    = rpt.metadatos         || rpt.metadata || {};
  const clf     = rpt.clasificacion     || {};
  const con     = rpt.conclusion        || {};
  const dots    = rpt.puntos_amarillos  || {};
  const incr    = rpt.actualizaciones_incrementales || {};
  const arts    = rpt.artefactos_imagen || {};
  const scanner = rpt.artefactos_scanner || {};
  const artif   = rpt.artefactos_artificiales || {};
  const imgpg   = rpt.imagenes_por_pagina || {};
  const txt     = rpt.capa_texto        || {};
  const alertas = rpt.alertas           || [];

  // ── Tipo de clasificación ──────────────────────────────────────────────
  const tipoCod  = tech.origin_classification || clf.classification || "E";
  const tipoDesc = tech.origin_description    || clf.classification_desc || "No determinado";
  const confianza= tech.confidence            || clf.confidence   || "bajo";
  const colorMap = { A: "success", B: "info", C: "warning", D: "danger", E: "secondary" };
  const icoMap   = { A: "file-earmark-text", B: "camera", C: "layers", D: "exclamation-triangle-fill", E: "question-circle" };
  const tipoBadge = `<span class="badge bg-${colorMap[tipoCod]||"secondary"} fs-6">
    <i class="bi bi-${icoMap[tipoCod]||"question"}"></i>
    Tipo ${tipoCod}</span>`;

  // ── Alertas ────────────────────────────────────────────────────────────
  const alertHtml = alertas.map(a => {
    const lvl = a.nivel === "alerta" ? "danger" : a.nivel === "ok" ? "success" : "info";
    const ico  = a.nivel === "alerta" ? "exclamation-triangle-fill" : a.nivel === "ok" ? "check-circle-fill" : "info-circle";
    return `<div class="alert alert-${lvl} py-2 mb-1 small">
      <i class="bi bi-${ico}"></i> ${escHtml(a.mensaje)}</div>`;
  }).join("") || `<div class="alert alert-success py-2 small">
    <i class="bi bi-check-circle"></i> Sin alertas forenses</div>`;

  // ── Risk flags ─────────────────────────────────────────────────────────
  const riskFlags = (tech.risk_flags || clf.risk_flags || []);
  const flagHtml = riskFlags.length
    ? riskFlags.map(f => `<li class="text-danger"><i class="bi bi-flag-fill"></i> ${escHtml(f)}</li>`).join("")
    : `<li class="text-muted">Sin flags de riesgo detectados</li>`;

  // ── Indicadores a favor / en contra ───────────────────────────────────
  const favHtml = (tech.indicators_for || clf.indicators_for || [])
    .map(f => `<li class="text-success small"><i class="bi bi-check2"></i> ${escHtml(f)}</li>`).join("");
  const contraHtml = (tech.indicators_against || clf.indicators_against || [])
    .map(f => `<li class="text-warning small"><i class="bi bi-dash"></i> ${escHtml(f)}</li>`).join("");

  // ── Imágenes por página ────────────────────────────────────────────────
  const imgRows = (imgpg.pages || []).map(pg => {
    const imgs = (pg.images || []).map(img =>
      `${img.width_px}×${img.height_px}px ${img.compression||""} ${img.color_space||""} ` +
      `x${img.x_ppi}dpi`
    ).join(", ");
    return `<tr><td>${pg.page}</td><td>${pg.image_count}</td><td class="small">${escHtml(imgs)||"—"}</td></tr>`;
  }).join("");

  // ── Prueba adicional requerida ─────────────────────────────────────────
  const pruebaHtml = (tech.additional_proof_needed || clf.additional_proof_needed || [])
    .map(p => `<li class="small text-muted">${escHtml(p)}</li>`).join("");

  // ── Metadatos técnicos canonicos ──────────────────────────────────────
  const producer    = tech.producer    || meta.producer    || meta.metadatos_pymupdf?.producer || "—";
  const creator     = tech.creator     || meta.creator     || meta.metadatos_pymupdf?.creator  || "—";
  const creDate     = tech.creation_date || meta.creation_date || meta.metadatos_pymupdf?.creationDate || "—";
  const modDate     = tech.mod_date    || meta.mod_date    || meta.metadatos_pymupdf?.modDate  || "—";
  const pdfVer      = tech.pdf_version || meta.pdf_version || "—";
  const pageCount   = tech.page_count  || meta.page_count  || meta.num_paginas || "—";
  const hasSig      = tech.digital_signature_present || meta.digital_signature_present || false;
  const hasAcro     = tech.acroform_present || meta.acroform_present || false;
  const incUpdates  = tech.incremental_updates || incr.incremental_updates || false;
  const incCount    = tech.incremental_update_count || incr.update_count || 0;
  const objCount    = tech.object_count || meta.object_count || "—";
  const docModel    = tech.document_model || rpt.estructura_pdf?.document_model || "—";
  const comprList   = (tech.compressions_found || imgpg.compressions_found || []).join(", ") || "—";
  const dpiAvg      = (tech.dpi_range?.avg) || (imgpg.dpi_range?.avg) || "—";
  const scanScore   = tech.scan_score  || scanner.scan_score  || 0;
  const artScore    = tech.artificial_score || artif.artificial_score || 0;
  const sha256      = tech.sha256 || (rpt.archivo||{}).sha256 || "—";
  const md5         = tech.md5    || (rpt.archivo||{}).md5    || "—";

  document.getElementById("forenseContainer").innerHTML = `
    <div class="row g-3">
      <!-- RESUMEN -->
      <div class="col-12">
        ${alertHtml}
        <div class="card border-${colorMap[tipoCod]||"secondary"} mt-2">
          <div class="card-body py-2">
            <div class="d-flex align-items-center gap-3">
              ${tipoBadge}
              <div>
                <strong>${escHtml(tipoDesc)}</strong>
                <span class="ms-2 badge bg-${confianza==="alto"?"success":confianza==="medio"?"warning":"secondary"}">
                  Confianza: ${confianza}</span>
              </div>
            </div>
            ${con.conclusion_tecnica
              ? `<p class="mt-2 mb-0 small text-muted fst-italic">${escHtml(con.conclusion_tecnica)}</p>`
              : ""}
          </div>
        </div>
      </div>

      <!-- METADATOS PDF -->
      <div class="col-md-6">
        <div class="card h-100">
          <div class="card-header fw-bold">
            <i class="bi bi-file-earmark-text"></i> Metadatos PDF
          </div>
          <div class="card-body p-0">
            <table class="table table-sm table-hover mb-0 small">
              <tr><td class="text-muted">Versión PDF</td><td><code>${pdfVer}</code></td></tr>
              <tr><td class="text-muted">Producer</td><td>${escHtml(producer)}</td></tr>
              <tr><td class="text-muted">Creator</td><td>${escHtml(creator)}</td></tr>
              <tr><td class="text-muted">Creación</td><td>${escHtml(creDate)}</td></tr>
              <tr><td class="text-muted">Modificación</td><td>${escHtml(String(modDate))}</td></tr>
              <tr><td class="text-muted">Páginas</td><td>${pageCount}</td></tr>
              <tr><td class="text-muted">Encriptado</td><td>${tech.encrypted?"Sí":"No"}</td></tr>
              <tr><td class="text-muted">Linearizado</td><td>${tech.linearized?"Sí":"No"}</td></tr>
              <tr><td class="text-muted">XMP metadata</td><td>${tech.xmp_present?"✅ Sí":"❌ No"}</td></tr>
              <tr><td class="text-muted">Firma digital</td><td>${hasSig?"✅ Sí":"❌ No"}</td></tr>
              <tr><td class="text-muted">AcroForm</td><td>${hasAcro?"Sí":"No"}</td></tr>
              <tr><td class="text-muted">XFA</td><td>${tech.xfa_present?"Sí":"No"}</td></tr>
              <tr><td class="text-muted">JavaScript</td><td>${tech.javascript_present
                ? "⚠ Sí":"No"}</td></tr>
              <tr><td class="text-muted">Adjuntos</td><td>${tech.attachments_present
                ? "⚠ Sí":"No"}</td></tr>
              <tr><td class="text-muted">Objetos PDF</td><td>${objCount}</td></tr>
              <tr class="${incUpdates?"table-warning":""}">
                <td class="text-muted">Act. incrementales</td>
                <td>${incUpdates ? `⚠ Sí (${incCount})` : "No"}</td>
              </tr>
              <tr><td class="text-muted">Modelo doc.</td><td><code>${docModel}</code></td></tr>
              <tr><td class="text-muted">Compresión</td><td><code>${comprList}</code></td></tr>
              <tr><td class="text-muted">DPI estimado (avg)</td><td>${dpiAvg}</td></tr>
              <tr><td class="text-muted">Categoría producer</td>
                  <td><span class="badge bg-${tech.producer_category==="generacion"?"danger":
                    tech.producer_category==="scanner"?"success":"secondary"}">
                    ${escHtml(tech.producer_category||"—")}</span></td></tr>
            </table>
          </div>
        </div>
      </div>

      <!-- ANÁLISIS DE CONTENIDO -->
      <div class="col-md-6">
        <div class="card mb-2">
          <div class="card-header fw-bold">
            <i class="bi bi-layers"></i> Contenido y capa de texto
          </div>
          <div class="card-body p-0">
            <table class="table table-sm mb-0 small">
              <tr><td class="text-muted">Texto real</td>
                  <td>${tech.text_objects_present?"✅ Sí":"❌ No"}</td></tr>
              <tr><td class="text-muted">Fuentes embebidas</td>
                  <td>${tech.embedded_fonts_present?"✅ Sí":"❌ No"}</td></tr>
              <tr><td class="text-muted">Vectores</td>
                  <td>${tech.vector_objects_present?"✅ Sí":"❌ No"}</td></tr>
              <tr><td class="text-muted">OCR invisible</td>
                  <td>${tech.invisible_ocr_present?"⚠ Sí":"No"}</td></tr>
              <tr><td class="text-muted">Caracteres totales</td>
                  <td>${tech.total_text_chars??txt.total_char_count??"—"}</td></tr>
            </table>
          </div>
        </div>
        <div class="card mb-2">
          <div class="card-header fw-bold">
            <i class="bi bi-activity"></i> Scores de análisis de imagen
          </div>
          <div class="card-body small">
            <div class="mb-1">Score escaneo físico: <strong>${scanScore}/100</strong>
              <div class="progress" style="height:6px">
                <div class="progress-bar bg-info" style="width:${scanScore}%"></div></div></div>
            <div class="mb-1">Score fabricación artificial: <strong>${artScore}/100</strong>
              <div class="progress" style="height:6px">
                <div class="progress-bar bg-danger" style="width:${artScore}%"></div></div></div>
            <div>Ruido fondo: ${tech.noise_level??scanner.noise_level??0}</div>
            <div>Sombra borde: ${tech.edge_shadow_detected||scanner.edge_shadow_detected?"Sí":"No"}</div>
            <div>Iluminación desigual: ${tech.illumination_uneven||scanner.illumination_uneven?"Sí":"No"}</div>
            <div>Fondo blanco perfecto: ${tech.perfect_white_bg||artif.perfect_white_bg?"⚠ Sí":"No"}</div>
            <div>Gaps histograma: ${(tech.histogram_gaps||arts.gaps_histograma||[]).length?
              (tech.histogram_gaps||arts.gaps_histograma).slice(0,5).join(", "):"Ninguno"}</div>
            <div>Puntos amarillos (MIC): <strong>${tech.yellow_dots_count||dots.puntos_detectados||0}</strong></div>
          </div>
        </div>
        <div class="card">
          <div class="card-header fw-bold">
            <i class="bi bi-shield-check"></i> Integridad
          </div>
          <div class="card-body small">
            <div class="text-muted">SHA-256</div>
            <div style="font-size:.65rem;word-break:break-all" class="mb-1">${sha256}</div>
            <div class="text-muted">MD5</div>
            <div style="font-size:.65rem;word-break:break-all">${md5}</div>
          </div>
        </div>
      </div>

      <!-- IMÁGENES POR PÁGINA -->
      ${imgRows ? `<div class="col-12">
        <div class="card">
          <div class="card-header fw-bold">
            <i class="bi bi-image"></i> Imágenes embebidas por página
          </div>
          <div class="card-body p-0">
            <table class="table table-sm mb-0 small">
              <thead><tr><th>Pág.</th><th>Imgs</th><th>Detalles</th></tr></thead>
              <tbody>${imgRows}</tbody>
            </table>
          </div>
        </div>
      </div>` : ""}

      <!-- FLAGS DE RIESGO -->
      <div class="col-md-6">
        <div class="card border-danger">
          <div class="card-header fw-bold text-danger">
            <i class="bi bi-flag-fill"></i> Flags de riesgo (${riskFlags.length})
          </div>
          <div class="card-body">
            <ul class="mb-0 ps-3">${flagHtml}</ul>
          </div>
        </div>
      </div>

      <!-- INDICADORES -->
      <div class="col-md-6">
        <div class="card">
          <div class="card-header fw-bold">
            <i class="bi bi-list-check"></i> Indicadores técnicos
          </div>
          <div class="card-body">
            ${favHtml ? `<p class="mb-1 fw-bold small text-success">A favor:</p>
              <ul class="mb-2 ps-3">${favHtml}</ul>` : ""}
            ${contraHtml ? `<p class="mb-1 fw-bold small text-warning">En contra:</p>
              <ul class="mb-0 ps-3">${contraHtml}</ul>` : ""}
          </div>
        </div>
      </div>

      <!-- PRUEBA ADICIONAL -->
      ${pruebaHtml ? `<div class="col-12">
        <div class="card border-secondary">
          <div class="card-header fw-bold text-secondary">
            <i class="bi bi-search"></i> Prueba adicional requerida
          </div>
          <div class="card-body">
            <ul class="mb-0 ps-3">${pruebaHtml}</ul>
          </div>
        </div>
      </div>` : ""}

      <!-- ADVERTENCIA LEGAL -->
      <div class="col-12">
        <div class="alert alert-secondary py-2 small">
          <i class="bi bi-info-circle"></i>
          ${escHtml(con.advertencia_legal || rpt.resumen || "")}
        </div>
      </div>
    </div>`;
}


// ── Comparación ───────────────────────────────────────────────────────────────

async function ejecutarComparacion() {
  const idEleccion = idEleccionFromSelect("selectActaComparacion");
  if (!idEleccion || !mesaActual) { toast("Selecciona un acta", "warning"); return; }
  setProcessing(true);
  document.getElementById("comparacionContainer").innerHTML =
    `<div class="spinner-border"></div>`;

  const r = await api("/api/comparar", "POST", {
    codigo_mesa: mesaActual, id_eleccion: idEleccion,
  });
  setProcessing(false);

  if (!r.ok) {
    document.getElementById("comparacionContainer").innerHTML =
      `<div class="alert alert-danger">${r.error}</div>`;
    return;
  }
  renderComparacion(r.data);
}

function renderComparacion(data) {
  const comp   = data.comparacion_api_ocr || {};
  const consist= data.consistencia_interna_api || {};
  const disc   = comp.discrepancias || [];
  const alertas= [...(comp.alertas || []), ...(consist.alertas || [])];

  const alertHtml = alertas.map(a =>
    `<div class="alert-forense ${a.nivel === "info" ? "info" : ""}">
      <i class="bi bi-exclamation-triangle-fill"></i> ${escHtml(a.mensaje)}
    </div>`
  ).join("") || `<div class="text-success mb-2"><i class="bi bi-check-circle"></i> Sin discrepancias detectadas</div>`;

  // Tabla comparación completa
  const allItems = [...(comp.coincidencias || []), ...disc];
  let filas = "";
  for (const it of allItems) {
    if ("partido_api" in it || "partido_ocr" in it) {
      const cls = it.coincide ? "ok-row" : "disc-row";
      const dif = it.diferencia !== undefined ? (it.diferencia > 0 ? "+" + it.diferencia : it.diferencia) : "";
      filas += `<tr class="${cls}">
        <td>${escHtml(it.partido_api || it.partido_ocr || "")}</td>
        <td class="text-end">${it.votos_api ?? "—"}</td>
        <td class="text-end">${it.votos_ocr ?? "—"}</td>
        <td class="text-end ${it.coincide ? "text-success" : "text-danger fw-bold"}">${dif || (it.coincide ? "✓" : "?")}</td>
      </tr>`;
    } else if ("campo" in it) {
      const cls = it.coincide ? "ok-row" : "disc-row";
      filas += `<tr class="${cls}">
        <td>${escHtml(it.campo)}</td>
        <td class="text-end">${it.valor_api ?? "—"}</td>
        <td class="text-end">${it.valor_ocr ?? "—"}</td>
        <td class="text-end ${it.coincide ? "text-success" : "text-danger fw-bold"}">${it.coincide ? "✓" : "≠"}</td>
      </tr>`;
    }
  }

  const consistBadge = consist.consistente
    ? `<span class="badge bg-success">Aritmética ✓</span>`
    : `<span class="badge bg-danger">Inconsistencia aritmética ⚠</span>`;

  document.getElementById("comparacionContainer").innerHTML = `
    <div class="mb-3">
      <span class="badge bg-${comp.estado === "ok" ? "success" : "danger"} me-2">
        Estado: ${comp.estado || "—"}
      </span>
      ${consistBadge}
    </div>
    ${alertHtml}
    <div class="card mt-2">
      <div class="card-header"><strong>Detalle comparación API ↔ OCR</strong></div>
      <div class="card-body p-0">
        <table class="table table-sm table-hover mb-0">
          <thead><tr>
            <th>Campo / Partido</th>
            <th class="text-end">API</th>
            <th class="text-end">OCR</th>
            <th class="text-end">Dif.</th>
          </tr></thead>
          <tbody>${filas || "<tr><td colspan='4' class='text-muted text-center p-3'>Sin datos suficientes</td></tr>"}</tbody>
        </table>
      </div>
    </div>
    ${comp.partidos_solo_api?.length ? `
    <div class="alert alert-warning mt-2 small">
      <b>Solo en API (no en OCR):</b> ${comp.partidos_solo_api.map(p=>escHtml(p.partido)).join(", ")}
    </div>` : ""}
    ${comp.partidos_solo_ocr?.length ? `
    <div class="alert alert-info mt-2 small">
      <b>Solo en OCR (no en API):</b> ${comp.partidos_solo_ocr.map(p=>escHtml(p.partido)).join(", ")}
    </div>` : ""}`;
}


// ── Helpers HTML ──────────────────────────────────────────────────────────────

function escHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

// ════════════════════════════════════════════════════════════════════════════
// ── BARRIDO AUTOMÁTICO ────────────────────────────────────────────────────
// ════════════════════════════════════════════════════════════════════════════

let _activeJobId   = null;
let _pollInterval  = null;

// ── Navegar a /proceso (nuevo diseño) ────────────────────────────────────────

function irAProcesar(modo) {
  const params = new URLSearchParams();

  if (modo === "rango") {
    const inicio  = document.getElementById("autoRangoInicio")?.value.trim().padStart(6,"0") || "000001";
    const fin     = document.getElementById("autoRangoFin")?.value.trim().padStart(6,"0")    || "001000";
    const iniNum  = parseInt(inicio.replace(/\D/g,"")) || 1;
    const finNum  = parseInt(fin.replace(/\D/g,""))    || 1000;

    if (iniNum > finNum) {
      toast("El rango de inicio debe ser ≤ al fin", "danger"); return;
    }
    if (finNum > 999999 || iniNum < 1) {
      toast("Rango fuera de límites (1–999999)", "danger"); return;
    }

    params.set("inicio",     inicio);
    params.set("fin",        fin);
    params.set("rate",       document.getElementById("autoDelay")?.value || "0.5");
    params.set("hilos",      document.getElementById("autoConcurrencia")?.value || "3");
    params.set("vacios",     document.getElementById("autoMaxVacios")?.value || "200");
    params.set("pdf",        document.getElementById("autoDescargarPdf")?.value  ? "1" : "0");
    params.set("ocr",        document.getElementById("autoEjecutarOcr")?.value   ? "1" : "0");
    params.set("forense",    document.getElementById("autoForense")?.value        ? "1" : "0");
    params.set("modo",       "rango");

  } else {
    // Consultar todas
    params.set("inicio",  "000001");
    params.set("fin",     "999999");
    params.set("rate",    document.getElementById("autoDelayTotal")?.value || "0.2");
    params.set("hilos",   document.getElementById("autoConcurrenciaTotal")?.value || "5");
    params.set("vacios",  document.getElementById("autoMaxVaciosTotal")?.value || "200");
    params.set("pdf",     "0");
    params.set("ocr",     "0");
    params.set("forense", "0");
    params.set("modo",    "todas");
  }

  window.location.href = "/proceso?" + params.toString();
}

function irADescargar(modo) {
  const params = new URLSearchParams();

  const inicio = (document.getElementById("dlRangoInicio")?.value || "000001").trim().padStart(6, "0");
  const fin    = (document.getElementById("dlRangoFin")?.value    || "001000").trim().padStart(6, "0");
  const carpeta = (document.getElementById("dlCarpeta")?.value    || "dataset").trim() || "dataset";

  if (modo === "rango") {
    const iniNum = parseInt(inicio.replace(/\D/g,"")) || 1;
    const finNum = parseInt(fin.replace(/\D/g,""))    || 1000;
    if (iniNum > finNum) { toast("El rango de inicio debe ser ≤ al fin", "danger"); return; }
    params.set("inicio", inicio);
    params.set("fin",    fin);
    params.set("modo",   "rango");
  } else {
    params.set("inicio", "000001");
    params.set("fin",    "999999");
    params.set("modo",   "todas");
  }

  params.set("rate",          document.getElementById("dlDelay")?.value     || "0.3");
  params.set("hilos",         "3");
  params.set("vacios",        document.getElementById("dlMaxVacios")?.value || "200");
  params.set("pdf",           "1");
  params.set("ocr",           "0");
  params.set("forense",       "0");
  params.set("solo_descarga", "1");
  params.set("carpeta",       carpeta);

  window.location.href = "/proceso?" + params.toString();
}


function _setBarridoRunning(running) {
  const btnIniciar   = document.getElementById("btnIniciarBarrido");
  const btnConsultar = document.getElementById("btnConsultarTodas");
  const btnsCtrl     = document.getElementById("btnsControlBarrido");

  if (running) {
    if (btnIniciar) {
      btnIniciar.disabled = true;
      btnIniciar.innerHTML = '<i class="bi bi-hourglass-split me-2"></i>EN EJECUCIÓN...';
    }
    if (btnConsultar) btnConsultar.disabled = true;
    btnsCtrl?.classList.remove("d-none");
    document.getElementById("cardProgreso")?.classList.remove("d-none");
  } else {
    if (btnIniciar) {
      btnIniciar.disabled = false;
      btnIniciar.innerHTML = '<i class="bi bi-play-circle-fill me-2" style="font-size:1.3rem"></i>PROCESAR ACTAS';
    }
    if (btnConsultar) btnConsultar.disabled = false;
    btnsCtrl?.classList.add("d-none");
  }
}

function iniciarBarrido() {
  const inicio      = parseInt(document.getElementById("autoRangoInicio").value) || 1;
  const fin         = parseInt(document.getElementById("autoRangoFin").value)    || 1000;
  const engine      = document.getElementById("autoOcrEngine").value;
  const conc        = parseInt(document.getElementById("autoConcurrencia").value) || 3;
  const delay       = parseFloat(document.getElementById("autoDelay")?.value) || 0.5;
  const maxVacios   = parseInt(document.getElementById("autoMaxVacios")?.value) || 200;
  const descargar   = document.getElementById("autoDescargarPdf").checked;
  const ejecutarOcr = document.getElementById("autoEjecutarOcr").checked;

  if (inicio > fin || inicio < 1 || fin > 999999) {
    toast("Rango inválido: inicio ≤ fin, entre 1 y 999999", "danger");
    return;
  }

  apiPost("/api/auto/barrido/iniciar", {
    rango_inicio: inicio, rango_fin: fin,
    ocr_engine: engine, concurrencia: conc,
    delay_segundos: delay,
    descargar_pdf: descargar, ejecutar_ocr: ejecutarOcr,
    max_vacios_consecutivos: maxVacios,
  }).then(r => {
    if (r.ok) {
      _activeJobId = r.data.job_id;
      toast(r.data.mensaje || "Barrido iniciado", "success");
      _setBarridoRunning(true);
      _pollInterval = setInterval(_pollBarrido, 2000);
    } else {
      toast(r.error || "Error al iniciar barrido", "danger");
    }
  });
}

function _pollBarrido() {
  if (!_activeJobId) return;
  apiGet(`/api/auto/barrido/estado/${_activeJobId}`).then(r => {
    if (!r.ok) return;
    const d = r.data;
    _actualizarUI(d);
    if (d.estado === "completado" || d.estado === "cancelado" || d.estado === "error") {
      clearInterval(_pollInterval);
      _pollInterval = null;
      _setBarridoRunning(false);
      const tipo = d.estado === "completado" ? "success" : "warning";
      toast(`Barrido ${d.estado}. ${d.progreso.toLocaleString()} mesas procesadas.`, tipo);
      cargarJobsHistorial();
    }
  });
}

function _actualizarUI(d) {
  const pct = d.porcentaje || 0;
  document.getElementById("progressBar").style.width    = pct + "%";
  document.getElementById("progressBar").textContent    = pct + "%";
  document.getElementById("progresoTexto").textContent  =
    `Mesa ${String(d.mesa_actual).padStart(6,"0")} | ${d.progreso.toLocaleString()} / ${d.total_mesas.toLocaleString()}`;
  if (d.eta_seg) {
    const m = Math.floor(d.eta_seg / 60), s = d.eta_seg % 60;
    document.getElementById("progresoETA").textContent = `ETA: ${m}m ${s}s`;
  }
  // Vacios consecutivos
  const vc = d.vacios_consecutivos || 0;
  const mvc = d.max_vacios_consecutivos || 0;
  const vcEl = document.getElementById("vaciosCounter");
  if (vcEl && mvc > 0) {
    vcEl.innerHTML = vc > 0
      ? `<i class="bi bi-hourglass text-warning"></i> Vacíos: <strong>${vc}</strong>/${mvc}`
      : ``;
  }
  const st = d.stats || {};
  document.getElementById("cntConPdf").textContent   = st.EXISTE_CON_PDF   || 0;
  document.getElementById("cntSinPdf").textContent   = st.EXISTE_SIN_PDF   || 0;
  document.getElementById("cntNoExiste").textContent = st.NO_EXISTE         || 0;
  document.getElementById("cntJee").textContent      = st.PARA_JEE          || 0;
  document.getElementById("cntConError").textContent = st.CON_ERROR_ACTA    || 0;
  document.getElementById("cntObservada").textContent= st.OBSERVADA         || 0;
  document.getElementById("cntErrorApi").textContent = st.ERROR_API         || 0;
}

function pausarBarrido() {
  if (!_activeJobId) return;
  apiPost(`/api/auto/barrido/pausar/${_activeJobId}`, {}).then(r => {
    if (r.ok) {
      clearInterval(_pollInterval); _pollInterval = null;
      document.getElementById("btnPausarBarrido").classList.add("d-none");
      document.getElementById("btnReanudarBarrido").classList.remove("d-none");
      toast("Barrido pausado", "info");
    }
  });
}

function reanudarBarrido() {
  if (!_activeJobId) return;
  apiPost(`/api/auto/barrido/reanudar/${_activeJobId}`, {}).then(r => {
    if (r.ok) {
      document.getElementById("btnReanudarBarrido").classList.add("d-none");
      document.getElementById("btnPausarBarrido").classList.remove("d-none");
      _pollInterval = setInterval(_pollBarrido, 2000);
      toast("Barrido reanudado", "info");
    }
  });
}

function cancelarBarrido() {
  if (!_activeJobId) return;
  if (!confirm("¿Cancelar el barrido en curso?")) return;
  apiPost(`/api/auto/barrido/cancelar/${_activeJobId}`, {}).then(r => {
    if (r.ok) {
      clearInterval(_pollInterval); _pollInterval = null;
      _activeJobId = null;
      _setBarridoRunning(false);
      toast("Barrido cancelado", "warning");
    }
  });
}

function consultarTodas() {
  const maxVacios = parseInt(document.getElementById("autoMaxVaciosTotal")?.value) || 200;
  if (!confirm(`¿Consultar TODAS las mesas (000001 → 999999)?\nSin descargar PDFs ni OCR.\nSe detendrá automáticamente si ${maxVacios} mesas consecutivas no tienen datos.`)) return;

  // Leer motor OCR configurado actualmente en el servidor
  apiGet("/api/auto/config").then(cfgR => {
    const ocrEng = cfgR.ok ? (cfgR.data.ocr_engine || "local") : "local";
    return apiPost("/api/auto/barrido/iniciar", {
      rango_inicio: 1,
      rango_fin: 999999,
      ocr_engine: ocrEng,
      concurrencia: 5,
      delay_segundos: 0.2,
      descargar_pdf: false,
      ejecutar_ocr: false,
      max_vacios_consecutivos: maxVacios,
    });
  }).then(r => {
    if (r.ok) {
      _activeJobId = r.data.job_id;
      toast(`Consulta total iniciada. Se detendrá tras ${maxVacios} vacíos consecutivos.`, "info");
      _setBarridoRunning(true);
      _pollInterval = setInterval(_pollBarrido, 2000);
    } else {
      toast(r.error || "Error al iniciar", "danger");
    }
  });
}

function cargarJobsHistorial() {
  apiGet("/api/auto/barrido/jobs").then(r => {
    if (!r.ok) return;
    const jobs = r.data || [];

    // ── Tabla historial completa (pestaña antigua, si existe) ──────────────
    const tbody = document.getElementById("tbodyJobs");
    if (tbody) {
      tbody.innerHTML = "";
      if (!jobs.length) {
        tbody.innerHTML = `<tr><td colspan="7" class="text-center text-muted p-3">Sin barridos registrados</td></tr>`;
      } else {
        jobs.forEach(j => {
          const stats  = j.stats || j.stats_json || {};
          const jee    = stats.PARA_JEE || 0;
          const enc    = (stats.EXISTE_CON_PDF || 0) + (stats.EXISTE_SIN_PDF || 0) + jee + (stats.CON_ERROR_ACTA || 0);
          const badgeColor = {ejecutando:"primary",completado:"success",pausado:"warning",cancelado:"secondary",error:"danger"}[j.estado] || "secondary";
          tbody.innerHTML += `<tr>
            <td class="font-monospace small" style="font-size:.75rem">${escHtml(j.job_id)}</td>
            <td>${j.rango_inicio?.toString().padStart(6,"0") || "?"} → ${j.rango_fin?.toString().padStart(6,"0") || "?"}</td>
            <td><span class="badge bg-${badgeColor}">${j.estado || "?"}</span></td>
            <td>${(j.progreso||0).toLocaleString()} / ${(j.total_mesas||j.rango_fin-j.rango_inicio+1).toLocaleString()}</td>
            <td>${enc.toLocaleString()}</td>
            <td><span class="${jee>0?"text-warning fw-bold":""}">${jee}</span></td>
            <td class="small text-muted">${(j.iniciado_en||"").replace("T"," ").substring(0,16)}</td>
          </tr>`;
        });
      }
    }

    // ── Mini-panel en col-masivo ───────────────────────────────────────────
    const mini = document.getElementById("jobsMiniList");
    if (mini) {
      if (!jobs.length) {
        mini.innerHTML = `<em>Sin jobs recientes</em>`;
      } else {
        const colorMap = {ejecutando:"primary",completado:"success",pausado:"warning",cancelado:"secondary",error:"danger"};
        mini.innerHTML = jobs.slice(0, 5).map(j => {
          const bc = colorMap[j.estado] || "secondary";
          const pct = j.total_mesas > 0 ? Math.round((j.progreso / j.total_mesas) * 100) : 0;
          const rango = `${(j.rango_inicio||1).toString().padStart(6,"0")}→${(j.rango_fin||1).toString().padStart(6,"0")}`;
          return `<div class="d-flex align-items-center gap-2 py-1 border-bottom">
            <span class="badge bg-${bc}" style="min-width:80px">${j.estado}</span>
            <code class="small">${rango}</code>
            <span class="ms-auto small text-muted">${pct}%</span>
          </div>`;
        }).join("");
      }
    }
  });
}

// ════════════════════════════════════════════════════════════════════════════
// ── ANOMALÍAS ─────────────────────────────────────────────────────────────
// ════════════════════════════════════════════════════════════════════════════

const _charts = {};

function cargarChartsAnomalias() {
  _cargarChart("chartScores",         "/api/charts/scores_histogram");
  _cargarChart("chartAnomaliasTemporal", "/api/charts/anomalias_timeline");
  _cargarChart("chartEstados",        "/api/charts/distribucion_estados");
  _cargarChart("chartTiposEleccion",  "/api/charts/mesas_por_tipo_eleccion");
  cargarAnomaliaStats();
}

function _cargarChart(canvasId, url) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  apiGet(url).then(r => {
    if (!r.ok) return;
    const d = r.data;
    if (_charts[canvasId]) { _charts[canvasId].destroy(); }
    const tipo = d.tipo === "horizontalBar" ? "bar" : (d.tipo || "bar");
    _charts[canvasId] = new Chart(canvas, {
      type: tipo,
      data: {
        labels: d.labels || [],
        datasets: [{
          label: d.titulo || "",
          data: d.data || [],
          backgroundColor: d.bg_colors || "rgba(54,162,235,0.6)",
          borderColor: d.bg_colors || "rgba(54,162,235,1)",
          borderWidth: 1,
          indexAxis: d.tipo === "horizontalBar" ? "y" : "x",
        }]
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false }, title: { display: false } },
        scales: tipo !== "doughnut" ? { y: { beginAtZero: true } } : {},
      }
    });
  });
}

function cargarAnomaliaStats() {
  apiGet("/api/anomalias/stats").then(r => {
    if (!r.ok) return;
    const d = r.data;
    const card = document.getElementById("anomStatsCard");
    if (!card) return;
    card.innerHTML = `
      <div class="row text-center g-2">
        <div class="col-6"><div class="border rounded p-2"><div class="fw-bold text-danger fs-4">${d.total_anomalias}</div><div class="small text-muted">Anomalías</div></div></div>
        <div class="col-6"><div class="border rounded p-2"><div class="fw-bold fs-4">${d.total_analizadas}</div><div class="small text-muted">Analizadas</div></div></div>
        <div class="col-4"><div class="p-1 text-center"><div class="fw-bold text-danger">${d.por_confianza?.alta||0}</div><div class="small text-muted">Alta conf.</div></div></div>
        <div class="col-4"><div class="p-1 text-center"><div class="fw-bold text-warning">${d.por_confianza?.media||0}</div><div class="small text-muted">Media</div></div></div>
        <div class="col-4"><div class="p-1 text-center"><div class="fw-bold text-secondary">${d.por_confianza?.baja||0}</div><div class="small text-muted">Baja</div></div></div>
      </div>
      <div class="mt-2 text-center small text-muted">${d.pct_anomalias}% del total analizado</div>
    `;
  });
}

function entrenarModelo() {
  const nu = parseFloat(document.getElementById("svmNu").value) || 0.05;
  const st = document.getElementById("entrenarStatus");
  st.innerHTML = `<span class="text-muted"><i class="bi bi-hourglass-split"></i> Entrenando...</span>`;
  apiPost("/api/anomalias/entrenar", { nu }).then(r => {
    if (r.ok) {
      const d = r.data;
      st.innerHTML = `<span class="text-success">✓ Modelo entrenado. Muestras: ${d.n_muestras || 0}, ν=${nu}</span>`;
      toast(`Modelo OCSVM entrenado con ${d.n_muestras || 0} muestras.`, "success");
    } else {
      st.innerHTML = `<span class="text-danger">✗ ${escHtml(r.error || "Error")}</span>`;
      toast(r.error || "Error al entrenar", "danger");
    }
  });
}

function analizarMesaAnom() {
  const mesa    = document.getElementById("anomMesa").value.padStart(6,"0");
  const elec    = parseInt(document.getElementById("anomEleccion").value);
  const div     = document.getElementById("anomResultado");
  div.innerHTML = `<span class="text-muted small"><i class="bi bi-hourglass-split"></i> Analizando...</span>`;
  apiPost("/api/anomalias/analizar", { codigo_mesa: mesa, id_eleccion: elec }).then(r => {
    if (!r.ok) { div.innerHTML = `<span class="text-danger small">✗ ${escHtml(r.error)}</span>`; return; }
    const d = r.data;
    const color = d.is_anomaly ? "danger" : "success";
    const icon  = d.is_anomaly ? "bi-exclamation-triangle-fill" : "bi-check-circle-fill";
    const exp   = (d.explanation || []).slice(0,3).map(e => `<li>${escHtml(e)}</li>`).join("");
    div.innerHTML = `
      <div class="alert alert-${color} py-2 mt-2 small">
        <i class="bi ${icon}"></i> <strong>${d.is_anomaly ? "ANÓMALA" : "Normal"}</strong>
        — Score: <code>${(d.score||0).toFixed(4)}</code>
        — Conf: <em>${d.confidence||"?"}</em>
        ${exp ? `<ul class="mb-0 mt-1">${exp}</ul>` : ""}
        ${!d.modelo_disponible ? '<div class="text-muted">Modelo no entrenado aún.</div>' : ""}
      </div>`;
  });
}

function analizarTodasLasMesas() {
  if (!confirm("¿Analizar TODAS las actas en DB con OCSVM? Puede tardar varios minutos.")) return;
  toast("Iniciando análisis masivo...", "info");
  apiPost("/api/anomalias/analizar_todas", {}).then(r => {
    if (r.ok) {
      toast(`Analizadas: ${r.data.total_analizadas}. Anomalías: ${r.data.n_anomalias}`, "success");
      cargarListaAnomalias();
      cargarChartsAnomalias();
    } else {
      toast(r.error || "Error en análisis masivo", "danger");
    }
  });
}

function cargarListaAnomalias() {
  apiGet("/api/anomalias/lista?solo_positivas=1&limit=200").then(r => {
    if (!r.ok) return;
    const tbody = document.getElementById("tbodyAnomalias");
    if (!tbody) return;
    tbody.innerHTML = "";
    const rows = r.data.anomalias || [];
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="text-center text-muted p-3">Sin anomalías detectadas</td></tr>`;
      return;
    }
    const elecNombre = {10:"Presidencial",11:"Senadores DEU",12:"Senadores DEM",13:"Diputados",14:"Parl. Andino"};
    rows.forEach(row => {
      const confColor = {alta:"danger",media:"warning",baja:"secondary"}[row.confidence] || "secondary";
      const exp = (row.explanation_json || []).slice(0,2).join(" | ");
      tbody.innerHTML += `<tr>
        <td class="font-monospace">${escHtml(row.codigo_mesa)}</td>
        <td>${elecNombre[row.id_eleccion] || row.id_eleccion}</td>
        <td><code class="text-danger">${(row.score||0).toFixed(4)}</code></td>
        <td><span class="badge bg-${confColor}">${row.confidence||"?"}</span></td>
        <td class="small text-muted">${escHtml(exp)}</td>
      </tr>`;
    });
  });
}

// ════════════════════════════════════════════════════════════════════════════
// ── CONFIGURACIÓN ─────────────────────────────────────────────────────────
// ════════════════════════════════════════════════════════════════════════════

function cargarConfig() {
  apiGet("/api/auto/config").then(r => {
    if (!r.ok) return;
    const d = r.data;
    // Seleccionar radio de OCR engine
    const radios = document.querySelectorAll("input[name='ocrEngine']");
    radios.forEach(radio => { radio.checked = (radio.value === d.ocr_engine); });
    // Parámetros de barrido
    if (d.barrido) {
      document.getElementById("cfgConcurrencia").value = d.barrido.concurrencia;
      document.getElementById("cfgConcVal").textContent = d.barrido.concurrencia;
      document.getElementById("cfgDelay").value         = d.barrido.delay_seg;
      document.getElementById("cfgDelayVal").textContent = d.barrido.delay_seg;
    }
    // Modo comparación
    const modo = d.comparacion_modo || "simple";
    window._comparacionModo = modo;
    document.querySelectorAll("input[name='comparacionModo']").forEach(r => {
      r.checked = (r.value === modo);
    });
    _toggleGeminiSection(modo === "hibrido_ia");
    // Estado clave Gemini
    const geminiSt = document.getElementById("geminiApiStatus");
    if (geminiSt) {
      geminiSt.textContent  = d.gemini_configured ? "✓ Configurada" : "Sin configurar";
      geminiSt.className    = `badge bg-${d.gemini_configured ? "success" : "secondary"} small`;
    }
    // Mostrar info del sistema
    const infoDiv = document.getElementById("sistemaCfgInfo");
    const docai   = d.google_docai || {};
    infoDiv.innerHTML = `
      <div class="row g-2 small">
        <div class="col-md-3"><strong>OCR activo:</strong> <span class="badge bg-primary">${d.ocr_engine||"local"}</span></div>
        <div class="col-md-3"><strong>Google DocAI:</strong> <span class="badge bg-${docai.configurado?"success":"secondary"}">${docai.configurado?"Configurado":"Sin configurar"}</span></div>
        <div class="col-md-3"><strong>Hilos barrido:</strong> ${d.barrido?.concurrencia||3}</div>
        <div class="col-md-3"><strong>OCSVM ν:</strong> ${d.ocsvm?.nu||0.05}</div>
        <div class="col-md-3"><strong>Comparación:</strong> <span class="badge bg-info text-dark">${modo}</span></div>
        <div class="col-md-3"><strong>Gemini:</strong> <span class="badge bg-${d.gemini_configured?"success":"secondary"}">${d.gemini_configured?"Configurado":"Sin configurar"}</span></div>
        ${docai.mensaje ? `<div class="col-12 text-muted">${escHtml(docai.mensaje)}</div>` : ""}
      </div>`;
  });
}

function guardarConfigOcr() {
  const engine = document.querySelector("input[name='ocrEngine']:checked")?.value || "local";
  apiPost("/api/auto/config", { ocr_engine: engine }).then(r => {
    const st = document.getElementById("configOcrStatus");
    if (r.ok) {
      st.innerHTML = `<span class="text-success">✓ Motor "${engine}" aplicado</span>`;
      toast(`Motor OCR cambiado a "${engine}"`, "success");
    } else {
      st.innerHTML = `<span class="text-danger">✗ ${escHtml(r.error)}</span>`;
    }
  });
}

function guardarConfigGoogle() {
  const projectId   = document.getElementById("gProjectId")?.value.trim() || "";
  const processorId = document.getElementById("gProcessorId")?.value.trim() || "";
  const location    = document.getElementById("gLocation")?.value || "us";

  // Nunca se envían credenciales — las resuelve el backend por ADC o GOOGLE_APPLICATION_CREDENTIALS
  const body = {
    google_project_id:   projectId,
    google_location:     location,
    google_processor_id: processorId,
  };

  apiPost("/api/auto/config", body).then(r => {
    const st = document.getElementById("googleTestResult");
    if (r.ok) {
      st.innerHTML = `<span class="text-success">✓ Configuración guardada</span>`;
      // Refrescar estado de autenticación tras guardar
      cargarEstadoAuth();
    } else {
      st.innerHTML = `<span class="text-danger">✗ ${escHtml(r.error)}</span>`;
    }
  });
}

function _toggleGeminiSection(visible) {
  const sec = document.getElementById("geminiKeySection");
  if (sec) sec.classList.toggle("d-none", !visible);
}

function toggleGeminiKeyVis(btn) {
  const inp = document.getElementById("geminiApiKey");
  if (!inp) return;
  const isPass = inp.type === "password";
  inp.type = isPass ? "text" : "password";
  btn.innerHTML = isPass ? '<i class="bi bi-eye-slash"></i>' : '<i class="bi bi-eye"></i>';
}

function guardarConfigComparacion() {
  const modo    = document.querySelector("input[name='comparacionModo']:checked")?.value || "simple";
  const apiKey  = document.getElementById("geminiApiKey")?.value.trim() || "";
  const body    = { comparacion_modo: modo };
  if (apiKey) body.gemini_api_key = apiKey;

  _toggleGeminiSection(modo === "hibrido_ia");

  apiPost("/api/auto/config", body).then(r => {
    const st = document.getElementById("configCmpStatus");
    if (r.ok) {
      window._comparacionModo = modo;
      st.innerHTML = `<span class="text-success">✓ Modo "${modo}" aplicado</span>`;
      toast(`Modo comparación: ${modo}`, "success");
      cargarConfig();   // actualiza la sección Estado del sistema
    } else {
      st.innerHTML = `<span class="text-danger">✗ ${escHtml(r.error)}</span>`;
    }
  });
}

// Vincular radios de comparación al toggle de la sección Gemini
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("input[name='comparacionModo']").forEach(r => {
    r.addEventListener("change", () => _toggleGeminiSection(r.value === "hibrido_ia" && r.checked));
  });
});

/**
 * Carga el diagnóstico completo de autenticación de Google Cloud desde el backend.
 * Rellena el panel #authStatusPanel con badges de estado y pasos sugeridos.
 */
function cargarEstadoAuth() {
  const panel = document.getElementById("authStatusPanel");
  const icon  = document.getElementById("authRefreshIcon");
  if (!panel) return;

  panel.innerHTML = `<div class="text-muted small text-center py-1"><i class="bi bi-hourglass-split"></i> Verificando…</div>`;
  if (icon) icon.classList.add("rotate-spin");

  apiGet("/api/google/auth_status").then(r => {
    if (icon) icon.classList.remove("rotate-spin");
    if (!r.ok) {
      panel.innerHTML = `<div class="text-danger small">✗ Error al obtener estado: ${escHtml(r.error || "")}</div>`;
      return;
    }
    const d = r.data;

    // Función helper para badge de estado
    const badge = (ok, labelOk, labelFail) =>
      ok ? `<span class="badge bg-success">${labelOk}</span>`
         : `<span class="badge bg-danger">${labelFail}</span>`;

    // Modo ADC → etiqueta legible
    const modoLabel = {
      "usuario_adc":                  "ADC usuario (gcloud)",
      "service_account_json":         "Cuenta de servicio (JSON)",
      "service_account_impersonation":"SA impersonation",
      "compute_engine":               "Compute Engine",
      "no_configurado":               "No configurado",
      "desconocido":                  "Desconocido",
    }[d.adc_modo] || d.adc_modo;

    let html = `<table class="table table-sm table-borderless mb-0" style="font-size:.75rem">
      <tbody>
        <tr><td class="text-muted pe-2">SDK instalado</td><td>${badge(d.sdk_instalado,"✓ sí","✗ no")}</td></tr>
        <tr><td class="text-muted pe-2">gcloud CLI</td><td>${badge(d.gcloud_cli_disponible,"✓ instalada","✗ no encontrada")}
          ${d.gcloud_account ? `<span class="text-muted ms-1">${escHtml(d.gcloud_account)}</span>` : ""}</td></tr>
        <tr><td class="text-muted pe-2">ADC configurado</td><td>${badge(d.adc_configurado,"✓ sí","✗ no")}</td></tr>
        <tr><td class="text-muted pe-2">Modo auth</td><td><span class="badge ${d.adc_configurado ? "bg-info text-dark" : "bg-secondary"}">${escHtml(modoLabel)}</span>
          ${d.adc_email ? `<span class="text-muted ms-1" style="font-size:.7rem">${escHtml(d.adc_email)}</span>` : ""}</td></tr>
        <tr><td class="text-muted pe-2">Project ID</td><td>${badge(d.project_id_configurado,"✓ configurado","✗ falta")}
          ${d.project_id ? `<span class="text-muted ms-1 font-monospace" style="font-size:.7rem">${escHtml(d.project_id)}</span>` : ""}</td></tr>
        <tr><td class="text-muted pe-2">Processor ID</td><td>${badge(d.processor_id_configurado,"✓ configurado","✗ falta")}
          ${d.processor_id ? `<span class="text-muted ms-1 font-monospace" style="font-size:.7rem">${escHtml(d.processor_id)}</span>` : ""}</td></tr>
      </tbody>
    </table>`;

    if (d.pasos_sugeridos && d.pasos_sugeridos.length > 0) {
      html += `<div class="mt-2 p-2 rounded border border-warning" style="background:#1a1a2e; font-size:.73rem">
        <div class="text-warning fw-bold mb-1"><i class="bi bi-list-check"></i> Pasos pendientes (${d.pasos_sugeridos.length})</div>
        <ol class="ps-3 mb-0" style="color:#c9d1d9">`;
      d.pasos_sugeridos.forEach(p => {
        html += `<li class="mb-1">${escHtml(p)}</li>`;
      });
      html += `</ol></div>`;
    } else if (d.listo_para_usar) {
      html += `<div class="mt-1 text-success small"><i class="bi bi-check-circle-fill"></i> Listo para usar Document AI</div>`;
    }

    if (d.error) {
      html += `<div class="mt-1 text-danger small"><i class="bi bi-exclamation-triangle"></i> ${escHtml(d.error)}</div>`;
    }

    panel.innerHTML = html;

    // Rellenar los campos si ya hay valores configurados
    if (d.project_id && document.getElementById("gProjectId"))
      document.getElementById("gProjectId").value = d.project_id;
    if (d.processor_id && document.getElementById("gProcessorId"))
      document.getElementById("gProcessorId").value = d.processor_id;
    if (d.location && document.getElementById("gLocation"))
      document.getElementById("gLocation").value = d.location;
  });
}

function testGoogleOcr() {
  const st = document.getElementById("googleTestResult");
  st.innerHTML = `<span class="text-muted"><i class="bi bi-hourglass-split"></i> Guardando configuración…</span>`;

  // Guardar Project ID / Processor ID / Location antes de probar
  const body = {
    google_project_id:   document.getElementById("gProjectId")?.value.trim() || "",
    google_processor_id: document.getElementById("gProcessorId")?.value.trim() || "",
    google_location:     document.getElementById("gLocation")?.value || "us",
  };
  apiPost("/api/auto/config", body).then(saveR => {
    if (!saveR.ok) {
      st.innerHTML = `<span class="text-danger">✗ Error al guardar: ${escHtml(saveR.error)}</span>`;
      return;
    }
    st.innerHTML = `<span class="text-muted"><i class="bi bi-hourglass-split"></i> Probando conexión con Document AI…</span>`;
    apiGet("/api/auto/google_ocr/test").then(r => {
      if (r.ok && r.data.ok) {
        st.innerHTML = `<span class="text-success"><i class="bi bi-check-circle-fill"></i> Conexión exitosa — ${r.data.n_procesadores || 0} procesador(es)</span>`;
        cargarEstadoAuth();  // refrescar panel
      } else {
        const msg = r.data?.error || r.error || "Error de conexión";
        st.innerHTML = `<span class="text-danger"><i class="bi bi-x-circle"></i> ${escHtml(msg)}</span>`;
      }
    });
  });
}

function estimarCosto() {
  const mesas = parseInt(document.getElementById("estMesas").value) || 170000;
  const actas = parseFloat(document.getElementById("estActas").value) || 4.2;
  apiGet(`/api/auto/google_ocr/estimate?n_mesas=${mesas}&actas_por_mesa=${actas}`).then(r => {
    if (!r.ok) return;
    const d = r.data;
    document.getElementById("costoResultado").innerHTML = `
      <table class="table table-sm table-bordered small mb-0">
        <tr><td>Mesas</td><td class="fw-bold text-end">${(d.n_mesas||0).toLocaleString()}</td></tr>
        <tr><td>Páginas estimadas</td><td class="fw-bold text-end">${(d.paginas_totales||0).toLocaleString()}</td></tr>
        <tr><td>Tarifa</td><td class="text-end">USD ${d.tarifa_usd_por_1000||"1.50"} / 1000 págs</td></tr>
        <tr class="table-warning"><td><strong>Costo total estimado</strong></td><td class="text-end fw-bold text-danger">USD ${(d.costo_total_usd||0).toFixed(2)}</td></tr>
      </table>`;
  });
}

function guardarConfigBarrido() {
  const conc  = parseInt(document.getElementById("cfgConcurrencia").value) || 3;
  const delay = parseFloat(document.getElementById("cfgDelay").value) || 0.5;
  apiPost("/api/auto/config", { barrido_concurrencia: conc, barrido_delay_seg: delay }).then(r => {
    const st = document.getElementById("configBarridoStatus");
    if (r.ok) st.innerHTML = `<span class="text-success">✓ Parámetros guardados</span>`;
    else      st.innerHTML = `<span class="text-danger">✗ ${escHtml(r.error)}</span>`;
  });
}

// ── Helpers API ───────────────────────────────────────────────────────────────

async function apiGet(url) {
  try {
    const res = await fetch(url);
    const json = await res.json();
    return json;
  } catch(e) {
    return { ok: false, error: e.message };
  }
}

async function apiPost(url, body) {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await res.json();
  } catch(e) {
    return { ok: false, error: e.message };
  }
}

// ── Banner de donación ────────────────────────────────────────────────────────

function cerrarBanner() {
  const b = document.getElementById("donaBanner");
  if (b) { b.classList.remove("visible"); }
  sessionStorage.setItem("donaVisto", "1");
}

document.addEventListener("DOMContentLoaded", () => {
  refreshStats();
  cargarConfig();  // Inicializar UI con la configuración guardada en servidor

  // Cargar estado de autenticación de Google al abrir el modal de configuración
  const modalConfigEl = document.getElementById("modalConfig");
  if (modalConfigEl) {
    modalConfigEl.addEventListener("show.bs.modal", () => {
      cargarEstadoAuth();
      cargarConfig();  // Sincronizar radios de OCR al abrir modal
    });
  }

  // Mostrar banner de donación tras 4 segundos (solo una vez por sesión)
  if (!sessionStorage.getItem("donaVisto")) {
    setTimeout(() => {
      document.getElementById("donaBanner")?.classList.add("visible");
    }, 4000);
  }
});
