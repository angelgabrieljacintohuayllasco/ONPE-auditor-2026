/* ═══════════════════════════════════════════════════════════════════════════
   resultados.js — Dashboard de resultados masivos
   ═══════════════════════════════════════════════════════════════════════════ */

// ── Estado global ─────────────────────────────────────────────────────────────
let currentPage   = 1;
let totalPages    = 1;
let currentActa   = null;
let currentTab    = "pdf";
let debounceTimer = null;
let charts        = {};
let offcanvasInst = null;
let pdfState      = { mesa: null, eleccion: null, page: 1, total: 1 };

// ── Cargar tabla ──────────────────────────────────────────────────────────────

async function cargarResultados(page) {
  if (page != null) currentPage = page;

  const mesa   = document.getElementById("filtroBuscar").value.trim();
  const filtro = document.getElementById("filtroTipo").value;
  const sort   = document.getElementById("filtroSort").value;
  const limit  = parseInt(document.getElementById("limitSelect").value);

  setTablaLoading(true);

  try {
    const params = new URLSearchParams({ page: currentPage, limit, filtro, sort });
    if (mesa) params.set("mesa", mesa);

    const res  = await fetch(`/api/resultados/actas?${params}`);
    const json = await res.json();

    if (!json.ok) throw new Error(json.error || "Error en el servidor");

    const d = json.data;
    totalPages = d.pages;

    document.getElementById("resultCountLabel").textContent =
      `${(d.total || 0).toLocaleString()} acta${d.total !== 1 ? "s" : ""}`;

    renderTabla(d.actas || []);
    renderPaginacion(d.total || 0, currentPage, limit);
    renderStatsStrip(d.global_stats || {});

  } catch (e) {
    document.getElementById("tablaBody").innerHTML =
      `<tr><td colspan="9" class="text-center text-danger py-4">
         <i class="bi bi-exclamation-triangle me-2"></i>${escHtml(e.message)}
       </td></tr>`;
    toast("Error: " + e.message, "err");
  }
}

function setTablaLoading(on) {
  if (on) {
    document.getElementById("tablaBody").innerHTML =
      `<tr><td colspan="9" class="text-center text-muted py-5">
         <div class="spinner-border spinner-border-sm text-secondary me-2"></div>Cargando...
       </td></tr>`;
  }
}

// ── Render tabla ──────────────────────────────────────────────────────────────

function renderTabla(actas) {
  if (!actas.length) {
    document.getElementById("tablaBody").innerHTML =
      `<tr><td colspan="9" class="text-center text-muted py-5">
         <i class="bi bi-inbox display-6 d-block mb-2"></i>
         Sin resultados para este filtro
       </td></tr>`;
    return;
  }

  const rows = actas.map(a => {
    const tieneAnom = a.anomalia_forense === 1 || a.is_anomaly_svm === 1;
    const discCount = Array.isArray(a.discrepancias_json) ? a.discrepancias_json.length : 0;
    const alertasN  = Array.isArray(a.alertas_forense)
      ? a.alertas_forense.filter(x => x.nivel === "alerta").length : 0;

    // Badge tipo documento
    let badgeDoc;
    if (!a.tiene_ocr) {
      badgeDoc = `<span class="badge badge-sin"><i class="bi bi-dash"></i> Sin OCR</span>`;
    } else if (a.es_escaneado === 1) {
      badgeDoc = `<span class="badge badge-escaneado"><i class="bi bi-camera-fill"></i> Escaneado</span>`;
    } else {
      badgeDoc = `<span class="badge badge-digital"><i class="bi bi-file-earmark-text-fill"></i> Digital</span>`;
    }

    // Badge OCR
    const badgeOcr = a.tiene_ocr
      ? `<span class="badge badge-ok"><i class="bi bi-check2"></i> ${escHtml(a.metodo_ocr || "ok")}</span>`
      : `<span class="badge badge-sin">—</span>`;

    // Badge forense
    let badgeForense;
    if (!a.tiene_forense) {
      badgeForense = `<span class="badge badge-sin">—</span>`;
    } else if (a.anomalia_forense) {
      badgeForense = `<span class="badge badge-alerta"><i class="bi bi-exclamation-triangle-fill"></i> ${alertasN} alerta${alertasN !== 1 ? "s" : ""}</span>`;
    } else {
      badgeForense = `<span class="badge badge-ok"><i class="bi bi-shield-check"></i> Normal</span>`;
    }

    // Badge anomalía
    let badgeAnom;
    if (tieneAnom) {
      badgeAnom = `<span class="badge badge-alerta badge-anomaly-anim"><i class="bi bi-radioactive"></i> ANOMALÍA</span>`;
    } else if (a.anomalia_forense === 0 || a.is_anomaly_svm === 0) {
      badgeAnom = `<span class="badge badge-ok"><i class="bi bi-check-circle"></i> Normal</span>`;
    } else {
      badgeAnom = `<span class="badge badge-sin">—</span>`;
    }

    // Badge discrepancias
    const badgeDisc = discCount > 0
      ? `<span class="badge badge-escaneado fw-bold">${discCount}</span>`
      : `<span class="text-muted small">—</span>`;

    const badgeEstado = a.estado
      ? `<span class="badge badge-sin" style="font-size:.65rem">${escHtml(a.estado)}</span>`
      : `<span class="text-muted">—</span>`;

    const rowCls = tieneAnom ? "row-anomaly" : (discCount > 0 ? "row-warning" : "");

    // Serializar datos para onclick (evitar XSS con JSON safe)
    const safeId = `${a.codigo_mesa}-${a.id_eleccion}`;

    return `<tr class="${rowCls}" id="row-${safeId}" data-mesa="${escHtml(a.codigo_mesa)}" data-eleccion="${a.id_eleccion}">
      <td class="font-monospace fw-bold text-info">${escHtml(a.codigo_mesa)}</td>
      <td class="small text-muted">${escHtml(a.tipo_eleccion || ("E" + a.id_eleccion))}</td>
      <td>${badgeEstado}</td>
      <td>${badgeDoc}</td>
      <td>${badgeOcr}</td>
      <td>${badgeForense}</td>
      <td>${badgeAnom}</td>
      <td class="text-center">${badgeDisc}</td>
      <td class="text-end">
        ${a.tiene_pdf
          ? `<button class="btn btn-xs btn-outline-primary me-1"
                     onclick="abrirDetalle('${escHtml(a.codigo_mesa)}',${a.id_eleccion},'pdf')"
                     title="Ver PDF">
               <i class="bi bi-file-pdf"></i>
             </button>`
          : ""}
        <button class="btn btn-xs btn-outline-info"
                onclick="abrirDetalle('${escHtml(a.codigo_mesa)}',${a.id_eleccion},'ocr')"
                title="Ver detalle">
          <i class="bi bi-info-circle"></i>
        </button>
      </td>
    </tr>`;
  });

  document.getElementById("tablaBody").innerHTML = rows.join("");

  // Guardar actas en caché indexadas para acceso rápido
  window._actasCache = {};
  actas.forEach(a => { window._actasCache[`${a.codigo_mesa}-${a.id_eleccion}`] = a; });
}

// ── Paginación ────────────────────────────────────────────────────────────────

