/**
 * auditoria.js — Sistema de Auditoría Electoral ONPE 2026
 * Gráficas y análisis de anomalías electorales.
 */

"use strict";

// ── Estado global ─────────────────────────────────────────────────────────────
const _charts = {};
let _eleccionActual = 10;
let _datosTimeline  = null;
let _datosPartido   = null;
let _datosResGeneral= null;
let _datosIncons    = null;
let _datosAnomaP    = null;
let _datosMesasSos  = null;

// ── Paleta de colores ─────────────────────────────────────────────────────────
const C = {
  red:    "#f85149",
  orange: "#ffa657",
  yellow: "#d29922",
  green:  "#3fb950",
  blue:   "#58a6ff",
  purple: "#bc8cff",
  teal:   "#39d353",
  gray:   "#8b949e",
  darkBg: "#161b22",
  grid:   "#21262d",
};

// Paleta extendida para partidos
const PARTY_COLORS = [
  "#58a6ff","#f85149","#3fb950","#ffa657","#bc8cff",
  "#39d353","#d29922","#79c0ff","#ff7b72","#56d364",
  "#e3b341","#d2a8ff","#7ee787","#ffa198","#cae8ff",
];

// ── Chart.js defaults ─────────────────────────────────────────────────────────
Chart.defaults.color = "#8b949e";
Chart.defaults.borderColor = "#21262d";
Chart.defaults.font.family = "'Segoe UI', system-ui, sans-serif";
Chart.defaults.font.size   = 11;

