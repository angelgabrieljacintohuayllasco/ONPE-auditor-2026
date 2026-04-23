"""
Sistema automático de barrido de mesas electorales.
=====================================================
Consulta mesas desde 000001 hasta 999999 (o un rango definido),
clasifica su estado, descarga PDFs opcionales y genera reportes.

Diseño:
  - BatchJob:   job persistente guardado en DB, reanudable
  - Worker:     hilo de trabajo que procesa mesas una a una
  - Statistics: agrega métricas en tiempo real

Estados de mesa descubiertos:
  EXISTE_SIN_PDF   → la API retorna actas pero sin URL de PDF
  EXISTE_CON_PDF   → la API retorna actas con URL de PDF descargable
  NO_EXISTE        → la API no tiene registros para esa mesa
  ERROR_API        → fallo de red o HTTP 5xx al consultar
  PARA_JEE         → codigoEstado indica que fue enviada al JEE
  CON_ERROR_ACTA   → codigoEstado indica acta con error
  OBSERVADA        → acta observada / impugnada
"""

import threading
import time
import json
import logging
from collections import deque
from datetime import datetime
from typing import Optional, Dict, List, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# ── Constantes de estado ──────────────────────────────────────────────────────

STATUS_NO_EXISTE      = "NO_EXISTE"
STATUS_EXISTE_SIN_PDF = "EXISTE_SIN_PDF"
STATUS_EXISTE_CON_PDF = "EXISTE_CON_PDF"
STATUS_PARA_JEE       = "PARA_JEE"
STATUS_CON_ERROR      = "CON_ERROR_ACTA"
STATUS_OBSERVADA      = "OBSERVADA"
STATUS_ERROR_API      = "ERROR_API"
STATUS_PENDIENTE      = "PENDIENTE"

# Códigos de estado ONPE que indican envío al JEE (observados en la API)
CODIGOS_JEE     = {"PARA_ENVIO_AL_JEE", "ENVIADO_AL_JEE", "JEE"}
CODIGOS_ERROR   = {"CON_ERROR", "ERROR_EN_ACTA", "CON_ERROR_AR"}
CODIGOS_OBS     = {"OBSERVADA", "IMPUGNADA", "OBSERVADO"}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _classify_acta_status(actas: List[Dict]) -> str:
    """Clasifica el estado consolidado de una mesa a partir de sus actas."""
    if not actas:
        return STATUS_NO_EXISTE

    # Revisar códigos de estado de cada acta
    codigos = set()
    tiene_pdf_url = False
    for a in actas:
        # Diferentes campos según el endpoint
        cod = (
            a.get("codigoEstado")
            or a.get("estadoActa")
            or a.get("descripcionEstadoActa")
            or ""
        ).upper().replace(" ", "_")
        codigos.add(cod)
        if a.get("file_url") or a.get("fileUrl") or a.get("urlFile"):
            tiene_pdf_url = True

    # Prioridad: JEE > Error > Observada > Con PDF > Sin PDF
    if codigos & CODIGOS_JEE:
        return STATUS_PARA_JEE
    if codigos & CODIGOS_ERROR:
        return STATUS_CON_ERROR
    if codigos & CODIGOS_OBS:
        return STATUS_OBSERVADA
    if tiene_pdf_url:
        return STATUS_EXISTE_CON_PDF
    return STATUS_EXISTE_SIN_PDF


def _format_codigo(n: int) -> str:
    return str(n).zfill(6)


# ── BatchJob ─────────────────────────────────────────────────────────────────