function renderPaginacion(total, page, limit) {
  const pages = Math.max(1, Math.ceil(total / limit));
  totalPages  = pages;
  const el    = document.getElementById("paginacion");
  if (pages <= 1) { el.innerHTML = ""; return; }

  const btns = [];
  btns.push(makePagBtn("<i class='bi bi-chevron-double-left'></i>", 1,      page === 1));
  btns.push(makePagBtn("<i class='bi bi-chevron-left'></i>",         page-1, page === 1));

  for (const p of pageRange(page, pages)) {
    if (p === "…") {
      btns.push(`<button class="btn btn-sm btn-dark border-0 disabled">…</button>`);
    } else {
      btns.push(`<button class="btn btn-sm ${p === page ? "btn-info" : "btn-outline-secondary"}"
                         onclick="cargarResultados(${p})">${p}</button>`);
    }
  }

  btns.push(makePagBtn("<i class='bi bi-chevron-right'></i>",         page+1, page === pages));
  btns.push(makePagBtn("<i class='bi bi-chevron-double-right'></i>",  pages,  page === pages));
  el.innerHTML = btns.join("");
}

function makePagBtn(label, target, disabled) {
  return `<button class="btn btn-sm btn-outline-secondary${disabled ? " disabled" : ""}"
                  onclick="cargarResultados(${target})">${label}</button>`;
}

function pageRange(cur, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const r = [1];
  if (cur > 3) r.push("…");
  for (let i = Math.max(2, cur - 1); i <= Math.min(total - 1, cur + 1); i++) r.push(i);
  if (cur < total - 2) r.push("…");
  r.push(total);
  return r;
}

// ── Stats strip ───────────────────────────────────────────────────────────────

function renderStatsStrip(gs) {
  const chips = [
    { label: "Total actas",    val: gs.total_actas   ?? "—", icon: "bi-table",               color: "#58a6ff" },
    { label: "Con PDF",        val: gs.con_pdf        ?? "—", icon: "bi-file-pdf-fill",        color: "#6ea8fe" },
    { label: "Con OCR",        val: gs.con_ocr        ?? "—", icon: "bi-text-paragraph",       color: "#56d364" },
    { label: "Digitales",      val: gs.digitales      ?? "—", icon: "bi-file-earmark-text",    color: "#79c0ff" },
    { label: "Escaneados",     val: gs.escaneados     ?? "—", icon: "bi-camera-fill",          color: "#e3b341" },
    { label: "Con anomalía",   val: gs.con_anomalia   ?? "—", icon: "bi-radioactive",          color: "#f85149" },
    { label: "Discrepancias",  val: gs.con_discrepancia ?? "—", icon: "bi-exclamation-triangle-fill", color: "#ffa657" },
  ];

  document.getElementById("statsStrip").innerHTML = chips.map(c => `
    <div class="stat-chip">
      <i class="bi ${c.icon}" style="font-size:1.4rem;color:${c.color}"></i>
      <div>
        <div class="val" style="color:${c.color}">${Number.isNaN(+c.val) ? c.val : (+c.val).toLocaleString()}</div>
        <div class="lbl">${c.label}</div>
      </div>
    </div>`).join("");
}

// ── Offcanvas detalle ─────────────────────────────────────────────────────────

function abrirDetalle(mesa, eleccion, tab) {
  const acta = (window._actasCache || {})[`${mesa}-${eleccion}`];
  if (!acta) { toast("Datos no disponibles en caché", "err"); return; }
  currentActa = acta;

  document.getElementById("offcanvasTitulo").textContent = `Acta Mesa ${mesa}`;
  document.getElementById("offcanvasSubtitulo").textContent =
    `${acta.tipo_eleccion || ("Elección " + eleccion)} · ${acta.estado || ""}`;

  // Activar tab correcto
  document.querySelectorAll("#detalleTabs .nav-link").forEach(el => el.classList.remove("active"));
  const tabLink = document.querySelector(`#detalleTabs .nav-link[onclick*="'${tab}'"]`);
  if (tabLink) tabLink.classList.add("active");

  currentTab = tab;
  renderTabContent(tab, acta);

  if (!offcanvasInst) {
    offcanvasInst = new bootstrap.Offcanvas(document.getElementById("offcanvasDetalle"));
  }
  offcanvasInst.show();
}

function mostrarTab(tab, el) {
  document.querySelectorAll("#detalleTabs .nav-link").forEach(e => e.classList.remove("active"));
  el.classList.add("active");
  currentTab = tab;
  if (currentActa) renderTabContent(tab, currentActa);
  return false;
}

// ── Contenido tabs ────────────────────────────────────────────────────────────

function renderTabContent(tab, acta) {
  const cnt = document.getElementById("detalleContent");

  if      (tab === "pdf")         renderTabPdf(cnt, acta);
  else if (tab === "ocr")         renderTabOcr(cnt, acta);
  else if (tab === "comparacion") renderTabComparacion(cnt, acta);
  else if (tab === "forense")     renderTabForense(cnt, acta);
  else if (tab === "ocsvm")       renderTabOcsvm(cnt, acta);
  else if (tab === "api")         renderTabApi(cnt, acta);
}

// ─── PDF ───────────────────────────────────────────────────────────────────────
function renderTabPdf(cnt, acta) {
  if (!acta.tiene_pdf) {
    cnt.innerHTML = `
      <div class="text-center text-muted py-5">
        <i class="bi bi-file-pdf display-4 d-block mb-3" style="color:#f85149"></i>
        <div class="mb-2">No hay PDF descargado para esta acta</div>
        <small>Ejecuta el barrido con opción "Descargar PDFs" activada</small>
      </div>`;
    return;
  }

  cnt.innerHTML = `
    <div class="text-center">
      <div id="pdfImgWrap" class="pdf-img-container mb-2" style="min-height:200px;display:flex;align-items:center;justify-content:center">
        <div class="spinner-border text-secondary" role="status"></div>
      </div>
      <div class="d-flex justify-content-center align-items-center gap-2 mt-2" id="pdfNavBar">
        <button class="btn btn-sm btn-outline-secondary" id="pdfBtnPrev" onclick="cambioPdfPag(-1)" disabled>
          <i class="bi bi-chevron-left"></i>
        </button>
        <span id="pdfPagLabel" class="text-muted small">—</span>
        <button class="btn btn-sm btn-outline-secondary" id="pdfBtnNext" onclick="cambioPdfPag(1)" disabled>
          <i class="bi bi-chevron-right"></i>
        </button>
        <a href="/api/pdf_download/${escHtml(acta.codigo_mesa)}/${acta.id_eleccion}"
           class="btn btn-sm btn-outline-primary ms-2" target="_blank">
          <i class="bi bi-download"></i> Descargar PDF
        </a>
      </div>
    </div>`;

  pdfState = { mesa: acta.codigo_mesa, eleccion: acta.id_eleccion, page: 1, total: 1 };
  loadPdfPage(acta.codigo_mesa, acta.id_eleccion, 1);
}

async function loadPdfPage(mesa, eleccion, page) {
  try {
    const res  = await fetch(`/api/pdf_preview/${mesa}/${eleccion}/${page}`);
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "No se pudo cargar");

    pdfState.page  = page;
    pdfState.total = json.data.total_pages;

    document.getElementById("pdfImgWrap").innerHTML =
      `<img src="data:image/jpeg;base64,${json.data.image_b64}"
            class="img-fluid" style="max-height:520px" alt="Página ${page}">`;

    document.getElementById("pdfPagLabel").textContent = `${page} / ${pdfState.total}`;
    document.getElementById("pdfBtnPrev").disabled = page <= 1;
    document.getElementById("pdfBtnNext").disabled = page >= pdfState.total;

  } catch (e) {
    document.getElementById("pdfImgWrap").innerHTML =
      `<div class="text-danger small p-3"><i class="bi bi-exclamation-triangle me-2"></i>${escHtml(e.message)}</div>`;
  }
}