function _chartBase(id) {
  const el = document.getElementById(id);
  if (_charts[id]) { _charts[id].destroy(); delete _charts[id]; }
  return el ? el.getContext("2d") : null;
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function _esc(s) {
  return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function toast(msg, tipo = "info") {
  const colors = { info: "bg-info", ok: "bg-success", err: "bg-danger", warn: "bg-warning" };
  const c = document.getElementById("toastContainer");
  if (!c) return;
  const div = document.createElement("div");
  div.className = `alert ${colors[tipo] || "bg-secondary"} text-white py-2 px-3 mb-2 small shadow`;
  div.style.cssText = "min-width:220px;border-radius:6px;animation:fadeIn .3s";
  div.textContent = msg;
  c.appendChild(div);
  setTimeout(() => div.remove(), 4000);
}

async function _fetch(url) {
  const r = await fetch(url);
  const j = await r.json();
  if (!j.ok) throw new Error(j.error || "Error API");
  return j.data;
}

async function _post(url, body = {}) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = await r.json();
  if (!j.ok) throw new Error(j.error || "Error API");
  return j.data;
}

// ── Carga inicial ─────────────────────────────────────────────────────────────
async function cargarTodo() {
  await Promise.all([
    cargarResumen(),
    cargarTimeline(),
    cargarVotosPartido(),
    cargarResumenGeneral(),
    cargarInconsistencias(),
    cargarAnomaliaPartido(),
    cargarMesasSospechosas(),
    cargarParticipacion(),
  ]);
}

// ── RESUMEN ESTADÍSTICO ───────────────────────────────────────────────────────
async function cargarResumen() {
  try {
    const d = await _fetch("/api/auditoria/resumen");
    renderStatCards(d);
    document.getElementById("badgeAnomalias").textContent =
      (d.actas_proceso_rapido + d.inconsistencias_suma);
  } catch (e) {
    document.getElementById("statsLoading").textContent = "Error: " + e.message;
  }
}

function renderStatCards(d) {
  const container = document.getElementById("statCards");
  document.getElementById("statsLoading").remove?.();

  const cards = [
    { num: d.total_actas_analizadas, lbl: "Actas en DB",       color: C.blue,   icon: "bi-file-earmark-text" },
    { num: d.con_timeline,           lbl: "Con timeline API",  color: C.teal,   icon: "bi-clock-history" },
    { num: d.dia_votacion_12,        lbl: "Actas día 12 abr",  color: C.green,  icon: "bi-calendar-check" },
    { num: d.dia_votacion_13,        lbl: "Actas día 13 abr",  color: C.blue,   icon: "bi-calendar-check" },
    { num: d.inconsistencias_suma,   lbl: "Sumas incorrectas", color: C.red,    icon: "bi-calculator" },
    { num: d.actas_proceso_rapido,   lbl: "Proceso <1.5 min",  color: C.orange, icon: "bi-lightning-charge" },
  ];

  container.innerHTML = cards.map(c => `
    <div class="col-xl-2 col-lg-3 col-md-4 col-sm-6">
      <div class="stat-card text-center">
        <div class="num" style="color:${c.color}">${c.num.toLocaleString()}</div>
        <div class="lbl"><i class="bi ${c.icon} me-1"></i>${c.lbl}</div>
      </div>
    </div>`).join("");
}

// ── TIMELINE ─────────────────────────────────────────────────────────────────
async function cargarTimeline() {
  try {
    _datosTimeline = await _fetch("/api/auditoria/timeline");
    renderTimelineCharts(_datosTimeline);
  } catch (e) {
    toast("Error timeline: " + e.message, "err");
  }
}

function renderTimelineCharts(d) {
  // 1. Pie: día 12 vs 13 vs otro
  const ctxPie = _chartBase("chartDiaVotacion");
  if (ctxPie) {
    const dia12 = d.por_dia["12"] || 0;
    const dia13 = d.por_dia["13"] || 0;
    const otro  = d.por_dia["otro"] || 0;
    _charts["chartDiaVotacion"] = new Chart(ctxPie, {
      type: "doughnut",
      data: {
        labels: ["Día 12 abr", "Día 13 abr", "Fecha desconocida"],
        datasets: [{
          data:            [dia12, dia13, otro],
          backgroundColor: [C.green, C.blue, C.gray],
          borderWidth:     2,
          borderColor:     C.darkBg,
        }],
      },
      options: {
        plugins: {
          legend: { position: "bottom", labels: { font: { size: 10 } } },
        },
        cutout: "60%",
      },
    });
    document.getElementById("lblDiaVotacion").innerHTML =
      `<span class="text-success">Día 12: <b>${dia12}</b></span> · 
       <span class="text-info">Día 13: <b>${dia13}</b></span> · 
       <span class="text-secondary">Desconocido: <b>${otro}</b></span>`;
  }

  // 2. Bar: distribución por hora (día 12 en verde, día 13 en azul)
  const ctxHora = _chartBase("chartHoraDigitalizacion");
  if (ctxHora) {
    const horas   = Array.from({length:24}, (_, i) => `${i}:00`);
    const por12   = Array.from({length:24}, (_, i) => d.acumulado_dia12?.[String(i)] || 0);
    const por13   = Array.from({length:24}, (_, i) => d.acumulado_dia13?.[String(i)] || 0);
    const porHora = Array.from({length:24}, (_, i) => d.por_hora_digitalizacion?.[String(i)] || 0);

    _charts["chartHoraDigitalizacion"] = new Chart(ctxHora, {
      type: "bar",
      data: {
        labels: horas,
        datasets: [
          { label: "Día 12",   data: por12,   backgroundColor: C.green + "cc" },
          { label: "Día 13",   data: por13,   backgroundColor: C.blue  + "cc" },
        ],
      },
      options: {
        plugins: { legend: { position: "bottom", labels: { font: { size: 10 } } } },
        scales: {
          x: { stacked: true, grid: { color: C.grid } },
          y: { stacked: true, grid: { color: C.grid }, ticks: { maxTicksLimit: 5 } },
        },
        animation: false,
      },
    });
  }

  // 3. Histograma de velocidad
  const ctxVel = _chartBase("chartVelocidad");
  const hist = d.velocidad_histograma || [];
  if (ctxVel && hist.length > 0) {
    const labels = hist.map(h => `${h.desde}-${h.hasta}m`);
    const data   = hist.map(h => h.count);
    const colors = hist.map(h => h.desde < 5 ? C.red : h.desde < 30 ? C.orange : C.green);
    _charts["chartVelocidad"] = new Chart(ctxVel, {
      type: "bar",
      data: {
        labels,
        datasets: [{ label: "Actas", data, backgroundColor: colors, borderWidth: 0 }],
      },
      options: {
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { color: C.grid } },
          y: { grid: { color: C.grid }, ticks: { maxTicksLimit: 4 } },
        },
        animation: false,
      },
    });
    document.getElementById("lblVelocidad").innerHTML =
      `Media: <b>${d.velocidad_media ?? "—"}m</b> · Mediana: <b>${d.velocidad_mediana ?? "—"}m</b> · 
       Min: <b>${d.velocidad_min ?? "—"}m</b> · Max: <b>${d.velocidad_max ?? "—"}m</b>`;
  }

  // 4. Stacked bar: tipo elección / día
  const ctxTipo = _chartBase("chartTipoEleccionDia");
  if (ctxTipo) {
    const tipos = Object.keys(d.por_tipo_dia || {});
    const dia12v = tipos.map(t => d.por_tipo_dia[t].dia12 || 0);
    const dia13v = tipos.map(t => d.por_tipo_dia[t].dia13 || 0);
    const otrv   = tipos.map(t => d.por_tipo_dia[t].otro  || 0);
    const labelsTrunc = tipos.map(t => t.length > 20 ? t.substring(0, 18) + "…" : t);
    _charts["chartTipoEleccionDia"] = new Chart(ctxTipo, {
      type: "bar",
      data: {
        labels: labelsTrunc,
        datasets: [
          { label: "Día 12", data: dia12v, backgroundColor: C.green + "cc" },
          { label: "Día 13", data: dia13v, backgroundColor: C.blue  + "cc" },
          { label: "Descon.", data: otrv,  backgroundColor: C.gray  + "88" },
        ],
      },
      options: {
        plugins: { legend: { position: "bottom", labels: { font: { size: 10 } } } },
        scales: {
          x: { stacked: true, grid: { color: C.grid } },
          y: { stacked: true, grid: { color: C.grid }, ticks: { maxTicksLimit: 5 } },
        },
        animation: false,
      },
    });
  }

  // 5. Tabla de anomalías timeline
  const anom = d.anomalias || [];
  document.getElementById("badgeTimelineAnomalias").textContent = anom.length;
  const tbl = document.getElementById("tablaTimelineAnomalias");
  if (!anom.length) {
    tbl.innerHTML = `<div class="text-success text-center py-3 small"><i class="bi bi-check-circle me-1"></i>Sin anomalías de timeline detectadas.</div>`;
  } else {
    tbl.innerHTML = `
      <table class="table table-dark table-sm table-hover mb-0">
        <thead><tr>
          <th>Mesa</th><th>Elección</th><th>Categoría</th><th>Motivo</th><th>Sev.</th>
        </tr></thead>
        <tbody>
          ${anom.map(a => `
            <tr>
              <td class="font-monospace text-info">${_esc(a.mesa)}</td>
              <td class="small text-muted">${_esc(a.tipo)}</td>
              <td><span class="badge bg-secondary">${_esc(a.categoria)}</span></td>
              <td class="small">${_esc(a.motivo)}</td>
              <td><span class="badge badge-sev-${a.severidad}">${a.severidad}</span></td>
            </tr>`).join("")}
        </tbody>
      </table>`;
  }
}