class BatchJob:
    """
    Representa un trabajo de barrido completo o parcial.
    Se persiste en la DB para poder reanudar tras un reinicio.
    """

    def __init__(
        self,
        job_id: str,
        rango_inicio: int = 1,
        rango_fin: int = 999999,
        descargar_pdf: bool = False,
        ejecutar_ocr: bool = False,
        ejecutar_forense: bool = False,
        ocr_engine: str = "local",         # "local" | "google"
        concurrencia: int = 3,
        delay_segundos: float = 0.5,       # pausa entre consultas
        max_vacios_consecutivos: int = 200, # 0 = sin límite
        carpeta_destino: str = None,        # None = data/actas/, "dataset" = data/dataset/
    ):
        self.job_id         = job_id
        self.rango_inicio   = rango_inicio
        self.rango_fin      = rango_fin
        self.descargar_pdf  = descargar_pdf
        self.ejecutar_ocr   = ejecutar_ocr
        self.ejecutar_forense = ejecutar_forense
        self.ocr_engine     = ocr_engine
        self.concurrencia   = concurrencia
        self.delay_segundos = delay_segundos
        self.max_vacios_consecutivos = max_vacios_consecutivos
        self.carpeta_destino = carpeta_destino  # subcarpeta destino para PDFs
        self._vacios_consecutivos    = 0   # contador en tiempo real

        # Estado en tiempo real (thread-safe)
        self._lock           = threading.Lock()
        self.estado          = "pendiente"   # pendiente | ejecutando | pausado | completado | error
        self.mesa_actual     = rango_inicio
        self.progreso        = 0             # mesas procesadas
        self.total_mesas     = rango_fin - rango_inicio + 1
        self.iniciado_en     = None
        self.finalizado_en   = None
        self.ultimo_error    = None

        # Estadísticas acumuladas
        self.stats = {
            STATUS_NO_EXISTE:      0,
            STATUS_EXISTE_SIN_PDF: 0,
            STATUS_EXISTE_CON_PDF: 0,
            STATUS_PARA_JEE:       0,
            STATUS_CON_ERROR:      0,
            STATUS_OBSERVADA:      0,
            STATUS_ERROR_API:      0,
        }

        # Log en tiempo real (últimos 200 mensajes)
        self._logs     = deque(maxlen=200)
        self._log_seq  = 0

        # Control de cancelación
        self._cancel_event = threading.Event()
        self._pause_event  = threading.Event()

    def add_log(self, tipo: str, msg: str, mesa: str = None):
        """Agrega un mensaje de log al buffer del job (thread-safe)."""
        with self._lock:
            self._log_seq += 1
            self._logs.append({
                "seq":  self._log_seq,
                "tipo": tipo,
                "msg":  msg,
                "mesa": mesa,
                "ts":   datetime.now().strftime("%H:%M:%S"),
            })

    def to_dict(self) -> Dict:
        with self._lock:
            pct = round(100 * self.progreso / max(1, self.total_mesas), 1)
            elapsed = None
            if self.iniciado_en:
                t0 = datetime.fromisoformat(self.iniciado_en)
                elapsed = round((datetime.now() - t0).total_seconds())
            eta = None
            if elapsed and self.progreso > 0 and self.estado == "ejecutando":
                rate = self.progreso / elapsed   # mesas/segundo
                remaining = self.total_mesas - self.progreso
                eta = round(remaining / rate) if rate > 0 else None

            return {
                "job_id":        self.job_id,
                "estado":        self.estado,
                "rango_inicio":  self.rango_inicio,
                "rango_fin":     self.rango_fin,
                "mesa_actual":   self.mesa_actual,
                "progreso":      self.progreso,
                "total_mesas":   self.total_mesas,
                "porcentaje":    pct,
                "stats":         dict(self.stats),
                "iniciado_en":   self.iniciado_en,
                "finalizado_en": self.finalizado_en,
                "elapsed_seg":   elapsed,
                "eta_seg":       eta,
                "ultimo_error":  self.ultimo_error,
                "ocr_engine":    self.ocr_engine,
                "descargar_pdf": self.descargar_pdf,
                "ejecutar_ocr":  self.ejecutar_ocr,
                "vacios_consecutivos":     self._vacios_consecutivos,
                "max_vacios_consecutivos": self.max_vacios_consecutivos,
                "carpeta_destino":         self.carpeta_destino,
                "log_recientes":           list(self._logs)[-50:],
            }

    def cancel(self):
        self._cancel_event.set()

    def pause(self):
        self._pause_event.set()
        with self._lock:
            if self.estado == "ejecutando":
                self.estado = "pausado"

    def resume(self):
        self._pause_event.clear()
        with self._lock:
            if self.estado == "pausado":
                self.estado = "ejecutando"


# ── Worker ────────────────────────────────────────────────────────────────────