function cambioPdfPag(delta) {
  const np = pdfState.page + delta;
  if (np >= 1 && np <= pdfState.total && pdfState.mesa) {
    document.getElementById("pdfImgWrap").innerHTML =
      `<div class="d-flex align-items-center justify-content-center" style="min-height:200px">
         <div class="spinner-border spinner-border-sm text-secondary"></div>
       </div>`;
    loadPdfPage(pdfState.mesa, pdfState.eleccion, np);
  }
}

// ─── OCR ───────────────────────────────────────────────────────────────────────
function renderTabOcr(cnt, acta) {
  if (!acta.tiene_ocr) {
    cnt.innerHTML = `
      <div class="text-center text-muted py-5">
        <i class="bi bi-text-paragraph display-4 d-block mb-3" style="color:#58a6ff"></i>
        <div class="mb-2">Sin datos OCR para esta acta</div>
        ${acta.tiene_pdf
          ? `<button class="btn btn-sm btn-outline-info mt-2"
                     onclick="procesarOcrAhora('${escHtml(acta.codigo_mesa)}',${acta.id_eleccion})">
               <i class="bi bi-play-fill"></i> Procesar OCR ahora
             </button>`
          : `<small>Descarga primero el PDF con el barrido</small>`}
      </div>`;
    return;
  }

  const hasGoogle = acta.datos_json_ocr_google && Object.keys(acta.datos_json_ocr_google).length > 0;
  const hasLocal  = acta.datos_json_ocr_local  && Object.keys(acta.datos_json_ocr_local).length > 0;
  const hasAny    = hasGoogle || hasLocal;

  // Determine initial source to show
  const initialSrc = hasGoogle ? "google" : "local";

  function renderOcrSource(src) {
    const datos  = src === "google" ? (acta.datos_json_ocr_google || {}) : (acta.datos_json_ocr_local || {});
    const metodo = src === "google" ? "google_enterprise_ocr" : (acta.metodo_ocr || "local");
    const esEsc  = acta.es_escaneado === 1;

    const btnGoogle = `<button class="btn btn-sm ${src === "google" ? "btn-primary" : "btn-outline-secondary"}"
        onclick="_switchOcrSource(this,'google','${escHtml(acta.codigo_mesa)}',${acta.id_eleccion})"
        ${!hasGoogle ? "disabled title='Sin datos Google OCR'" : ""}>
      <i class="bi bi-google me-1"></i>Google DocAI
      ${hasGoogle ? "" : "<span class='text-warning ms-1'>✕</span>"}
    </button>`;
    const btnLocal = !hasLocal && acta.tiene_pdf
      ? `<button class="btn btn-sm btn-outline-warning"
            id="btnProcesarLocalOcr"
            onclick="procesarOcrLocalAhora('${escHtml(acta.codigo_mesa)}',${acta.id_eleccion},this)">
          <i class="bi bi-cpu me-1"></i>Procesar OCR local
        </button>`
      : `<button class="btn btn-sm ${src === "local" ? "btn-warning text-dark" : "btn-outline-secondary"}"
            onclick="_switchOcrSource(this,'local','${escHtml(acta.codigo_mesa)}',${acta.id_eleccion})"
            ${!hasLocal ? "disabled title='Sin datos OCR local'" : ""}>
          <i class="bi bi-cpu me-1"></i>Local (Tesseract/MNIST)
          ${hasLocal ? "" : "<span class='text-warning ms-1'>✕</span>"}
        </button>`;

    let html = `
      <div class="d-flex gap-2 align-items-center mb-3 flex-wrap">
        ${btnGoogle}
        ${btnLocal}
        <span class="badge ${esEsc ? "badge-escaneado" : "badge-digital"} ms-2">
          <i class="bi ${esEsc ? "bi-camera-fill" : "bi-file-earmark-text-fill"}"></i>
          ${esEsc ? "Escaneado" : "Digital"}
        </span>
        <span class="badge badge-sin">Método: ${escHtml(metodo)}</span>
      </div>`;

    const entries = Object.entries(datos);
    if (entries.length === 0) {
      html += `<div class="text-muted small text-center py-3">
        <i class="bi bi-info-circle me-1"></i>
        Sin datos estructurados de este OCR${src === "google" ? " — ejecuta el barrido con motor Google" : " — ejecuta el barrido con motor Local"}.
      </div>`;
    } else {
      html += `<div class="table-responsive">
        <table class="table table-dark table-sm table-bordered">
          <thead><tr><th style="width:45%">Campo</th><th>Valor</th></tr></thead>
          <tbody>`;
      for (const [k, v] of entries) {
        const display = typeof v === "object" ? JSON.stringify(v, null, 2) : String(v ?? "—");
        html += `<tr>
          <td class="text-info fw-semibold">${escHtml(k)}</td>
          <td class="font-monospace">${escHtml(display)}</td>
        </tr>`;
      }
      html += `</tbody></table></div>`;
    }
    return html;
  }

  // Store context for switching
  cnt._ocrActa = acta;
  cnt.innerHTML = `<div id="ocrSourceContent">${renderOcrSource(initialSrc)}</div>`;

  // Expose switch function scoped to this container
  window._switchOcrSource = function(btn, src, mesa, elec) {
    const ocrCnt = document.getElementById("ocrSourceContent")?.parentElement;
    if (!ocrCnt) return;
    const a = ocrCnt._ocrActa;
    if (!a) return;
    ocrCnt.querySelector("#ocrSourceContent").innerHTML = renderOcrSource(src);
  };
}