// ── VOTOS POR PARTIDO ────────────────────────────────────────────────────────
async function cargarVotosPartido() {
  try {
    _datosPartido = await _fetch("/api/auditoria/votos-partido");
    renderVotosPartido(_eleccionActual);
  } catch (e) {
    toast("Error votos-partido: " + e.message, "err");
  }
}

function seleccionarEleccion(el, id) {
  document.querySelectorAll(".tab-pill").forEach(p => p.classList.remove("active"));
  el.classList.add("active");
  _eleccionActual = id;
  renderVotosPartido(id);
  renderLocalVsApi(id);
}

function renderVotosPartido(idElec) {
  if (!_datosPartido) return;
  const partidos = _datosPartido[String(idElec)] || [];
  if (!partidos.length) return;

  // Top 15 partidos
  const top = partidos.slice(0, 15);
  const labels  = top.map(p => p.nombre.length > 30 ? p.nombre.substring(0,28)+"…" : p.nombre);
  const datos   = top.map(p => p.votos);
  const colores = top.map((_, i) => PARTY_COLORS[i % PARTY_COLORS.length]);

  const ctx = _chartBase("chartVotosPartido");
  if (!ctx) return;
  _charts["chartVotosPartido"] = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: "Votos válidos",
        data:  datos,
        backgroundColor: colores,
        borderWidth: 0,
      }],
    },
    options: {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: C.grid } },
        y: { grid: { color: C.grid }, ticks: { font: { size: 10 } } },
      },
      animation: false,
    },
  });
}

// ── LOCAL vs API GENERAL ──────────────────────────────────────────────────────
async function cargarResumenGeneral() {
  try {
    const r = await _fetch("/api/auditoria/resumen-general");
    _datosResGeneral = r.comparacion || {};
    const badge = document.getElementById("cacheBadge");
    if (!r.cached) {
      badge.classList.remove("d-none");
      badge.textContent = "sin sync";
      badge.className = "ms-2 badge bg-warning";
    } else {
      badge.classList.add("d-none");
    }
    renderLocalVsApi(_eleccionActual);
  } catch (e) {
    toast("Error resumen-general: " + e.message, "err");
  }
}