def _process_single_mesa(
    codigo: str,
    job: BatchJob,
    on_result: Optional[Callable] = None,
) -> Dict:
    """
    Procesa UNA mesa: consulta la API, clasifica, opcionalmente descarga PDF
    y ejecuta OCR/forense. Guarda en DB.

    Retorna un dict con el resultado de la mesa.
    """
    from modules.api_client  import ONPEApiClient
    from modules.downloader  import download_pdf, get_local_path
    from modules             import database as db
    from config              import ELECCION_IDS, OCR_DPI, DATA_DIR
    import os as _os

    result = {
        "codigo_mesa": codigo,
        "status":      STATUS_ERROR_API,
        "actas":       [],
        "pdfs":        [],
        "error":       None,
        "timestamp":   datetime.now().isoformat(),
    }

    ELECCION_NOMBRES = {v: k for k, v in ELECCION_IDS.items()}

    # Actualizar mesa actual en tiempo real (visible para el UI antes de terminar)
    with job._lock:
        job.mesa_actual = int(codigo)
    job.add_log("info", f"Consultando API para mesa {codigo}…", codigo)

    client = ONPEApiClient()
    try:
        actas_raw = client.buscar_por_mesa(codigo)
    except Exception as e:
        result["error"] = str(e)
        job.add_log("error", f"Error de red: {e}", codigo)
        client.close()
        return result
    finally:
        pass

    if not actas_raw:
        client.close()
        result["status"] = STATUS_NO_EXISTE
        job.add_log("warn", f"Mesa {codigo}: no existe en ONPE", codigo)
        # Guardar en DB como no existente (solo si no la tenemos ya)
        try:
            db.upsert_batch_mesa(codigo, STATUS_NO_EXISTE, job.job_id, [])
        except AttributeError:
            pass   # función aún no disponible en DB
        return result

    # Enriquecer actas: obtener detalle completo (con candidatos, votos y archivos PDF)
    # Una sola llamada a /actas/{id} por acta que trae TODO: votos, candidatos y archivos
    actas_enriquecidas = []
    for acta in actas_raw[:10]:   # máximo 10 actas por mesa
        acta_id = acta.get("id")
        id_elec = acta.get("idEleccion", 0)
        tipo    = ELECCION_NOMBRES.get(id_elec, f"Elección {id_elec}")

        # Obtener detalle completo: votos por partido, candidatos, archivos (MongoDB IDs)
        detalle_completo = {}
        file_url = None
        try:
            if acta_id:
                detalle_completo = client.get_acta_detalle_completo(int(acta_id))
                # La presigned URL se obtiene del mismo detalle (archivos[tipo=1].id)
                # — sin doble request
                file_url = client.get_acta_file_url(int(acta_id), detalle=detalle_completo)
        except Exception as e:
            logger.debug(f"[barrido] detalle/file acta {acta_id}: {e}")

        actas_enriquecidas.append({
            **acta,
            "detalle_completo": detalle_completo,
            "file_url":         file_url,
            "tipoEleccion":     tipo,
        })

    client.close()

    status = _classify_acta_status(actas_enriquecidas)
    result["status"] = status
    result["actas"]  = actas_enriquecidas
    n_actas = len(actas_enriquecidas)
    job.add_log("info", f"Mesa {codigo}: {n_actas} acta(s) — {status}", codigo)

    # ── Guardar en DB ──────────────────────────────────────────────────────
    try:
        db.upsert_mesa(codigo, "auto_barrido", f"Barrido automático job={job.job_id}", {"actas": actas_raw})
    except Exception as e:
        logger.debug(f"[barrido] upsert_mesa error: {e}")

    for acta in actas_enriquecidas:
        id_elec = acta.get("idEleccion", 0)
        tipo    = acta.get("tipoEleccion", "")
        # Guardar el detalle completo (con candidatos, votos y archivos)
        # Si no se pudo obtener el detalle completo, usar el array de votos del buscar/mesa
        api_json_data = acta.get("detalle_completo") or acta.get("detalle") or {}
        try:
            db.upsert_acta(
                codigo_mesa   = codigo,
                id_eleccion   = id_elec,
                tipo_eleccion = tipo,
                estado        = acta.get("descripcionEstadoActa", acta.get("estado", "")),
                codigo_estado = acta.get("codigoEstadoActa", acta.get("codigoEstado", "")),
                api_json      = api_json_data,
                file_url      = acta.get("file_url"),
            )
        except Exception as e:
            logger.debug(f"[barrido] upsert_acta error: {e}")

    # ── Descargar PDFs (opcional) ─────────────────────────────────────────
    if job.descargar_pdf:
        n_con_url = sum(1 for a in actas_enriquecidas if a.get("file_url"))
        if n_con_url:
            job.add_log("info", f"Mesa {codigo}: descargando {n_con_url} PDF(s)…", codigo)
        _client2 = ONPEApiClient()
        for acta in actas_enriquecidas:
            id_elec  = acta.get("idEleccion", 0)
            acta_id  = acta.get("id")
            if not acta_id:
                continue
            try:
                # Re-obtener presigned URL fresca (expiran en 660s)
                # Reutilizar el detalle_completo que ya tenemos para no hacer otro GET /actas/{id}
                detalle_guardado = acta.get("detalle_completo") or {}
                fresh_url = _client2.get_acta_file_url(int(acta_id), detalle=detalle_guardado or None)
                if not fresh_url:
                    continue
                # Si el job tiene carpeta_destino, guardar en data/<carpeta>/ en vez de data/actas/
                _base_dir = _os.path.join(DATA_DIR, job.carpeta_destino) if job.carpeta_destino else None
                dest     = get_local_path(codigo, id_elec, "pdf", base_dir=_base_dir)
                pdf_path = download_pdf(fresh_url, dest, session=_client2.get_session())
                if pdf_path:
                    result["pdfs"].append({"id_eleccion": id_elec, "path": pdf_path})
                    nombre_elec = ELECCION_NOMBRES.get(id_elec, f"e{id_elec}")
                    job.add_log("ok", f"PDF descargado: {nombre_elec}", codigo)
                    # Actualizar pdf_path en DB
                    db.upsert_acta(
                        codigo_mesa   = codigo,
                        id_eleccion   = id_elec,
                        tipo_eleccion = acta.get("tipoEleccion", ""),
                        estado        = acta.get("descripcionEstadoActa", ""),
                        api_json      = {},
                        pdf_path      = pdf_path,
                    )
            except Exception as e:
                logger.warning(f"[barrido] descarga PDF {codigo}/{id_elec}: {e}")
                job.add_log("error", f"Error descarga e{id_elec}: {e}", codigo)
        _client2.close()

    # ── OCR (opcional) ────────────────────────────────────────────────────
    if job.ejecutar_ocr and result["pdfs"]:
        for pdf_info in result["pdfs"]:
            id_elec   = pdf_info["id_eleccion"]
            pdf_path  = pdf_info["path"]
            nombre_el = ELECCION_NOMBRES.get(id_elec, f"e{id_elec}")
            engine_label = "Google DocAI" if job.ocr_engine == "google" else "Local OCR"
            job.add_log("info", f"OCR [{engine_label}] {nombre_el}…", codigo)
            try:
                if job.ocr_engine == "google":
                    from modules.google_ocr import process_pdf_with_google
                    ocr_result = process_pdf_with_google(pdf_path)
                else:
                    from modules.ocr_processor import process_pdf
                    ocr_result = process_pdf(pdf_path, dpi=OCR_DPI)

                if "error" not in ocr_result:
                    n_campos = len(ocr_result.get("datos_extraidos", {}))
                    job.add_log("ok", f"OCR {nombre_el}: {n_campos} campos extraídos", codigo)
                    acta_rows = db.get_actas_by_mesa(codigo)
                    acta_row  = next((a for a in acta_rows if a["id_eleccion"] == id_elec), None)
                    if acta_row:
                        acta_db_id = acta_row["id"]
                        db.save_ocr_result(
                            acta_id     = acta_db_id,
                            codigo_mesa = codigo,
                            id_eleccion = id_elec,
                            texto_crudo = ocr_result.get("texto_crudo", ""),
                            datos       = ocr_result.get("datos_extraidos", {}),
                            es_escaneado= ocr_result.get("es_escaneado", True),
                            metodo      = ocr_result.get("metodo", ""),
                        )

                        # ── OCR secundario local (cuando engine=google, para modo híbrido) ──
                        if job.ocr_engine == "google":
                            try:
                                from modules.ocr_processor import process_pdf as _proc_local
                                ocr_local_result = _proc_local(pdf_path, dpi=OCR_DPI)
                                if "error" not in ocr_local_result:
                                    db.save_ocr_result(
                                        acta_id     = acta_db_id,
                                        codigo_mesa = codigo,
                                        id_eleccion = id_elec,
                                        texto_crudo = ocr_local_result.get("texto_crudo", ""),
                                        datos       = ocr_local_result.get("datos_extraidos", {}),
                                        es_escaneado= ocr_local_result.get("es_escaneado", True),
                                        metodo      = ocr_local_result.get("metodo", "local"),
                                    )
                                    job.add_log("ok", f"OCR local {nombre_el}: {len(ocr_local_result.get('datos_extraidos', {}))} campos", codigo)
                            except Exception as _el:
                                logger.warning(f"[barrido] OCR local secundario {codigo}/{id_elec}: {_el}")

                        # ── Forense (si está habilitado) ──────────────────
                        if job.ejecutar_forense:
                            try:
                                job.add_log("info", f"Forense {nombre_el}…", codigo)
                                from modules.forensics import full_forensic_analysis
                                reporte = full_forensic_analysis(pdf_path)
                                db.save_forensic_report(
                                    acta_id     = acta_db_id,
                                    codigo_mesa = codigo,
                                    id_eleccion = id_elec,
                                    reporte     = reporte,
                                )
                                score = reporte.get("score_anomalia", "?")
                                job.add_log("ok", f"Forense {nombre_el}: score={score}", codigo)
                            except Exception as ef:
                                logger.warning(f"[barrido] Forense {codigo}/{id_elec}: {ef}")
                                job.add_log("warn", f"Forense {nombre_el}: {ef}", codigo)

                        # ── Comparación API vs OCR ────────────────────────
                        try:
                            from modules.comparator import compare_api_vs_ocr, compare_votos_api_vs_grafico
                            api_data = acta_row.get("api_json")
                            if isinstance(api_data, str):
                                import json as _json
                                api_data = _json.loads(api_data or "{}")
                            ocr_datos = ocr_result.get("datos_extraidos", {})
                            if isinstance(api_data, dict) and isinstance(ocr_datos, dict):
                                comp = compare_api_vs_ocr(api_data, ocr_datos, codigo)
                                db.save_comparison(
                                    acta_id     = acta_db_id,
                                    codigo_mesa = codigo,
                                    id_eleccion = id_elec,
                                    comparacion = comp,
                                )
                        except Exception as ec:
                            logger.warning(f"[barrido] Comparación {codigo}/{id_elec}: {ec}")
                else:
                    err_msg = ocr_result.get("error", "desconocido")
                    job.add_log("error", f"OCR {nombre_el} falló: {err_msg}", codigo)

            except Exception as e:
                logger.warning(f"[barrido] OCR {codigo}/{id_elec}: {e}")
                job.add_log("error", f"OCR {nombre_el} excepción: {e}", codigo)

    # ── Salvar estado en batch_results ────────────────────────────────────
    job.add_log("ok", f"Mesa {codigo} completada → {status}", codigo)
    try:
        db.upsert_batch_result(job.job_id, codigo, status, result)
    except AttributeError:
        pass

    if on_result:
        on_result(result)

    return result