// ─── Comparación ──────────────────────────────────────────────────────────────
function renderTabComparacion(cnt, acta) {
  if (!acta.tiene_ocr) {
    cnt.innerHTML = `
      <div class="text-center text-muted py-5">
        <i class="bi bi-arrows-expand display-4 d-block mb-3" style="color:#ffa657"></i>
        Se necesita OCR para comparar con datos de la API
      </div>`;
    return;
  }

  const modo = window._comparacionModo || "simple";

  const apiData    = acta.api_json || {};
  const ocrData    = acta.datos_json_ocr   || {};
  const ocrLocal   = acta.datos_json_ocr_local  || {};
  const ocrGoogle  = acta.datos_json_ocr_google || {};
  const compEstado = acta.comp_estado;
  const isHibrido  = (modo === "hibrido" || modo === "hibrido_ia");

  const badgeEstado = compEstado === "alerta"
    ? `<span class="badge badge-alerta fs-6 px-3 py-2"><i class="bi bi-exclamation-triangle me-2"></i>Con discrepancias</span>`
    : compEstado === "ok"
    ? `<span class="badge badge-ok fs-6 px-3 py-2"><i class="bi bi-check-circle me-2"></i>Sin discrepancias</span>`
    : `<span class="badge badge-sin fs-6 px-3 py-2"><i class="bi bi-dash me-2"></i>Sin comparación guardada</span>`;

  const btnComparar = `<button class="btn btn-sm btn-outline-info"
      onclick="procesarComparacionAhora('${escHtml(acta.codigo_mesa)}',${acta.id_eleccion})">
    <i class="bi bi-arrow-repeat"></i> Comparar y guardar
  </button>`;

  let html = `
    <div class="d-flex justify-content-between align-items-center mb-3">
      ${badgeEstado}
      <div class="d-flex gap-2 align-items-center">
        <span class="badge ${isHibrido ? "bg-info text-dark" : "bg-secondary"} small">Modo: ${modo}</span>
        ${btnComparar}
      </div>
    </div>`;

  const hasApi = Object.keys(apiData).length > 0;
  if (!hasApi) {
    html += `<div class="text-muted small text-center py-3">Sin datos de API — ejecuta el barrido o usa <strong>Comparar y guardar</strong></div>`;
    cnt.innerHTML = html;
    return;
  }

  // ── Normalización / fuzzy match (compartido con ambos modos) ──────────────
  function _normStr(s) {
    return s.toUpperCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  }
  function nombreMatch(a, b) {
    const na = _normStr(a), nb = _normStr(b);
    if (na === nb) return true;
    const wa = na.split(/\s+/).filter(w => w.length >= 4);
    const wb = nb.split(/\s+/).filter(w => w.length >= 4);
    if (!wa.length || !wb.length) return false;
    const setA = new Set(wa);
    const setB = new Set(wb);
    const inter = [...setA].filter(w => setB.has(w)).length;
    const union = new Set([...setA, ...setB]).size;
    return inter / union >= 0.5;
  }
  const _OCR_EXCLUIR = ["TOTAL EMITIDOS","VOTOS EN BLANCO","VOTOS BLANCOS",
                        "VOTOS NULOS","VOTOS IMPUGNADOS","EN BLANCO","NULOS","IMPUGNADOS"];
  function filtrarPartidos(vpp) {
    const out = {};
    for (const [k, v] of Object.entries(vpp || {})) {
      const ku = k.toUpperCase().trim();
      if (_OCR_EXCLUIR.some(e => ku === e || ku.startsWith(e))) continue;
      out[k] = v;
    }
    return out;
  }

  // Partidos API
  const apiPartidos = {};
  for (const item of (apiData.detalle || [])) {
    const nombre = (item.descripcion || item.adDescripcion || "").trim();
    const nom_up = nombre.toUpperCase();
    if (nom_up.includes("NULOS") || nom_up.includes("BLANCO") || nom_up.includes("IMPUGNADOS")) continue;
    const v = item.nvotos ?? item.adVotos;
    if (v != null) apiPartidos[nombre] = parseInt(v);
  }

  // Totales API para nulos/blancos
  let apiNulos = null, apiBlancos = null;
  for (const item of (apiData.detalle || [])) {
    const nombre = (item.descripcion || item.adDescripcion || "").trim().toUpperCase();
    const v = item.nvotos ?? item.adVotos;
    if (v == null) continue;
    if (nombre.includes("NULOS"))  apiNulos   = parseInt(v);
    if (nombre.includes("BLANCO")) apiBlancos = parseInt(v);
  }

  // ── MODO HÍBRIDO (5 columnas) ─────────────────────────────────────────────
  if (isHibrido) {
    const locPartidos = filtrarPartidos(ocrLocal.votos_por_partido  || {});
    const gooPartidos = filtrarPartidos(ocrGoogle.votos_por_partido || {});

    // Filas de totales para modo híbrido
    const totalesH = [
      { campo: "Electores Hábiles", apiVal: apiData.totalElectoresHabiles,
        locVal: ocrLocal.electores_habiles, gooVal: ocrGoogle.electores_habiles },
      { campo: "Total Votantes",    apiVal: apiData.totalVotosEmitidos,
        locVal: ocrLocal.total_votantes,    gooVal: ocrGoogle.total_votantes },
      { campo: "Votos Válidos",     apiVal: apiData.totalVotosValidos,
        locVal: ocrLocal.votos_validos,     gooVal: ocrGoogle.votos_validos },
      { campo: "Votos Nulos",       apiVal: apiNulos,
        locVal: ocrLocal.votos_nulos,       gooVal: ocrGoogle.votos_nulos },
      { campo: "Votos en Blanco",   apiVal: apiBlancos,
        locVal: ocrLocal.votos_blancos,     gooVal: ocrGoogle.votos_blancos },
    ];

    function celdaHibrido(row) {
      const apiStr = row.apiVal != null ? escHtml(String(row.apiVal)) : `<span class="text-muted">—</span>`;
      const locStr = row.locVal != null ? escHtml(String(row.locVal)) : `<span class="text-muted">—</span>`;
      const gooStr = row.gooVal != null ? escHtml(String(row.gooVal)) : `<span class="text-muted">—</span>`;

      const coinLoc = row.apiVal != null && row.locVal != null && Number(row.apiVal) === Number(row.locVal);
      const coinGoo = row.apiVal != null && row.gooVal != null && Number(row.apiVal) === Number(row.gooVal);
      const coinAny = coinLoc || coinGoo;

      let estadoCell;
      if (row.apiVal == null) {
        estadoCell = `<span class="text-muted">—</span>`;
      } else if (row.locVal == null && row.gooVal == null) {
        estadoCell = `<span class="text-secondary" title="Sin datos OCR">⚠</span>`;
      } else if (coinAny) {
        const lbl = coinLoc && coinGoo ? "ambos" : coinLoc ? "local" : "google";
        estadoCell = `<span class="text-success" title="Coincide (${lbl})">✓</span>`;
      } else {
        const dL = row.locVal != null ? (Number(row.apiVal) - Number(row.locVal)) : null;
        const dG = row.gooVal != null ? (Number(row.apiVal) - Number(row.gooVal)) : null;
        const parts = [];
        if (dL != null) parts.push(`L:${dL > 0 ? "+" : ""}${dL}`);
        if (dG != null) parts.push(`G:${dG > 0 ? "+" : ""}${dG}`);
        estadoCell = `<span class="text-danger fw-bold" title="Ningún OCR coincide">${parts.join(" ")}</span>`;
      }

      const trClass = (!coinAny && row.apiVal != null && (row.locVal != null || row.gooVal != null))
        ? ' class="table-danger"' : '';
      return `<tr${trClass}>
        <td class="text-info">${escHtml(row.campo)}</td>
        <td class="font-monospace text-end">${apiStr}</td>
        <td class="font-monospace text-end ${coinGoo ? "text-success" : ""}">${gooStr}</td>
        <td class="font-monospace text-end ${coinLoc ? "text-success" : ""}">${locStr}</td>
        <td class="text-center">${estadoCell}</td>
      </tr>`;
    }

    // Partidos híbrido
    const matchedLoc = new Set(), matchedGoo = new Set();
    const partidoRowsH = [];
    for (const [apiNombre, apiV] of Object.entries(apiPartidos)) {
      let locNombre = null, gooNombre = null;
      for (const n of Object.keys(locPartidos)) { if (nombreMatch(apiNombre, n)) { locNombre = n; matchedLoc.add(n); break; } }
      for (const n of Object.keys(gooPartidos)) { if (nombreMatch(apiNombre, n)) { gooNombre = n; matchedGoo.add(n); break; } }
      const locV = locNombre != null ? locPartidos[locNombre] : null;
      const gooV = gooNombre != null ? gooPartidos[gooNombre] : null;
      partidoRowsH.push({ campo: apiNombre, apiVal: apiV, locVal: locV, gooVal: gooV });
    }

    // Indicador de anomalías totales (ningún OCR coincidió)
    const anomalias = partidoRowsH.filter(r =>
      r.apiVal != null &&
      (r.locVal == null || Number(r.apiVal) !== Number(r.locVal)) &&
      (r.gooVal == null || Number(r.apiVal) !== Number(r.gooVal))
    );

    html += `<div class="table-responsive">
      <table class="table table-dark table-sm table-bordered mb-0">
        <thead>
          <tr>
            <th style="width:38%">Campo</th>
            <th class="text-end" style="width:14%"><i class="bi bi-cloud text-info"></i> API</th>
            <th class="text-end" style="width:14%"><i class="bi bi-google text-primary"></i> Google</th>
            <th class="text-end" style="width:14%"><i class="bi bi-cpu text-warning"></i> Local</th>
            <th class="text-center" style="width:14%">Estado</th>
          </tr>
        </thead>
        <tbody>
          <tr class="table-secondary">
            <td colspan="5" class="text-muted small fw-semibold px-2 py-1">Totales generales</td>
          </tr>`;

    for (const row of totalesH) {
      if (row.apiVal == null && row.locVal == null && row.gooVal == null) continue;
      html += celdaHibrido(row);
    }

    if (partidoRowsH.length > 0) {
      html += `<tr class="table-secondary">
        <td colspan="5" class="text-muted small fw-semibold px-2 py-1">Votos por partido
          ${anomalias.length > 0 ? `<span class="badge bg-danger ms-2">${anomalias.length} anomalía${anomalias.length > 1 ? "s" : ""}</span>` : ""}
        </td>
      </tr>`;
      for (const row of partidoRowsH) html += celdaHibrido(row);
    }

    html += `</tbody></table></div>`;

    // Botón Gemini (solo en modo hibrido_ia con anomalías)
    if (modo === "hibrido_ia" && anomalias.length > 0) {
      html += `
        <div class="mt-3 d-flex align-items-center gap-3">
          <button class="btn btn-sm btn-outline-warning fw-bold"
              onclick="analizarConGemini('${escHtml(acta.codigo_mesa)}',${acta.id_eleccion},this)">
            <i class="bi bi-stars me-1"></i>Analizar con Gemini Flash 2.5
          </button>
          <span class="text-muted small">${anomalias.length} campo${anomalias.length > 1 ? "s" : ""} sin consenso</span>
        </div>
        <div id="geminiResultPanel" class="mt-2 d-none"></div>`;
    }

    cnt.innerHTML = html;
    return;
  }

  // ── MODO SIMPLE (4 columnas — comportamiento original) ────────────────────
  const ocrPartidos = filtrarPartidos(ocrData.votos_por_partido || {});

  const totalesRows = [
    { campo: "Electores Hábiles", apiVal: apiData.totalElectoresHabiles, ocrVal: ocrData.electores_habiles },
    { campo: "Total Votantes",    apiVal: apiData.totalVotosEmitidos,    ocrVal: ocrData.total_votantes },
    { campo: "Votos Válidos",     apiVal: apiData.totalVotosValidos,     ocrVal: ocrData.votos_validos },
    { campo: "Votos Nulos",       apiVal: apiNulos,                      ocrVal: ocrData.votos_nulos },
    { campo: "Votos en Blanco",   apiVal: apiBlancos,                    ocrVal: ocrData.votos_blancos },
  ];

  const partidoRows = [];
  const matchedOcr  = new Set();
  for (const [apiNombre, apiV] of Object.entries(apiPartidos)) {
    let ocrNombre = null;
    for (const ocn of Object.keys(ocrPartidos)) {
      if (nombreMatch(apiNombre, ocn)) { ocrNombre = ocn; matchedOcr.add(ocn); break; }
    }
    partidoRows.push({
      campo: apiNombre, apiVal: apiV,
      ocrVal: ocrNombre != null ? ocrPartidos[ocrNombre] : null,
      soloApi: ocrNombre == null,
    });
  }
  for (const [ocrNombre, ocrV] of Object.entries(ocrPartidos)) {
    if (!matchedOcr.has(ocrNombre)) {
      partidoRows.push({ campo: ocrNombre, apiVal: null, ocrVal: ocrV, soloOcr: true });
    }
  }

  function celdaFila(row) {
    const apiStr = row.apiVal != null ? escHtml(String(row.apiVal)) : `<span class="text-muted">—</span>`;
    const ocrStr = row.ocrVal != null ? escHtml(String(row.ocrVal)) : `<span class="text-muted">—</span>`;
    let estadoCell;
    if (row.apiVal == null || row.ocrVal == null) {
      estadoCell = `<span class="text-secondary" title="Solo en una fuente">⚠</span>`;
    } else if (row.apiVal === row.ocrVal) {
      estadoCell = `<span class="text-success">✓</span>`;
    } else {
      const delta = row.apiVal - row.ocrVal;
      estadoCell = `<span class="text-warning fw-bold">${delta > 0 ? "+" : ""}${delta}</span>`;
    }
    const trClass = (row.apiVal != null && row.ocrVal != null && row.apiVal !== row.ocrVal)
      ? ' class="table-warning"' : '';
    return `<tr${trClass}>
      <td class="text-info">${escHtml(row.campo)}</td>
      <td class="font-monospace text-end">${apiStr}</td>
      <td class="font-monospace text-end">${ocrStr}</td>
      <td class="text-center">${estadoCell}</td>
    </tr>`;
  }

  html += `<div class="table-responsive">
    <table class="table table-dark table-sm table-bordered mb-0">
      <thead>
        <tr>
          <th style="width:45%">Campo</th>
          <th class="text-end" style="width:18%"><i class="bi bi-cloud text-info"></i> API</th>
          <th class="text-end" style="width:18%"><i class="bi bi-text-paragraph text-warning"></i> OCR</th>
          <th class="text-center" style="width:12%">Estado</th>
        </tr>
      </thead>
      <tbody>
        <tr class="table-secondary">
          <td colspan="4" class="text-muted small fw-semibold px-2 py-1">Totales generales</td>
        </tr>`;

  for (const row of totalesRows) {
    if (row.apiVal == null && row.ocrVal == null) continue;
    html += celdaFila(row);
  }

  if (partidoRows.length > 0) {
    html += `<tr class="table-secondary">
      <td colspan="4" class="text-muted small fw-semibold px-2 py-1">Votos por partido</td>
    </tr>`;
    for (const row of partidoRows) html += celdaFila(row);
  } else if (!hasApi) {
    html += `<tr><td colspan="4" class="text-muted small text-center py-2">
      Sin datos de API — ejecuta el barrido o usa <strong>Comparar y guardar</strong>
    </td></tr>`;
  }

  html += `</tbody></table></div>`;
  cnt.innerHTML = html;
}