function renderLocalVsApi(idElec) {
  const ctx = _chartBase("chartLocalVsApi");
  const lbl = document.getElementById("lblLocalVsApi");
  if (!_datosResGeneral) return;

  const d = _datosResGeneral[String(idElec)];
  if (!d) {
    if (lbl) lbl.textContent = "Sin datos sincronizados. Usa 'Sincronizar con ONPE'.";
    return;
  }

  const partidos = (d.partidos || []).slice(0, 15);
  const labels   = partidos.map(p => p.nombre.length > 25 ? p.nombre.substring(0,23)+"…" : p.nombre);
  const diffs    = partidos.map(p => p.diff_pct);
  const colors   = diffs.map(v => Math.abs(v) > 15 ? C.red : Math.abs(v) > 5 ? C.orange : C.green);

  if (!ctx) return;
  _charts["chartLocalVsApi"] = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: "Diferencia %",
        data:  diffs,
        backgroundColor: colors,
        borderWidth: 0,
      }],
    },
    options: {
      indexAxis: "y",
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => {
              const p = partidos[ctx.dataIndex];
              return ` API: ${(p.votos_api||0).toLocaleString()} | Local: ${(p.votos_local||0).toLocaleString()} | Δ: ${ctx.raw}%`;
            }
          }
        }
      },
      scales: {
        x: { grid: { color: C.grid }, title: { display: true, text: "% diferencia (API − local)" } },
        y: { grid: { color: C.grid }, ticks: { font: { size: 10 } } },
      },
      animation: false,
    },
  });

  const anomalias = partidos.filter(p => p.anomalia).length;
  if (lbl) {
    lbl.innerHTML = anomalias > 0
      ? `<span class="text-danger fw-bold"><i class="bi bi-exclamation-triangle me-1"></i>${anomalias} partido(s) con diferencia > 15%</span>`
      : `<span class="text-success"><i class="bi bi-check-circle me-1"></i>Sin diferencias significativas (> 15%)</span>`;
  }
}

// ── INCONSISTENCIAS DE SUMA ───────────────────────────────────────────────────
async function cargarInconsistencias() {
  try {
    _datosIncons = await _fetch("/api/auditoria/inconsistencias");
    renderInconsistencias(_datosIncons);
  } catch (e) {
    toast("Error inconsistencias: " + e.message, "err");
  }
}

function renderInconsistencias(d) {
  const items = d.items || [];
  document.getElementById("badgeInconsistencias").textContent = d.total || 0;

  // Chart: por tipo de elección
  const por_tipo = {};
  for (const item of items) {
    por_tipo[item.tipo] = (por_tipo[item.tipo] || 0) + 1;
  }
  const ctx = _chartBase("chartInconsistenciasTipo");
  if (ctx && Object.keys(por_tipo).length) {
    const labels = Object.keys(por_tipo);
    const data   = Object.values(por_tipo);
    _charts["chartInconsistenciasTipo"] = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels,
        datasets: [{
          data,
          backgroundColor: PARTY_COLORS.slice(0, labels.length),
          borderColor: C.darkBg,
          borderWidth: 2,
        }],
      },
      options: {
        plugins: { legend: { position: "bottom", labels: { font: { size: 9 } } } },
        cutout: "50%",
      },
    });
  }

  // Tabla
  const tbl = document.getElementById("tablaInconsistencias");
  if (!items.length) {
    tbl.innerHTML = `<div class="text-success text-center py-3 small"><i class="bi bi-check-circle me-1"></i>No se detectaron inconsistencias de suma.</div>`;
    return;
  }
  tbl.innerHTML = `
    <table class="table table-dark table-sm table-hover mb-0">
      <thead><tr>
        <th>Mesa</th><th>Elección</th>
        <th class="text-end">API emitidos</th>
        <th class="text-end">Suma calc.</th>
        <th class="text-end">Δ emitidos</th>
        <th class="text-end">Δ válidos</th>
        <th>Sev.</th>
      </tr></thead>
      <tbody>
        ${items.slice(0,200).map(i => `
          <tr class="${Math.abs(i.dif_emitidos) > 0 ? 'table-danger' : ''}">
            <td class="font-monospace text-info">${_esc(i.mesa)}</td>
            <td class="small text-muted">${_esc(i.tipo)}</td>
            <td class="text-end font-monospace">${i.total_emitidos_api.toLocaleString()}</td>
            <td class="text-end font-monospace">${i.suma_votos_calculada.toLocaleString()}</td>
            <td class="text-end font-monospace ${Math.abs(i.dif_emitidos)>0?'text-danger fw-bold':''}">${i.dif_emitidos > 0 ? '+' : ''}${i.dif_emitidos}</td>
            <td class="text-end font-monospace ${i.dif_validos && Math.abs(i.dif_validos)>0?'text-warning':''}">${i.dif_validos != null ? (i.dif_validos > 0 ? '+' : '') + i.dif_validos : '—'}</td>
            <td><span class="badge badge-sev-${i.severidad}">${i.severidad}</span></td>
          </tr>`).join("")}
      </tbody>
    </table>`;
}