# ── Runner principal ──────────────────────────────────────────────────────────

def run_batch_job(
    job: BatchJob,
    on_progress: Optional[Callable[[Dict], None]] = None,
) -> Dict:
    """
    Ejecuta el BatchJob procesando todas las mesas del rango.
    Esta función es bloqueante — lanzar en un hilo separado.

    on_progress(job_dict) se llama tras cada mesa procesada.
    """
    with job._lock:
        job.estado      = "ejecutando"
        job.iniciado_en = datetime.now().isoformat()

    rango = range(job.rango_inicio, job.rango_fin + 1)
    # Si ya hay progreso (reanudación), saltar las mesas ya procesadas
    rango = range(job.mesa_actual, job.rango_fin + 1)

    def _update_stats(result: Dict):
        status = result.get("status", STATUS_ERROR_API)
        should_stop = False
        with job._lock:
            job.stats[status] = job.stats.get(status, 0) + 1
            job.progreso += 1
            job.mesa_actual = int(result["codigo_mesa"])
            if status == STATUS_NO_EXISTE:
                job._vacios_consecutivos += 1
            else:
                job._vacios_consecutivos = 0
            if (job.max_vacios_consecutivos > 0
                    and job._vacios_consecutivos >= job.max_vacios_consecutivos):
                should_stop = True
        if should_stop and not job._cancel_event.is_set():
            logger.info(
                f"[BatchJob {job.job_id}] Detenido: "
                f"{job._vacios_consecutivos} mesas consecutivas sin respuesta."
            )
            job._cancel_event.set()
        if on_progress:
            on_progress(job.to_dict())

    logger.info(
        f"[BatchJob {job.job_id}] Iniciando barrido "
        f"{_format_codigo(job.rango_inicio)}-{_format_codigo(job.rango_fin)} "
        f"({job.total_mesas:,} mesas)"
    )

    try:
        with ThreadPoolExecutor(max_workers=job.concurrencia) as executor:
            futures = {}

            for n in rango:
                # Control de pausa/cancelación
                while job._pause_event.is_set():
                    time.sleep(0.5)
                if job._cancel_event.is_set():
                    break

                codigo = _format_codigo(n)
                future = executor.submit(_process_single_mesa, codigo, job, None)
                futures[future] = codigo

                # Limitar cantidad de futures pendientes (ventana deslizante)
                if len(futures) >= job.concurrencia * 4:
                    done = next(as_completed(futures))
                    result = done.result()
                    _update_stats(result)
                    del futures[done]

                time.sleep(job.delay_segundos / job.concurrencia)

            # Vaciar futuros pendientes
            for future in as_completed(futures):
                if job._cancel_event.is_set():
                    break
                result = future.result()
                _update_stats(result)

    except Exception as e:
        with job._lock:
            job.ultimo_error = str(e)
        logger.error(f"[BatchJob {job.job_id}] Error fatal: {e}", exc_info=True)

    with job._lock:
        job.estado        = "completado" if not job._cancel_event.is_set() else "cancelado"
        job.finalizado_en = datetime.now().isoformat()

    logger.info(
        f"[BatchJob {job.job_id}] Finalizado. "
        f"Stats: {job.stats}"
    )

    return job.to_dict()