// ─── Gemini Flash 2.5 — análisis imagen ──────────────────────────────────────

async function analizarConGemini(mesa, eleccion, btn) {
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Analizando…`; }
  const panel = document.getElementById("geminiResultPanel");
  if (panel) panel.className = "mt-2";

  try {
    const res  = await fetch("/api/comparar/gemini", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ codigo_mesa: mesa, id_eleccion: eleccion }),
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "Error Gemini");

    const d = json.data;
    const esAnom = d.anomalia === true;
    const esOk   = d.anomalia === false;
    const conf   = d.confianza || "?";

    const badgeColor = esAnom ? "danger" : esOk ? "success" : "secondary";
    const badgeLabel = esAnom ? "ANOMALÍA" : esOk ? "SIN ANOMALÍA" : "INDETERMINADO";

    if (panel) panel.innerHTML = `
      <div class="card border-warning" style="background:#1c1a05">
        <div class="card-header small fw-bold d-flex justify-content-between align-items-center"
             style="background:#2a2500;color:#ffd700">
          <span><i class="bi bi-stars me-1"></i>Gemini Flash 2.5</span>
          <div class="d-flex gap-2">
            <span class="badge bg-${badgeColor}">${badgeLabel}</span>
            <span class="badge bg-secondary">confianza: ${escHtml(conf)}</span>
          </div>
        </div>
        <div class="card-body small" style="color:#e6edf3;white-space:pre-wrap">
          ${escHtml(d.anotacion || "(sin anotación)")}
        </div>
      </div>`;

    if (btn) { btn.disabled = false; btn.innerHTML = `<i class="bi bi-stars me-1"></i>Analizar con Gemini Flash 2.5`; }
  } catch (e) {
    if (panel) panel.innerHTML = `<div class="alert alert-danger small py-2">${escHtml(e.message)}</div>`;
    if (btn) { btn.disabled = false; btn.innerHTML = `<i class="bi bi-stars me-1"></i>Analizar con Gemini Flash 2.5`; }
  }
}

// ─── Forense ──────────────────────────────────────────────────────────────────
function renderTabForense(cnt, acta) {
  if (!acta.tiene_forense) {
    cnt.innerHTML = `
      <div class="text-center text-muted py-5">
        <i class="bi bi-shield-exclamation display-4 d-block mb-3" style="color:#e3b341"></i>
        Sin análisis forense para esta acta
      </div>`;
    return;
  }

  const alertas = acta.alertas_forense || [];
  const tieneAnom = acta.anomalia_forense === 1;

  const badgeGlobal = tieneAnom
    ? `<span class="badge badge-alerta badge-anomaly-anim fs-6 px-3 py-2">
         <i class="bi bi-exclamation-triangle-fill me-2"></i>ALERTA FORENSE
       </span>`
    : `<span class="badge badge-ok fs-6 px-3 py-2">
         <i class="bi bi-shield-check me-2"></i>Sin anomalías
       </span>`;

  let html = `<div class="text-center mb-4">${badgeGlobal}</div>`;

  if (alertas.length === 0) {
    html += `<div class="text-muted small text-center">No se registraron alertas.</div>`;
  } else {
    html += alertas.map(a => {
      const color = a.nivel === "alerta" ? "danger" : a.nivel === "advertencia" ? "warning" : "info";
      return `<div class="alert alert-${color} py-2 small border-0"
                   style="background:rgba(var(--bs-${color}-rgb),.15);border-left:3px solid var(--bs-${color}) !important">
        <div class="fw-bold mb-1">
          <i class="bi bi-exclamation-circle me-1"></i>${escHtml(a.tipo || "Alerta")}
          <span class="badge bg-${color} ms-2 fw-normal" style="font-size:.65rem">${escHtml(a.nivel || "")}</span>
        </div>
        <div>${escHtml(a.descripcion || a.mensaje || JSON.stringify(a))}</div>
      </div>`;
    }).join("");
  }

  cnt.innerHTML = html;
}

// ─── OCSVM ────────────────────────────────────────────────────────────────────
function renderTabOcsvm(cnt, acta) {
  const isAnom  = acta.is_anomaly_svm;
  const score   = acta.score_svm;
  const conf    = acta.confidence_svm;

  let badgeAnom = '';
  if (isAnom === 1 || isAnom === true) {
    badgeAnom = `<span class="badge badge-alerta badge-anomaly-anim fs-6 px-3 py-2">
      <i class="bi bi-cpu-fill me-2"></i>ANOMALÍA OCSVM</span>`;
  } else if (isAnom === 0 || isAnom === false) {
    badgeAnom = `<span class="badge badge-ok fs-6 px-3 py-2">
      <i class="bi bi-cpu me-2"></i>Normal (OCSVM)</span>`;
  } else {
    badgeAnom = `<span class="badge badge-sin fs-6 px-3 py-2">
      <i class="bi bi-cpu me-2"></i>Sin análisis OCSVM</span>`;
  }

  let html = `<div class="text-center mb-4">${badgeAnom}</div>`;

  // Métricas actuales
  if (score != null || conf != null) {
    html += `<div class="row g-2 mb-3">`;
    if (score != null) {
      const scoreNum = parseFloat(score);
      const scoreColor = isAnom ? "#f85149" : "#3fb950";
      html += `<div class="col-6">
        <div class="p-2 rounded text-center" style="background:#0a0f16;border:1px solid #30363d">
          <div class="fw-bold fs-5" style="color:${scoreColor}">${scoreNum.toFixed(4)}</div>
          <div class="text-muted" style="font-size:.7rem">Score SVM</div>
        </div></div>`;
    }
    if (conf != null) {
      html += `<div class="col-6">
        <div class="p-2 rounded text-center" style="background:#0a0f16;border:1px solid #30363d">
          <div class="fw-bold fs-5 text-warning">${escHtml(String(conf))}</div>
          <div class="text-muted" style="font-size:.7rem">Confianza</div>
        </div></div>`;
    }
    html += `</div>`;
  }

  // Acciones OCSVM
  html += `
    <div class="d-grid gap-2 mb-3">
      <button class="btn btn-sm btn-outline-warning" id="btnOcsvmAnalizar"
          onclick="ocsvmAnalizarActa('${escHtml(acta.codigo_mesa)}',${acta.id_eleccion})">
        <i class="bi bi-search"></i> Analizar esta acta
      </button>
    </div>
    <hr style="border-color:#30363d">
    <div class="text-muted small mb-2 fw-semibold">Modelo global</div>
    <div class="d-flex gap-2 mb-2">
      <button class="btn btn-sm btn-outline-secondary flex-fill" id="btnOcsvmMasivo"
          onclick="ocsvmAnalizarTodas()">
        <i class="bi bi-collection"></i> Analizar todas
      </button>
    </div>
    <div class="d-flex gap-2 align-items-center mb-1">
      <label class="text-muted small" style="white-space:nowrap">ν (nu):</label>
      <input type="number" id="ocsvmNuInput" class="form-control form-control-sm"
             style="max-width:90px;background:#0a0f16;border-color:#30363d;color:#c9d1d9"
             min="0.01" max="0.5" step="0.01" value="0.05">
      <button class="btn btn-sm btn-outline-primary flex-fill" id="btnOcsvmEntrenar"
          onclick="ocsvmEntrenar()">
        <i class="bi bi-robot"></i> Entrenar modelo
      </button>
    </div>
    <div id="ocsvmResult" class="mt-2 small"></div>`;

  cnt.innerHTML = html;
}

async function ocsvmAnalizarActa(mesa, eleccion) {
  const btn = document.getElementById("btnOcsvmAnalizar");
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Analizando...`; }
  try {
    const res  = await fetch("/api/anomalias/analizar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ codigo_mesa: mesa, id_eleccion: eleccion }),
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "Error");
    const d = json.data;
    if (currentActa) {
      currentActa.is_anomaly_svm  = d.is_anomaly ? 1 : 0;
      currentActa.score_svm       = d.score;
      currentActa.confidence_svm  = d.confidence;
      renderTabOcsvm(document.getElementById("detalleContent"), currentActa);
    }
    toast(d.is_anomaly ? "⚠ Anomalía detectada" : "Análisis OK: normal", d.is_anomaly ? "warn" : "ok");
  } catch (e) {
    toast("Error OCSVM: " + e.message, "err");
    if (btn) { btn.disabled = false; btn.innerHTML = `<i class="bi bi-search"></i> Analizar esta acta`; }
  }
}