// ── ANOMALÍAS ESTADÍSTICAS POR PARTIDO ───────────────────────────────────────
async function cargarAnomaliaPartido() {
  try {
    _datosAnomaP = await _fetch("/api/auditoria/anomalias-partido");
    renderAnomaliaPartido(_datosAnomaP);
  } catch (e) {
    toast("Error anomalias-partido: " + e.message, "err");
  }
}

function renderAnomaliaPartido(d) {
  const items = d.items || [];
  document.getElementById("badgeAnomaliaPartido").textContent = d.total || 0;

  // Chart: top partidos con más outliers
  const por_partido = {};
  for (const i of items) {
    por_partido[i.partido] = (por_partido[i.partido] || 0) + 1;
  }
  const sorted = Object.entries(por_partido).sort((a,b) => b[1]-a[1]).slice(0,12);
  const ctx = _chartBase("chartAnomaliaPartido");
  if (ctx && sorted.length) {
    const labels = sorted.map(([k]) => k.length > 25 ? k.substring(0,23)+"…" : k);
    const data   = sorted.map(([,v]) => v);
    _charts["chartAnomaliaPartido"] = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "Mesas anómalas",
          data,
          backgroundColor: sorted.map((_, i) => PARTY_COLORS[i % PARTY_COLORS.length]),
          borderWidth: 0,
        }],
      },
      options: {
        indexAxis: "y",
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { color: C.grid } },
          y: { grid: { color: C.grid }, ticks: { font: { size: 9 } } },
        },
        animation: false,
      },
    });
  }

  // Tabla
  const tbl = document.getElementById("tablaAnomaliaPartido");
  if (!items.length) {
    tbl.innerHTML = `<div class="text-success text-center py-3 small"><i class="bi bi-check-circle me-1"></i>Sin outliers estadísticos detectados (se necesitan más actas para el modelo).</div>`;
    return;
  }
  tbl.innerHTML = `
    <table class="table table-dark table-sm table-hover mb-0">
      <thead><tr>
        <th>Mesa</th><th>Partido</th><th class="text-end">%</th>
        <th class="text-end">Media%</th><th class="text-end">σ</th>
        <th class="text-end">z-score</th><th>Motivo</th><th>Sev.</th>
      </tr></thead>
      <tbody>
        ${items.slice(0,200).map(i => `
          <tr class="${i.z_score > 4 ? 'table-danger' : 'table-warning'}">
            <td class="font-monospace text-info">${_esc(i.mesa)}</td>
            <td class="small">${_esc(i.partido)}</td>
            <td class="text-end font-monospace fw-bold">${i.pct}%</td>
            <td class="text-end text-muted">${i.media_pct}%</td>
            <td class="text-end text-muted">${i.std_pct}</td>
            <td class="text-end fw-bold ${i.z_score>4?'text-danger':'text-warning'}">${i.z_score}σ</td>
            <td class="small text-muted">${_esc(i.motivo)}</td>
            <td><span class="badge badge-sev-${i.severidad}">${i.severidad}</span></td>
          </tr>`).join("")}
      </tbody>
    </table>`;
}

// ── MESAS SOSPECHOSAS ─────────────────────────────────────────────────────────
async function cargarMesasSospechosas() {
  try {
    _datosMesasSos = await _fetch("/api/auditoria/mesas-sospechosas?limit=50");
    renderMesasSospechosas(_datosMesasSos);
  } catch (e) {
    toast("Error mesas-sospechosas: " + e.message, "err");
  }
}