# ── Registro global de jobs activos ──────────────────────────────────────────

_active_jobs: Dict[str, BatchJob] = {}
_jobs_lock = threading.Lock()


def create_job(
    rango_inicio: int = 1,
    rango_fin: int = 999999,
    descargar_pdf: bool = False,
    ejecutar_ocr: bool = False,
    ejecutar_forense: bool = False,
    ocr_engine: str = "local",
    concurrencia: int = 3,
    delay_segundos: float = 0.5,
    max_vacios_consecutivos: int = 200,
    carpeta_destino: str = None,
) -> BatchJob:
    """Crea un nuevo BatchJob y lo registra."""
    import uuid
    job_id = f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    job = BatchJob(
        job_id         = job_id,
        rango_inicio   = rango_inicio,
        rango_fin      = rango_fin,
        descargar_pdf  = descargar_pdf,
        ejecutar_ocr   = ejecutar_ocr,
        ejecutar_forense = ejecutar_forense,
        ocr_engine     = ocr_engine,
        concurrencia   = concurrencia,
        delay_segundos = delay_segundos,
        max_vacios_consecutivos = max_vacios_consecutivos,
        carpeta_destino = carpeta_destino,
    )
    with _jobs_lock:
        _active_jobs[job_id] = job
    return job


def get_job(job_id: str) -> Optional[BatchJob]:
    with _jobs_lock:
        return _active_jobs.get(job_id)


def list_jobs() -> List[Dict]:
    with _jobs_lock:
        return [j.to_dict() for j in _active_jobs.values()]


def start_job_async(
    job: BatchJob,
    on_progress: Optional[Callable] = None,
) -> threading.Thread:
    """Lanza el job en un hilo daemon y retorna el hilo."""
    t = threading.Thread(
        target  = run_batch_job,
        args    = (job, on_progress),
        daemon  = True,
        name    = f"BatchJob-{job.job_id}",
    )
    t.start()
    return t


# ── Estadísticas de barrido ──────────────────────────────────────────────────

def get_barrido_stats() -> Dict:
    """
    Agrega estadísticas de todos los resultados de barrido almacenados en la DB.
    """
    try:
        from modules import database as db
        return db.get_barrido_stats()
    except Exception as e:
        return {"error": str(e)}


def get_mesas_by_status(status: str, limit: int = 200) -> List[Dict]:
    """Retorna mesas filtradas por estado de barrido."""
    try:
        from modules import database as db
        return db.get_batch_results_by_status(status, limit)
    except Exception as e:
        return []