async function ocsvmAnalizarTodas() {
  const btn = document.getElementById("btnOcsvmMasivo");
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Procesando...`; }
  try {
    const res  = await fetch("/api/anomalias/analizar_todas", { method: "POST" });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "Error");
    const d = json.data;
    const out = document.getElementById("ocsvmResult");
    if (out) out.innerHTML = `<span class="text-success">
      ${d.total_analizadas} actas analizadas · ${d.n_anomalias} anomalías</span>`;
    toast(`Análisis masivo: ${d.n_anomalias} anomalías`, d.n_anomalias > 0 ? "warn" : "ok");
  } catch (e) {
    toast("Error: " + e.message, "err");
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = `<i class="bi bi-collection"></i> Analizar todas`; }
  }
}

async function ocsvmEntrenar() {
  const btn   = document.getElementById("btnOcsvmEntrenar");
  const nuInp = document.getElementById("ocsvmNuInput");
  const nu    = parseFloat(nuInp?.value || 0.05);
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Entrenando...`; }
  try {
    const res  = await fetch("/api/anomalias/entrenar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nu }),
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "Error");
    const d = json.data;
    const out = document.getElementById("ocsvmResult");
    if (out) out.innerHTML = `<span class="text-success">
      Modelo entrenado con ${d.n_samples || "?"} muestras (ν=${nu})</span>`;
    toast("Modelo OCSVM entrenado", "ok");
  } catch (e) {
    toast("Error entrenando: " + e.message, "err");
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = `<i class="bi bi-robot"></i> Entrenar modelo`; }
  }
}

