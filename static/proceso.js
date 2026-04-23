/* ─────────────────────────────────────────────────────────────────────────────
   proceso.js  —  Motor de ejecución en tiempo real para /proceso
   ─────────────────────────────────────────────────────────────────────────── */

// ── Estado global ─────────────────────────────────────────────────────────────
let jobId        = null;
let pollTimer    = null;
let prevProgreso = 0;
let prevTs       = null;
let velocidadBuf = [];     // últimos N valores velocidad/min para el chart
let vaciosBuf    = [];     // últimos N valores vacíos consecutivos
let tiemposBuf   = [];     // etiquetas de tiempo para los charts
let pausado      = false;
let _lastLogSeq  = 0;      // último seq de log ya mostrado en UI

// Parámetros del URL
const _p = new URLSearchParams(window.location.search);
const CFG = {
  inicio:         _p.get("inicio")         || "000001",
  fin:            _p.get("fin")            || "999999",
  rate:           parseFloat(_p.get("rate")   || "0.5"),
  hilos:          parseInt(_p.get("hilos")    || "3"),
  vacios:         parseInt(_p.get("vacios")   || "200"),
  pdf:            _p.get("pdf")            === "1",
  ocr:            _p.get("ocr")            === "1",
  forense:        _p.get("forense")        === "1",
  modo:           _p.get("modo")           || "rango",
  solo_descarga:  _p.get("solo_descarga")  === "1",
  carpeta:        _p.get("carpeta")        || null,
};

// ── Charts ────────────────────────────────────────────────────────────────────
let chartEstados   = null;
let chartVelocidad = null;
let chartVacios    = null;