function renderMesasSospechosas(d) {
  const items = d.items || [];

  // Chart top 20
  const top20 = items.slice(0, 20);
  const ctx   = _chartBase("chartMesasSospechosas");
  if (ctx && top20.length) {
    const labels = top20.map(m => m.mesa);
    const data   = top20.map(m => m.score);
    const colors = data.map(s => s > 100 ? C.red : s > 50 ? C.orange : C.yellow);
    _charts["chartMesasSospechosas"] = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "Score sospecha",
          data,
          backgroundColor: colors,
          borderWidth: 0,
        }],
      },
      options: {
        indexAxis: "y",
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { color: C.grid } },
          y: { grid: { color: C.grid }, ticks: { font: { size: 10 } } },
        },
        animation: false,
      },
    });
  }

  // Tabla detallada
  const tbl = document.getElementById("tablaMesasSospechosas");
  if (!items.length) {
    tbl.innerHTML = `<div class="text-success text-center py-3 small"><i class="bi bi-check-circle me-1"></i>Sin mesas marcadas como sospechosas.</div>`;
    return;
  }
  tbl.innerHTML = `
    <table class="table table-dark table-sm table-hover mb-0">
      <thead><tr><th>Mesa</th><th>Tipo</th><th class="text-end">Score</th><th>Señales detectadas</th></tr></thead>
      <tbody>
        ${items.map(m => `
          <tr>
            <td class="font-monospace text-info">${_esc(m.mesa)}</td>
            <td class="small text-muted">${_esc(m.tipo)}</td>
            <td class="text-end fw-bold ${m.score > 100 ? 'text-danger' : m.score > 50 ? 'text-warning' : 'text-secondary'}">${m.score}</td>
            <td class="small">
              ${(m.señales||[]).map(s =>
                `<span class="tag-anomaly me-1" title="${_esc(JSON.stringify(s.detalle))}">${_esc(s.motivo)} (+${s.puntos})</span>`
              ).join("")}
            </td>
          </tr>`).join("")}
      </tbody>
    </table>`;
}