// ─── API raw ──────────────────────────────────────────────────────────────────
function renderTabApi(cnt, acta) {
  const apiData = acta.api_json || {};
  const hasData = Object.keys(apiData).length > 0;

  // Botón para cargar datos frescos
  const btnRefresh = `<button class="btn btn-sm btn-outline-info" id="btnRefreshApi"
      onclick="refreshApiData('${escHtml(acta.codigo_mesa)}',${acta.id_eleccion})">
    <i class="bi bi-arrow-clockwise"></i> Cargar desde API ONPE
  </button>`;

  if (!hasData) {
    cnt.innerHTML = `
      <div class="text-center py-4">
        <i class="bi bi-cloud-slash display-4 d-block mb-3 text-muted"></i>
        <div class="text-muted small mb-3">Sin datos de API guardados para esta acta</div>
        ${btnRefresh}
      </div>`;
    return;
  }

  // Mostrar datos estructurados
  let html = `
    <div class="d-flex justify-content-between align-items-center mb-3">
      <span class="text-muted small">Datos de la API ONPE</span>
      ${btnRefresh}
    </div>`;

  // Tarjetas de totales
  const totals = [
    { label: "Electores hábiles", val: apiData.totalElectoresHabiles },
    { label: "Votos emitidos",    val: apiData.totalVotosEmitidos },
    { label: "Votos válidos",     val: apiData.totalVotosValidos },
  ].filter(t => t.val != null);

  if (totals.length > 0) {
    html += `<div class="row g-2 mb-3">`;
    for (const t of totals) {
      html += `<div class="col-4">
        <div class="p-2 rounded text-center" style="background:#0a0f16;border:1px solid #30363d">
          <div class="fw-bold text-info fs-5">${escHtml(String(t.val))}</div>
          <div class="text-muted" style="font-size:.7rem">${escHtml(t.label)}</div>
        </div>
      </div>`;
    }
    html += `</div>`;
  }

  // Tabla de votos por partido
  const detalle = apiData.detalle || [];
  if (detalle.length > 0) {
    html += `<div class="table-responsive mb-3">
      <table class="table table-dark table-sm table-bordered mb-0">
        <thead><tr>
          <th>Partido / Categoría</th>
          <th class="text-end">Votos</th>
        </tr></thead>
        <tbody>`;
    for (const item of detalle) {
      const nombre = item.descripcion || item.adDescripcion || "—";
      const votos  = item.nvotos ?? item.adVotos ?? "—";
      const nom_up = nombre.toUpperCase();
      const cls = nom_up.includes("NULOS") || nom_up.includes("BLANCO") || nom_up.includes("IMPUGNADOS")
        ? "text-muted" : "text-info";
      html += `<tr>
        <td class="${cls}">${escHtml(nombre)}</td>
        <td class="text-end font-monospace">${escHtml(String(votos))}</td>
      </tr>`;
    }
    html += `</tbody></table></div>`;
  }

  // JSON crudo colapsable
  html += `
    <details class="mt-2">
      <summary class="text-muted small" style="cursor:pointer">Ver JSON completo</summary>
      <pre class="rounded p-3 mt-2 small text-info"
           style="background:#0a0f16;overflow-x:auto;white-space:pre-wrap;word-break:break-all;font-size:.72rem"
      >${escHtml(JSON.stringify(apiData, null, 2))}</pre>
    </details>`;

  cnt.innerHTML = html;
}

async function refreshApiData(mesa, eleccion) {
  const btn = document.getElementById("btnRefreshApi");
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Cargando...`; }
  try {
    const res  = await fetch(`/api/acta/refresh_api/${encodeURIComponent(mesa)}/${eleccion}`, { method: "POST" });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "Error al cargar");
    // Actualizar el acta en memoria y rerenderizar
    if (currentActa) {
      currentActa.api_json = json.data.api_json;
      renderTabApi(document.getElementById("detalleContent"), currentActa);
    }
    toast("Datos de API cargados", "ok");
  } catch (e) {
    toast("Error: " + e.message, "err");
    if (btn) { btn.disabled = false; btn.innerHTML = `<i class="bi bi-arrow-clockwise"></i> Cargar desde API ONPE`; }
  }
}

// ── Acciones rápidas ──────────────────────────────────────────────────────────

async function procesarOcrAhora(mesa, eleccion) {
  const btn = document.querySelector(`button[onclick*="procesarOcrAhora"]`);
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Procesando...`; }

  try {
    const res  = await fetch("/api/procesar_ocr", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ codigo_mesa: mesa, id_eleccion: eleccion }),
    });
    const json = await res.json();
    if (json.ok) {
      toast("OCR procesado correctamente", "ok");
      offcanvasInst?.hide();
      cargarResultados(currentPage);
    } else {
      throw new Error(json.error || "Error en OCR");
    }
  } catch (e) {
    toast("Error OCR: " + e.message, "err");
    if (btn) { btn.disabled = false; btn.innerHTML = `<i class="bi bi-play-fill"></i> Procesar OCR ahora`; }
  }
}

async function procesarOcrLocalAhora(mesa, eleccion, btnEl) {
  if (btnEl) { btnEl.disabled = true; btnEl.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Procesando...`; }
  try {
    const res  = await fetch("/api/auto/procesar_ocr", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ codigo_mesa: mesa, id_eleccion: eleccion, ocr_engine: "local" }),
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "Error OCR local");
    toast("OCR local procesado correctamente", "ok");
    offcanvasInst?.hide();
    cargarResultados(currentPage);
  } catch (e) {
    toast("Error OCR local: " + e.message, "err");
    if (btnEl) { btnEl.disabled = false; btnEl.innerHTML = `<i class="bi bi-cpu me-1"></i>Procesar OCR local`; }
  }
}

async function procesarComparacionAhora(mesa, eleccion) {
  const btn = document.querySelector(`button[onclick*="procesarComparacionAhora"]`);
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Comparando...`; }

  try {
    const res  = await fetch("/api/comparar", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ codigo_mesa: mesa, id_eleccion: eleccion }),
    });
    const json = await res.json();
    if (json.ok) {
      toast("Comparación guardada", "ok");
      offcanvasInst?.hide();
      cargarResultados(currentPage);
    } else {
      throw new Error(json.error || "Error comparación");
    }
  } catch (e) {
    toast("Error: " + e.message, "err");
    if (btn) { btn.disabled = false; btn.innerHTML = `<i class="bi bi-arrows-expand"></i> Comparar ahora`; }
  }
}

// ── Gráficas ──────────────────────────────────────────────────────────────────