function initCharts() {
  chartEstados = new Chart(document.getElementById("chartEstados"), {
    type: "doughnut",
    data: {
      labels: ["Con PDF","Sin PDF","No existe","JEE","Error acta","Error API","Observada"],
      datasets: [{ data: [0,0,0,0,0,0,0],
        backgroundColor: ["#198754","#0dcaf0","#6c757d","#ffc107","#dc3545","#dc3545","#fd7e14"],
        borderWidth: 2, borderColor: "#212529" }]
    },
    options: {
      responsive: true,
      plugins: { legend: { position: "bottom", labels: { color: "#adb5bd", font: { size: 11 } } } }
    }
  });

  chartVelocidad = new Chart(document.getElementById("chartVelocidad"), {
    type: "line",
    data: {
      labels: [],
      datasets: [{
        label: "Mesas/min",
        data: [],
        borderColor: "#0d6efd",
        backgroundColor: "rgba(13,110,253,0.15)",
        tension: 0.3, fill: true, pointRadius: 2,
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#6c757d", maxTicksLimit: 6 }, grid: { color: "#2d3035" } },
        y: { beginAtZero: true, ticks: { color: "#6c757d" }, grid: { color: "#2d3035" } }
      }
    }
  });

  chartVacios = new Chart(document.getElementById("chartVacios"), {
    type: "bar",
    data: {
      labels: [],
      datasets: [{
        label: "Vacíos consec.",
        data: [],
        backgroundColor: vaciosBuf.map(v => v > CFG.vacios * 0.8 ? "#dc3545" : "#ffc107"),
        borderWidth: 0,
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#6c757d", maxTicksLimit: 8 }, grid: { color: "#2d3035" } },
        y: { beginAtZero: true, max: CFG.vacios,
             ticks: { color: "#6c757d" }, grid: { color: "#2d3035" } }
      }
    }
  });
}

// ── Log console ───────────────────────────────────────────────────────────────

function agregarLog(tipo, msg, mesa) {
  const console_ = document.getElementById("logConsole");
  if (!console_) return;
  const ts = new Date().toLocaleTimeString("es-PE", { hour12: false });
  const clsMap = {
    ok:      "log-ok",
    warn:    "log-warn",
    error:   "log-error",
    info:    "log-info",
    system:  "log-system",
  };
  const icMap = {
    ok:     "✓",
    warn:   "⚠",
    error:  "✗",
    info:   "ℹ",
    system: "⚙",
  };
  const mesaStr = mesa ? `<span class="log-mesa">${String(mesa).padStart(6,"0")}</span>` : "";
  const line = `<div class="log-line ${clsMap[tipo] || ""}">`
             + `<span class="log-ts">${ts}</span>`
             + `<span class="log-icon">${icMap[tipo] || "·"}</span>`
             + `${mesaStr}<span class="log-msg">${escHtml(msg)}</span>`
             + `</div>`;
  console_.insertAdjacentHTML("beforeend", line);
  const autoScroll = document.getElementById("autoScroll");
  if (!autoScroll || autoScroll.checked) {
    console_.scrollTop = console_.scrollHeight;
  }
  // Limitar a 2000 líneas
  const lineas = console_.querySelectorAll(".log-line");
  if (lineas.length > 2000) {
    for (let i = 0; i < 50; i++) lineas[i]?.remove();
  }
}

function limpiarLog() {
  const c = document.getElementById("logConsole");
  if (c) c.innerHTML = "";
  agregarLog("system", "Log limpiado");
}

function escHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

// ── Iniciar job ───────────────────────────────────────────────────────────────

async function iniciarProcesoAuto() {
  agregarLog("system", `Iniciando barrido: ${CFG.inicio} → ${CFG.fin} (modo=${CFG.modo})`);

  // Leer el motor OCR configurado actualmente en el servidor
  let ocrEngine = "local";
  try {
    const cfgR = await fetch("/api/auto/config");
    const cfgJ = await cfgR.json();
    if (cfgJ.ok) ocrEngine = cfgJ.data.ocr_engine || "local";
  } catch (_) {}

  const body = {
    rango_inicio:            parseInt(CFG.inicio.replace(/\D/g,"")) || 1,
    rango_fin:               parseInt(CFG.fin.replace(/\D/g,""))    || 999999,
    ocr_engine:              ocrEngine,
    concurrencia:            CFG.hilos,
    delay_segundos:          CFG.rate,
    descargar_pdf:           CFG.pdf,
    ejecutar_ocr:            CFG.ocr,
    ejecutar_forense:        CFG.forense,
    max_vacios_consecutivos: CFG.vacios,
    carpeta_destino:         CFG.carpeta || null,
  };

  try {
    const res = await fetch("/api/auto/barrido/iniciar", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(body),
    });
    const r = await res.json();
    if (r.ok) {
      jobId = r.data.job_id;
      document.getElementById("jobIdLabel").textContent = `ID: ${jobId}`;
      agregarLog("ok", `Job creado: ${jobId}`);
      setStatus("ejecutando");
      pollTimer = setInterval(pollEstado, 2000);
    } else {
      agregarLog("error", `Error al iniciar: ${r.error || "Error desconocido"}`);
      setStatus("error");
    }
  } catch (e) {
    agregarLog("error", `Error de red: ${e.message}`);
    setStatus("error");
  }
}

// ── Poll estado ───────────────────────────────────────────────────────────────

async function pollEstado() {
  if (!jobId) return;
  try {
    const res = await fetch(`/api/auto/barrido/estado/${jobId}`);
    const r   = await res.json();
    if (!r.ok) return;
    const d = r.data;
    actualizarMetricas(d);
    actualizarCharts(d);
    generarLogDesdeEstado(d);

    if (["completado","cancelado","error"].includes(d.estado)) {
      clearInterval(pollTimer);
      pollTimer = null;
      setStatus(d.estado);
      mostrarResumenFinal(d);
    }
  } catch (e) {
    agregarLog("warn", `Fallo de polling: ${e.message}`);
  }
}

// ── Actualizar métricas ───────────────────────────────────────────────────────

let _prevStats   = null;
let _prevMesaAct = 0;

function actualizarMetricas(d) {
  const st  = d.stats || {};
  const pct = d.porcentaje || 0;

  // Progreso bar
  document.getElementById("progressBar").style.width = pct + "%";
  document.getElementById("progressBar").textContent = pct.toFixed(1) + "%";

  // Texto progreso
  const mesaStr = d.mesa_actual ? String(d.mesa_actual).padStart(6,"0") : "—";
  document.getElementById("progresoTexto").textContent =
    `Mesa ${mesaStr}  ·  ${(d.progreso||0).toLocaleString()} / ${(d.total_mesas||0).toLocaleString()} procesadas`;

  // ETA + contador
  if (d.eta_seg) {
    const h = Math.floor(d.eta_seg / 3600);
    const m = Math.floor((d.eta_seg % 3600) / 60);
    const s = d.eta_seg % 60;
    document.getElementById("progresoETA").textContent =
      h > 0 ? `ETA ${h}h ${m}m` : m > 0 ? `ETA ${m}m ${s}s` : `ETA ${s}s`;
  }
  document.getElementById("progresoContador").textContent =
    `${pct.toFixed(1)}%`;

  // Métricas individuales
  document.getElementById("mTotal").textContent   = (d.progreso||0).toLocaleString();
  document.getElementById("mConPdf").textContent  = (st.EXISTE_CON_PDF   || 0).toLocaleString();
  document.getElementById("mSinPdf").textContent  = (st.EXISTE_SIN_PDF   || 0).toLocaleString();
  document.getElementById("mNoExiste").textContent= (st.NO_EXISTE         || 0).toLocaleString();
  document.getElementById("mJee").textContent     = (st.PARA_JEE          || 0).toLocaleString();
  document.getElementById("mError").textContent   = ((st.CON_ERROR_ACTA || 0) + (st.ERROR_API || 0)).toLocaleString();

  const vc  = d.vacios_consecutivos || 0;
  const mvc = d.max_vacios_consecutivos || CFG.vacios;
  document.getElementById("mVacios").textContent    = vc.toLocaleString();
  document.getElementById("mVaciosMax").textContent = `/ ${mvc}`;
  document.getElementById("mVacios").style.color    = vc > mvc * 0.7 ? "#dc3545" : vc > mvc * 0.4 ? "#ffc107" : "";

  // Velocidad (mesas/min)
  const now = Date.now();
  if (prevTs) {
    const dt = (now - prevTs) / 1000 / 60;
    const dp = (d.progreso || 0) - prevProgreso;
    if (dt > 0 && dp >= 0) {
      const vel = Math.round(dp / dt);
      document.getElementById("mVelocidad").textContent  = vel.toLocaleString();
      document.getElementById("cfgVelocidad").textContent = vel + "/min";
      velocidadBuf.push(vel);
      vaciosBuf.push(vc);
      tiemposBuf.push(new Date().toLocaleTimeString("es-PE",{hour12:false,hour:"2-digit",minute:"2-digit",second:"2-digit"}));
      if (velocidadBuf.length > 60) {
        velocidadBuf.shift(); vaciosBuf.shift(); tiemposBuf.shift();
      }
    }
  }
  prevTs       = now;
  prevProgreso = d.progreso || 0;
  _prevStats   = st;
}

// ── Actualizar charts ─────────────────────────────────────────────────────────

function actualizarCharts(d) {
  const st = d.stats || {};

  // Doughnut: distribución
  if (chartEstados) {
    chartEstados.data.datasets[0].data = [
      st.EXISTE_CON_PDF   || 0,
      st.EXISTE_SIN_PDF   || 0,
      st.NO_EXISTE        || 0,
      st.PARA_JEE         || 0,
      st.CON_ERROR_ACTA   || 0,
      st.ERROR_API        || 0,
      st.OBSERVADA        || 0,
    ];
    chartEstados.update("none");
  }

  // Line: velocidad
  if (chartVelocidad && velocidadBuf.length) {
    chartVelocidad.data.labels   = [...tiemposBuf];
    chartVelocidad.data.datasets[0].data = [...velocidadBuf];
    chartVelocidad.update("none");
  }

  // Bar: vacíos
  if (chartVacios && vaciosBuf.length) {
    chartVacios.data.labels  = [...tiemposBuf];
    chartVacios.data.datasets[0].data = [...vaciosBuf];
    chartVacios.data.datasets[0].backgroundColor = vaciosBuf.map(
      v => v > CFG.vacios * 0.8 ? "#dc3545" : v > CFG.vacios * 0.5 ? "#ffc107" : "#198754"
    );
    chartVacios.update("none");
  }
}

// ── Log generado desde diff de estado ────────────────────────────────────────

function generarLogDesdeEstado(d) {
  // Mostrar nuevas entradas del buffer de logs del backend
  const logs = d.log_recientes;
  if (Array.isArray(logs) && logs.length > 0) {
    for (const entry of logs) {
      if (entry.seq <= _lastLogSeq) continue;
      _lastLogSeq = entry.seq;
      agregarLog(entry.tipo || "info", entry.msg || "", entry.mesa || null);
    }
  }

  // Fallback: si no hay log_recientes, loguear cambios de estado globales
  if (!Array.isArray(logs) || logs.length === 0) {
    if (d.estado === "completado") {
      agregarLog("ok", "Barrido completado exitosamente");
    } else if (d.estado === "cancelado") {
      agregarLog("warn", "Barrido cancelado por el usuario");
    } else if (d.estado === "error") {
      agregarLog("error", `Barrido terminó con error: ${d.ultimo_error || ""}`);
    }
  }
}

// ── Controles ─────────────────────────────────────────────────────────────────

async function pausarJob() {
  if (!jobId) return;
  const r = await apiPost(`/api/auto/barrido/pausar/${jobId}`, {});
  if (r.ok) {
    clearInterval(pollTimer); pollTimer = null; pausado = true;
    document.getElementById("btnPausar").classList.add("d-none");
    document.getElementById("btnReanudar").classList.remove("d-none");
    setStatus("pausado");
    agregarLog("system", "Barrido pausado");
  } else {
    agregarLog("error", `No se pudo pausar: ${r.error}`);
  }
}

async function reanudarJob() {
  if (!jobId) return;
  const r = await apiPost(`/api/auto/barrido/reanudar/${jobId}`, {});
  if (r.ok) {
    document.getElementById("btnReanudar").classList.add("d-none");
    document.getElementById("btnPausar").classList.remove("d-none");
    pausado = false;
    pollTimer = setInterval(pollEstado, 2000);
    setStatus("ejecutando");
    agregarLog("system", "Barrido reanudado");
  } else {
    agregarLog("error", `No se pudo reanudar: ${r.error}`);
  }
}

async function detenerJob() {
  if (!jobId) return;
  if (!confirm("¿Detener el barrido en curso? Se guardará el progreso.")) return;
  const r = await apiPost(`/api/auto/barrido/cancelar/${jobId}`, {});
  if (r.ok) {
    clearInterval(pollTimer); pollTimer = null;
    setStatus("cancelado");
    agregarLog("warn", "Barrido detenido por el usuario");
    document.getElementById("btnPausar").classList.add("d-none");
    document.getElementById("btnReanudar").classList.add("d-none");
    document.getElementById("btnDetener").disabled = true;
  }
}

function volverInicio() {
  window.location.href = "/";
}

// ── UI helpers ────────────────────────────────────────────────────────────────

function setStatus(estado) {
  const badge = document.getElementById("jobStatusBadge");
  const map = {
    ejecutando: ["primary",  "bi-play-circle-fill",     "Ejecutando"],
    pausado:    ["warning",  "bi-pause-circle-fill",    "Pausado"],
    completado: ["success",  "bi-check-circle-fill",    "Completado"],
    cancelado:  ["secondary","bi-stop-circle-fill",     "Cancelado"],
    error:      ["danger",   "bi-exclamation-circle",   "Error"],
    iniciando:  ["info",     "bi-hourglass-split",      "Iniciando…"],
  };
  const [color, icon, texto] = map[estado] || ["secondary","bi-question","?"];
  badge.className = `badge bg-${color} fs-6 px-3 py-2`;
  badge.innerHTML = `<i class="bi ${icon}"></i> ${texto}`;

  // Ajustar barra de progreso
  const bar = document.getElementById("progressBar");
  if (estado === "completado") {
    bar.classList.remove("progress-bar-animated", "progress-bar-striped");
    bar.classList.add("bg-success");
  } else if (estado === "error") {
    bar.classList.remove("progress-bar-animated");
    bar.classList.add("bg-danger");
  } else if (estado === "cancelado") {
    bar.classList.remove("progress-bar-animated");
    bar.classList.add("bg-warning");
  }
}

// ── Resumen final ─────────────────────────────────────────────────────────────

function mostrarResumenFinal(d) {
  const st    = d.stats || {};
  const total = d.progreso || 0;
  const enc   = (st.EXISTE_CON_PDF||0) + (st.EXISTE_SIN_PDF||0) + (st.PARA_JEE||0) + (st.CON_ERROR_ACTA||0);
  const pct   = total > 0 ? ((enc / total) * 100).toFixed(1) : "0";
  const rango = `${CFG.inicio} → ${CFG.fin}`;

  const esOk    = d.estado === "completado";
  const color   = esOk ? "success" : d.estado === "cancelado" ? "warning" : "danger";
  const iconH   = esOk ? "bi-check-circle-fill" : d.estado === "cancelado" ? "bi-stop-circle-fill" : "bi-exclamation-circle";
  const titulo  = esOk ? "Barrido completado" : d.estado === "cancelado" ? "Barrido cancelado" : "Barrido con error";

  document.getElementById("resumenHeader").className = `modal-header bg-${color} text-${esOk?"white":"dark"}`;
  document.getElementById("resumenTitulo").innerHTML = `<i class="bi ${iconH}"></i> ${titulo}`;
  document.getElementById("resumenBody").innerHTML = `
    <div class="row g-3 text-center">
      <div class="col-4">
        <div class="border rounded p-3">
          <div class="fs-3 fw-bold">${total.toLocaleString()}</div>
          <div class="text-muted small">Mesas procesadas</div>
        </div>
      </div>
      <div class="col-4">
        <div class="border rounded p-3 border-success">
          <div class="fs-3 fw-bold text-success">${enc.toLocaleString()}</div>
          <div class="text-muted small">Mesas con datos</div>
        </div>
      </div>
      <div class="col-4">
        <div class="border rounded p-3">
          <div class="fs-3 fw-bold text-info">${pct}%</div>
          <div class="text-muted small">Tasa de éxito</div>
        </div>
      </div>
    </div>
    <hr>
    <div class="row g-2 small">
      <div class="col-6"><b>Rango:</b> ${escHtml(rango)}</div>
      <div class="col-6"><b>Con PDF:</b> ${(st.EXISTE_CON_PDF||0).toLocaleString()}</div>
      <div class="col-6"><b>Sin PDF:</b> ${(st.EXISTE_SIN_PDF||0).toLocaleString()}</div>
      <div class="col-6"><b>JEE:</b> <span class="text-warning fw-bold">${(st.PARA_JEE||0).toLocaleString()}</span></div>
      <div class="col-6"><b>No existe:</b> ${(st.NO_EXISTE||0).toLocaleString()}</div>
      <div class="col-6"><b>Errores:</b> <span class="text-danger">${((st.CON_ERROR_ACTA||0)+(st.ERROR_API||0)).toLocaleString()}</span></div>
    </div>`;

  // Mostrar modal automáticamente
  const modal = new bootstrap.Modal(document.getElementById("modalResumen"));
  modal.show();
}

// ── Helpers API ───────────────────────────────────────────────────────────────

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

// ── DOMContentLoaded ─────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  // Rellenar config summary
  document.getElementById("cfgRango").textContent    = `${CFG.inicio} → ${CFG.fin}`;
  document.getElementById("cfgDelay").textContent    = `${CFG.rate}s`;
  document.getElementById("cfgHilos").textContent    = CFG.hilos;
  document.getElementById("cfgMaxVacios").textContent= CFG.vacios;
  document.getElementById("cfgInicio").textContent   = new Date().toLocaleTimeString("es-PE");

  // Inicializar charts
  initCharts();

  // Log inicial
  agregarLog("system", `Modo: ${CFG.modo} | Rango: ${CFG.inicio} → ${CFG.fin}`);
  agregarLog("system", `Hilos: ${CFG.hilos} | Delay: ${CFG.rate}s | Max vacíos: ${CFG.vacios}`);
  if (CFG.solo_descarga) {
    agregarLog("system", `Modo descarga masiva — solo PDFs → carpeta: data/${CFG.carpeta || "dataset"}/`);
  } else {
    agregarLog("system", `Opciones: PDF=${CFG.pdf?"SÍ":"NO"} | OCR=${CFG.ocr?"SÍ":"NO"} | Forense=${CFG.forense?"SÍ":"NO"}`);
  }
  agregarLog("system", "Conectando con servidor…");

  setStatus("iniciando");

  // Auto-iniciar tras 800ms (para que el DOM esté listo)
  setTimeout(iniciarProcesoAuto, 800);
});