// ── SINCRONIZAR CON ONPE ──────────────────────────────────────────────────────
async function sincronizarONPE(btn) {
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Sincronizando...`;

  const statusDiv = document.getElementById("syncStatus");
  statusDiv.innerHTML = `<span class="badge bg-warning">Descargando datos de ONPE...</span>`;

  try {
    const d = await _post("/api/auditoria/sincronizar-onpe");
    const n  = d.total_elecciones || 0;
    const errs = (d.errores || []).length;

    statusDiv.innerHTML = `
      <span class="badge bg-success"><i class="bi bi-check-circle me-1"></i>${n} elecciones sincronizadas</span>
      ${errs > 0 ? `<span class="badge bg-warning">${errs} errores</span>` : ""}
    `;
    toast(`Sincronización completa: ${n} elecciones`, "ok");

    // Recargar comparación
    await cargarResumenGeneral();
    renderLocalVsApi(_eleccionActual);
  } catch (e) {
    statusDiv.innerHTML = `<span class="badge bg-danger">Error: ${_esc(e.message)}</span>`;
    toast("Error sincronización: " + e.message, "err");
  } finally {
    btn.disabled  = false;
    btn.innerHTML = orig;
  }
}

// ── INIT ──────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  cargarTodo();
});

// ── PARTICIPACIÓN CIUDADANA ───────────────────────────────────────────────────
async function cargarParticipacion() {
  try {
    const d = await _fetch("/api/auditoria/participacion");
    renderParticipacion(d);
  } catch (e) {
    toast("Error participación: " + e.message, "err");
  }
}

function renderParticipacion(d) {
  const oficial = d.oficial || {};
  const local   = d.local   || {};
  const comp    = d.comparacion || {};
  const cached  = d.cached;

  // Badge sync
  const badgeSync = document.getElementById("badgePartSync");
  if (!cached) {
    badgeSync.style.display = "";
    badgeSync.textContent   = "sin sync";
  } else {
    badgeSync.style.display = "none";
  }

  // Tarjetas macros ONPE
  const container = document.getElementById("statCardsParticipacion");
  const elec    = (oficial.total_electores || 0).toLocaleString();
  const asist   = (oficial.total_asistentes || 0).toLocaleString();
  const ausent  = (oficial.total_ausentes || 0).toLocaleString();
  const pend    = (oficial.pendientes || 0).toLocaleString();
  const pctA    = (oficial.pct_asistentes || 0).toFixed(3);
  const pctAus  = (oficial.pct_ausentes || 0).toFixed(3);
  const pctPend = (oficial.pct_pendientes || 0).toFixed(3);
  const cob     = local.cobertura_pct ? local.cobertura_pct.toFixed(1) + "%" : "—";

  if (!oficial.total_electores) {
    container.innerHTML = `<div class="col text-center text-muted small py-3"><i class="bi bi-info-circle me-1"></i>Sin datos oficiales — usa "Sincronizar participación".</div>`;
  } else {
    container.innerHTML = `
      <div class="col-xl-2 col-lg-3 col-md-4 col-sm-6">
        <div class="stat-card text-center">
          <div class="num" style="color:#58a6ff">${elec}</div>
          <div class="lbl"><i class="bi bi-people me-1"></i>Electores hábiles</div>
        </div>
      </div>
      <div class="col-xl-2 col-lg-3 col-md-4 col-sm-6">
        <div class="stat-card text-center">
          <div class="num" style="color:#3fb950">${asist}</div>
          <div class="lbl"><i class="bi bi-check-circle me-1"></i>Asistentes ${pctA}%</div>
        </div>
      </div>
      <div class="col-xl-2 col-lg-3 col-md-4 col-sm-6">
        <div class="stat-card text-center">
          <div class="num" style="color:#8b949e">${ausent}</div>
          <div class="lbl"><i class="bi bi-x-circle me-1"></i>Ausentes ${pctAus}%</div>
        </div>
      </div>
      <div class="col-xl-2 col-lg-3 col-md-4 col-sm-6">
        <div class="stat-card text-center">
          <div class="num" style="color:#d29922">${pend}</div>
          <div class="lbl"><i class="bi bi-hourglass me-1"></i>Pendientes ${pctPend}%</div>
        </div>
      </div>
      <div class="col-xl-2 col-lg-3 col-md-4 col-sm-6">
        <div class="stat-card text-center">
          <div class="num" style="color:#bc8cff">${(local.mesas_analizadas||0).toLocaleString()}</div>
          <div class="lbl"><i class="bi bi-file-earmark me-1"></i>Mesas en DB local</div>
        </div>
      </div>
      <div class="col-xl-2 col-lg-3 col-md-4 col-sm-6">
        <div class="stat-card text-center">
          <div class="num" style="color:#ffa657">${cob}</div>
          <div class="lbl"><i class="bi bi-percent me-1"></i>Cobertura electores</div>
        </div>
      </div>`;
  }

  // 1. Donut oficial
  const ctxDnt = _chartBase("chartParticipacionOficial");
  if (ctxDnt && oficial.total_electores) {
    const pend_abs = oficial.pendientes || 0;
    _charts["chartParticipacionOficial"] = new Chart(ctxDnt, {
      type: "doughnut",
      data: {
        labels: ["Asistentes", "Ausentes", "Pendientes"],
        datasets: [{
          data:            [oficial.total_asistentes, oficial.total_ausentes, pend_abs],
          backgroundColor: [C.green, C.gray, C.yellow],
          borderColor:     "#0d1117",
          borderWidth:     2,
        }],
      },
      options: {
        plugins: { legend: { position: "bottom", labels: { font: { size: 9 } } } },
        cutout: "60%",
      },
    });
    document.getElementById("lblPartOficial").innerHTML =
      `<b>${pctA}%</b> asist. · <b>${pctAus}%</b> aus. · <b>${pctPend}%</b> pend.`;
  }

  // 2. Barras: suma local vs oficial
  const ctxComp = _chartBase("chartLocalVsAsistentes");
  if (ctxComp) {
    const sumaLocal   = local.suma_emitidos || 0;
    const sumOficial  = oficial.total_asistentes || 0;
    const desfaseAbs  = comp.desfase_votos ?? (sumaLocal - sumOficial);
    const desfasePct  = comp.desfase_pct;

    _charts["chartLocalVsAsistentes"] = new Chart(ctxComp, {
      type: "bar",
      data: {
        labels: ["ONPE oficial", "Suma actas locales", "Desfase absoluto"],
        datasets: [{
          label: "Votos / Electores",
          data:  [sumOficial, sumaLocal, Math.abs(desfaseAbs)],
          backgroundColor: [C.blue, C.green, desfasePct && Math.abs(desfasePct) > 5 ? C.red : C.orange],
          borderWidth: 0,
        }],
      },
      options: {
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { color: C.grid } },
          y: { grid: { color: C.grid }, ticks: { callback: v => v.toLocaleString() } },
        },
        animation: false,
      },
    });

    const lblDesfase = document.getElementById("lblDesfase");
    if (sumOficial > 0) {
      const color = desfasePct && Math.abs(desfasePct) > UMBRAL_DESFASE ? "text-danger fw-bold" : "text-success";
      lblDesfase.innerHTML = `<span class="${color}">Desfase: ${desfaseAbs >= 0 ? "+" : ""}${desfaseAbs?.toLocaleString() ?? "—"} (${desfasePct ?? "—"}%)</span>`;
    } else {
      lblDesfase.textContent = "Sin datos ONPE — sincroniza primero";
    }
  }

  // 3. Histograma de participación por mesa
  const ctxHist = _chartBase("chartHistParticipacion");
  const hist = d.histograma_participacion || [];
  if (ctxHist && hist.length > 0) {
    const labels = hist.map(h => `${h.desde}–${h.hasta}%`);
    const data   = hist.map(h => h.count);
    const colors = hist.map(h => h.hasta <= 30 ? C.red : h.hasta <= 60 ? C.orange : h.desde >= 98 ? C.red : C.green);
    _charts["chartHistParticipacion"] = new Chart(ctxHist, {
      type: "bar",
      data: {
        labels,
        datasets: [{ label: "Mesas", data, backgroundColor: colors, borderWidth: 0 }],
      },
      options: {
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { color: C.grid } },
          y: { grid: { color: C.grid }, ticks: { maxTicksLimit: 4 } },
        },
        animation: false,
      },
    });

    const media = local.media_pct;
    const std   = local.std_pct;
    document.getElementById("lblHistPart").innerHTML =
      `Media: <b>${media ?? "—"}%</b> · σ: <b>${std ?? "—"}</b>`;
  }

  // Tabla de anomalías
  const anomalias = d.anomalias || [];
  const badge = document.getElementById("badgeAnomaliasPart");
  const cnt   = document.getElementById("cntAnomaliasPart");
  cnt.textContent = d.total_anomalias || 0;
  if ((d.total_anomalias || 0) > 0) {
    badge.style.display = "";
    badge.textContent   = d.total_anomalias;
  }

  const tbl = document.getElementById("tablaAnomaliasPart");
  if (!anomalias.length) {
    tbl.innerHTML = `<div class="text-success text-center py-3 small"><i class="bi bi-check-circle me-1"></i>Sin anomalías de participación detectadas${!oficial.total_electores ? " (sincroniza con ONPE para análisis completo)" : ""}.</div>`;
    return;
  }

  tbl.innerHTML = `
    <table class="table table-dark table-sm table-hover mb-0">
      <thead><tr>
        <th>Mesa</th><th>Categoría</th><th>Motivo</th>
        <th class="text-end">Emitidos</th><th class="text-end">Electores</th><th>Sev.</th>
      </tr></thead>
      <tbody>
        ${anomalias.slice(0, 200).map(a => `
          <tr class="${a.severidad === 'alta' ? 'table-danger' : 'table-warning'}">
            <td class="font-monospace text-info">${_esc(a.mesa)}</td>
            <td><span class="badge bg-secondary">${_esc(a.categoria)}</span></td>
            <td class="small">${_esc(a.motivo)}</td>
            <td class="text-end font-monospace">${a.detalle?.emitidos?.toLocaleString() ?? "—"}</td>
            <td class="text-end font-monospace">${a.detalle?.electores?.toLocaleString() ?? "—"}</td>
            <td><span class="badge badge-sev-${a.severidad}">${a.severidad}</span></td>
          </tr>`).join("")}
      </tbody>
    </table>`;
}

const UMBRAL_DESFASE = 5;  // % de diferencia que se considera anómalo

async function sincronizarParticipacion(btn) {
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Sincronizando...`;
  try {
    const d = await _post("/api/auditoria/sincronizar-participacion");
    toast(`Participación sincronizada: ${d.total_electores?.toLocaleString()} electores`, "ok");
    await cargarParticipacion();
  } catch (e) {
    toast("Error: " + e.message, "err");
  } finally {
    btn.disabled  = false;
    btn.innerHTML = orig;
  }
}