async function cargarCharts() {
  try {
    const res  = await fetch("/api/resultados/charts");
    const json = await res.json();
    if (!json.ok) return;
    const d = json.data;

    for (const c of Object.values(charts)) c.destroy();
    charts = {};

    const chartOpts = {
      plugins: {
        legend: { labels: { color: "#8b949e", boxWidth: 12 } },
      },
      responsive: true,
      maintainAspectRatio: true,
    };

    // ── Chart 1: Tipo documento (Donut)
    const ctx1 = document.getElementById("chartTipoDoc");
    if (ctx1) {
      charts.tipoDoc = new Chart(ctx1, {
        type: "doughnut",
        data: {
          labels:   ["Digital", "Escaneado", "Sin OCR"],
          datasets: [{
            data:            [d.tipo_documento.digital, d.tipo_documento.escaneado, d.tipo_documento.sin_ocr],
            backgroundColor: ["#1f6feb", "#e3b341", "#30363d"],
            borderColor:     ["#388bfd", "#ffa657", "#8b949e"],
            borderWidth: 2,
          }],
        },
        options: {
          ...chartOpts,
          plugins: {
            ...chartOpts.plugins,
            title: { display: true, text: "Tipo de documento", color: "#c9d1d9", font: { size: 13 } },
          },
        },
      });
    }

    // ── Chart 2: Anomalías por tipo (Bar apilado)
    const ctx2 = document.getElementById("chartAnomalias");
    if (ctx2 && d.anomalias_por_tipo.length) {
      charts.anomalias = new Chart(ctx2, {
        type: "bar",
        data: {
          labels:   d.anomalias_por_tipo.map(r => r.tipo_eleccion),
          datasets: [
            {
              label:           "Normal",
              data:            d.anomalias_por_tipo.map(r => r.total - r.con_anomalia),
              backgroundColor: "#238636",
              borderRadius:    3,
            },
            {
              label:           "Con anomalía",
              data:            d.anomalias_por_tipo.map(r => r.con_anomalia),
              backgroundColor: "#da3633",
              borderRadius:    3,
            },
          ],
        },
        options: {
          ...chartOpts,
          indexAxis: "y",
          scales: {
            x: { stacked: true, ticks: { color: "#8b949e" }, grid: { color: "#21262d" } },
            y: { stacked: true, ticks: { color: "#8b949e" }, grid: { color: "#21262d" } },
          },
          plugins: {
            ...chartOpts.plugins,
            title: { display: true, text: "Anomalías por tipo de elección", color: "#c9d1d9", font: { size: 13 } },
          },
        },
      });
    } else if (ctx2) {
      ctx2.parentElement.innerHTML = `<div class="text-muted small text-center py-4">Sin datos forenses aún</div>`;
    }

    // ── Chart 3: Discrepancias (Donut)
    const ctx3 = document.getElementById("chartDiscrepancias");
    if (ctx3) {
      const total3 = d.discrepancias.alerta + d.discrepancias.ok;
      if (total3 === 0) {
        ctx3.parentElement.innerHTML = `<div class="text-muted small text-center py-4">Sin comparaciones realizadas</div>`;
      } else {
        charts.discrepancias = new Chart(ctx3, {
          type: "doughnut",
          data: {
            labels:   ["Con discrepancia", "Sin discrepancia"],
            datasets: [{
              data:            [d.discrepancias.alerta, d.discrepancias.ok],
              backgroundColor: ["#e3b341", "#238636"],
              borderColor:     ["#ffa657", "#56d364"],
              borderWidth: 2,
            }],
          },
          options: {
            ...chartOpts,
            plugins: {
              ...chartOpts.plugins,
              title: { display: true, text: "Discrepancias API vs OCR", color: "#c9d1d9", font: { size: 13 } },
            },
          },
        });
      }
    }

  } catch (e) {
    console.error("Error cargando charts:", e);
  }
}

// ── Export CSV ────────────────────────────────────────────────────────────────

async function exportarCSV() {
  const mesa   = document.getElementById("filtroBuscar").value.trim();
  const filtro = document.getElementById("filtroTipo").value;

  toast("Exportando datos…", "ok");

  try {
    const params = new URLSearchParams({ page: 1, limit: 10000, filtro });
    if (mesa) params.set("mesa", mesa);

    const res  = await fetch(`/api/resultados/actas?${params}`);
    const json = await res.json();
    if (!json.ok) throw new Error(json.error);

    const actas = json.data.actas;
    const headers = [
      "Mesa", "ID Elección", "Tipo Elección", "Estado",
      "Tiene PDF", "Tiene OCR", "Es Escaneado", "Método OCR",
      "Tiene Forense", "Anomalía Forense", "Anomalía SVM", "Score SVM", "Confianza SVM",
      "Estado Comparación", "Nro Discrepancias", "Fecha Descarga",
    ];

    const rows = actas.map(a => [
      a.codigo_mesa,
      a.id_eleccion,
      a.tipo_eleccion || "",
      a.estado || "",
      a.tiene_pdf   ? "SI" : "NO",
      a.tiene_ocr   ? "SI" : "NO",
      a.es_escaneado === 1 ? "ESCANEADO" : a.es_escaneado === 0 ? "DIGITAL" : "—",
      a.metodo_ocr  || "—",
      a.tiene_forense ? "SI" : "NO",
      a.anomalia_forense === 1 ? "SI" : a.anomalia_forense === 0 ? "NO" : "—",
      a.is_anomaly_svm === 1 ? "SI" : a.is_anomaly_svm === 0 ? "NO" : "—",
      a.score_svm      ?? "—",
      a.confidence_svm || "—",
      a.comp_estado    || "—",
      Array.isArray(a.discrepancias_json) ? a.discrepancias_json.length : 0,
      a.fecha_descarga || "—",
    ]);

    const csv  = [headers, ...rows]
      .map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(","))
      .join("\n");
    const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href = url;
    a.download = `resultados_actas_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    toast(`${actas.length} registros exportados`, "ok");

  } catch (e) {
    toast("Error al exportar: " + e.message, "err");
  }
}

// ── Refresh ───────────────────────────────────────────────────────────────────

async function refrescar() {
  const btn = document.getElementById("btnRefrescar");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm"></span>`;
  }
  await Promise.all([cargarResultados(currentPage), cargarCharts()]);
  if (btn) {
    btn.disabled = false;
    btn.innerHTML = `<i class="bi bi-arrow-clockwise"></i> Actualizar`;
  }
}

// ── Debounce búsqueda ─────────────────────────────────────────────────────────

function debounceBuscar() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => cargarResultados(1), 380);
}

// ── Toast ─────────────────────────────────────────────────────────────────────

function toast(msg, type = "ok") {
  const zone = document.getElementById("toastZone");
  const el   = document.createElement("div");
  el.className = `res-toast ${type}`;
  el.innerHTML = `<i class="bi ${type === "ok" ? "bi-check-circle" : "bi-exclamation-triangle"} me-2"></i>${escHtml(msg)}`;
  zone.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Init ──────────────────────────────────────────────────────────────────────

async function _iniciarResultados() {
  // Cargar modo comparación desde la config guardada antes de cualquier render
  try {
    const r = await fetch("/api/auto/config");
    if (r.ok) {
      const json = await r.json();
      if (json.ok && json.data?.comparacion_modo) {
        window._comparacionModo = json.data.comparacion_modo;
      }
    }
  } catch (_) { /* sin config → simple */ }

  cargarResultados(1);
  cargarCharts();
}

document.addEventListener("DOMContentLoaded", () => {
  _iniciarResultados();

  // Chevron charts
  const sec = document.getElementById("chartsSection");
  if (sec) {
    sec.addEventListener("show.bs.collapse",
      () => (document.getElementById("chartsChevron").className = "bi bi-chevron-up small text-muted"));
    sec.addEventListener("hide.bs.collapse",
      () => (document.getElementById("chartsChevron").className = "bi bi-chevron-down small text-muted"));
  }
});
