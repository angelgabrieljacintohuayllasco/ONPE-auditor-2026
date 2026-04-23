"""
Base de datos SQLite local para almacenar actas, resultados OCR y reportes forenses.
"""

import sqlite3
import json
import os
import logging
from datetime import datetime
from typing import Dict, List, Optional

from config import DB_PATH

logger = logging.getLogger(__name__)


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Crea las tablas si no existen."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS mesas (
                codigo_mesa     TEXT PRIMARY KEY,
                categoria       TEXT,
                descripcion     TEXT,
                fecha_consulta  TEXT,
                raw_json        TEXT
            );

            CREATE TABLE IF NOT EXISTS actas (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_mesa     TEXT NOT NULL,
                id_eleccion     INTEGER,
                tipo_eleccion   TEXT,
                estado          TEXT,
                codigo_estado   TEXT,
                api_json        TEXT,
                file_url        TEXT,
                pdf_path        TEXT,
                fecha_descarga  TEXT,
                UNIQUE(codigo_mesa, id_eleccion)
            );

            -- ── Tablas del sistema automático de barrido ──────────────────
            CREATE TABLE IF NOT EXISTS batch_jobs (
                job_id          TEXT PRIMARY KEY,
                rango_inicio    INTEGER,
                rango_fin       INTEGER,
                estado          TEXT DEFAULT 'pendiente',
                config_json     TEXT,
                stats_json      TEXT,
                iniciado_en     TEXT,
                finalizado_en   TEXT,
                progreso        INTEGER DEFAULT 0,
                mesa_actual     INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS batch_results (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          TEXT REFERENCES batch_jobs(job_id),
                codigo_mesa     TEXT NOT NULL,
                status          TEXT NOT NULL,
                actas_count     INTEGER DEFAULT 0,
                tiene_pdf       INTEGER DEFAULT 0,
                es_jee          INTEGER DEFAULT 0,
                result_json     TEXT,
                timestamp       TEXT,
                UNIQUE(job_id, codigo_mesa)
            );

            -- ── Tabla de anomalías detectadas por One-Class SVM ──────────
            CREATE TABLE IF NOT EXISTS anomaly_results (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_mesa     TEXT NOT NULL,
                id_eleccion     INTEGER NOT NULL,
                is_anomaly      INTEGER DEFAULT 0,
                score           REAL,
                score_normalized REAL,
                confidence      TEXT,
                features_json   TEXT,
                explanation_json TEXT,
                model_version   TEXT,
                fecha_analisis  TEXT,
                UNIQUE(codigo_mesa, id_eleccion)
            );

            CREATE TABLE IF NOT EXISTS ocr_results (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                acta_id         INTEGER REFERENCES actas(id),
                codigo_mesa     TEXT,
                id_eleccion     INTEGER,
                texto_crudo     TEXT,
                datos_json      TEXT,
                es_escaneado    INTEGER,
                metodo_ocr      TEXT,
                fecha_proceso   TEXT
            );

            CREATE TABLE IF NOT EXISTS forensic_reports (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                acta_id         INTEGER REFERENCES actas(id),
                codigo_mesa     TEXT,
                id_eleccion     INTEGER,
                reporte_json    TEXT,
                alertas_json    TEXT,
                tiene_anomalia  INTEGER DEFAULT 0,
                fecha_analisis  TEXT
            );

            CREATE TABLE IF NOT EXISTS comparisons (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                acta_id         INTEGER REFERENCES actas(id),
                codigo_mesa     TEXT,
                id_eleccion     INTEGER,
                estado          TEXT,
                discrepancias_json TEXT,
                alertas_json    TEXT,
                fecha_comparacion TEXT
            );

            CREATE TABLE IF NOT EXISTS resumen_general_cache (
                id_eleccion         INTEGER PRIMARY KEY,
                tipo_eleccion       TEXT,
                totales_json        TEXT,
                participantes_json  TEXT,
                fecha_captura       TEXT
            );

            CREATE TABLE IF NOT EXISTS participacion_cache (
                id              INTEGER PRIMARY KEY CHECK (id = 1),
                totales_json    TEXT,
                ubigeos_json    TEXT,
                fecha_captura   TEXT
            );
        """)
    logger.info(f"DB inicializada: {DB_PATH}")


# ── CRUD ─────────────────────────────────────────────────────────────────────

def upsert_mesa(codigo_mesa: str, categoria: str = "", descripcion: str = "", raw: dict = None):
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO mesas (codigo_mesa, categoria, descripcion, fecha_consulta, raw_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(codigo_mesa) DO UPDATE SET
                categoria      = excluded.categoria,
                descripcion    = excluded.descripcion,
                fecha_consulta = excluded.fecha_consulta,
                raw_json       = excluded.raw_json
        """, (
            codigo_mesa,
            categoria,
            descripcion,
            datetime.now().isoformat(),
            json.dumps(raw or {}, ensure_ascii=False),
        ))


def upsert_acta(
    codigo_mesa: str,
    id_eleccion: int,
    tipo_eleccion: str = "",
    estado: str = "",
    codigo_estado: str = "",
    api_json: dict = None,
    file_url: str = None,
    pdf_path: str = None,
) -> int:
    """Inserta o actualiza un acta. Retorna el rowid."""
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO actas (
                codigo_mesa, id_eleccion, tipo_eleccion, estado,
                codigo_estado, api_json, file_url, pdf_path, fecha_descarga
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(codigo_mesa, id_eleccion) DO UPDATE SET
                tipo_eleccion  = excluded.tipo_eleccion,
                estado         = excluded.estado,
                codigo_estado  = excluded.codigo_estado,
                api_json       = CASE WHEN excluded.api_json IS NOT NULL AND excluded.api_json != '{}' THEN excluded.api_json ELSE api_json END,
                file_url       = COALESCE(excluded.file_url, file_url),
                pdf_path       = COALESCE(excluded.pdf_path, pdf_path),
                fecha_descarga = excluded.fecha_descarga
        """, (
            codigo_mesa, id_eleccion, tipo_eleccion, estado,
            codigo_estado,
            json.dumps(api_json or {}, ensure_ascii=False),
            file_url, pdf_path,
            datetime.now().isoformat(),
        ))
        row = conn.execute(
            "SELECT id FROM actas WHERE codigo_mesa=? AND id_eleccion=?",
            (codigo_mesa, id_eleccion)
        ).fetchone()
        return row["id"] if row else 0


def save_ocr_result(
    acta_id: int,
    codigo_mesa: str,
    id_eleccion: int,
    texto_crudo: str,
    datos: dict,
    es_escaneado: bool,
    metodo: str,
):
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO ocr_results
                (acta_id, codigo_mesa, id_eleccion, texto_crudo, datos_json,
                 es_escaneado, metodo_ocr, fecha_proceso)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            acta_id, codigo_mesa, id_eleccion,
            texto_crudo,
            json.dumps(datos, ensure_ascii=False),
            1 if es_escaneado else 0,
            metodo,
            datetime.now().isoformat(),
        ))


def save_forensic_report(
    acta_id: int,
    codigo_mesa: str,
    id_eleccion: int,
    reporte: dict,
):
    alertas = reporte.get("alertas", [])
    tiene_anomalia = 1 if any(a["nivel"] == "alerta" for a in alertas) else 0
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO forensic_reports
                (acta_id, codigo_mesa, id_eleccion, reporte_json,
                 alertas_json, tiene_anomalia, fecha_analisis)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            acta_id, codigo_mesa, id_eleccion,
            json.dumps(reporte, ensure_ascii=False),
            json.dumps(alertas, ensure_ascii=False),
            tiene_anomalia,
            datetime.now().isoformat(),
        ))


def save_comparison(
    acta_id: int,
    codigo_mesa: str,
    id_eleccion: int,
    comparacion: dict,
):
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO comparisons
                (acta_id, codigo_mesa, id_eleccion, estado,
                 discrepancias_json, alertas_json, fecha_comparacion)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            acta_id, codigo_mesa, id_eleccion,
            comparacion.get("estado", ""),
            json.dumps(comparacion.get("discrepancias", []), ensure_ascii=False),
            json.dumps(comparacion.get("alertas", []), ensure_ascii=False),
            datetime.now().isoformat(),
        ))


# ── Consultas ──────────────────────────────────────────────────────────────

def get_all_mesas() -> List[Dict]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM mesas ORDER BY fecha_consulta DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_mesa(codigo_mesa: str) -> Optional[Dict]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM mesas WHERE codigo_mesa=?", (codigo_mesa,)
        ).fetchone()
        return dict(row) if row else None


def get_actas_by_mesa(codigo_mesa: str) -> List[Dict]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM actas WHERE codigo_mesa=? ORDER BY id_eleccion",
            (codigo_mesa,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_ocr(codigo_mesa: str, id_eleccion: int) -> Optional[Dict]:
    with _get_conn() as conn:
        row = conn.execute("""
            SELECT * FROM ocr_results
            WHERE codigo_mesa=? AND id_eleccion=?
            ORDER BY fecha_proceso DESC LIMIT 1
        """, (codigo_mesa, id_eleccion)).fetchone()
        if row:
            r = dict(row)
            r["datos_json"] = json.loads(r.get("datos_json") or "{}")
            return r
        return None


def get_ocr_by_method(codigo_mesa: str, id_eleccion: int, metodo: str) -> Optional[Dict]:
    """Devuelve el OCR más reciente filtrado por método ('local' o 'google')."""
    with _get_conn() as conn:
        if metodo == "local":
            # 'local' incluye filas donde metodo_ocr es NULL (anteriores a que se guardara el campo)
            row = conn.execute("""
                SELECT * FROM ocr_results
                WHERE codigo_mesa=? AND id_eleccion=?
                  AND (metodo_ocr IS NULL OR metodo_ocr='local')
                ORDER BY fecha_proceso DESC LIMIT 1
            """, (codigo_mesa, id_eleccion)).fetchone()
        else:
            row = conn.execute("""
                SELECT * FROM ocr_results
                WHERE codigo_mesa=? AND id_eleccion=? AND metodo_ocr=?
                ORDER BY fecha_proceso DESC LIMIT 1
            """, (codigo_mesa, id_eleccion, metodo)).fetchone()
        if row:
            r = dict(row)
            r["datos_json"] = json.loads(r.get("datos_json") or "{}")
            return r
        return None


def get_latest_forensic(codigo_mesa: str, id_eleccion: int) -> Optional[Dict]:
    with _get_conn() as conn:
        row = conn.execute("""
            SELECT * FROM forensic_reports
            WHERE codigo_mesa=? AND id_eleccion=?
            ORDER BY fecha_analisis DESC LIMIT 1
        """, (codigo_mesa, id_eleccion)).fetchone()
        if row:
            r = dict(row)
            r["reporte_json"] = json.loads(r.get("reporte_json") or "{}")
            r["alertas_json"] = json.loads(r.get("alertas_json") or "[]")
            return r
        return None


def get_dashboard_stats() -> Dict:
    with _get_conn() as conn:
        stats = {
            "total_mesas":      conn.execute("SELECT COUNT(*) FROM mesas").fetchone()[0],
            "total_actas":      conn.execute("SELECT COUNT(*) FROM actas").fetchone()[0],
            "actas_con_pdf":    conn.execute("SELECT COUNT(*) FROM actas WHERE pdf_path IS NOT NULL").fetchone()[0],
            "actas_con_ocr":    conn.execute("SELECT COUNT(DISTINCT acta_id) FROM ocr_results").fetchone()[0],
            "actas_con_forense":conn.execute("SELECT COUNT(DISTINCT acta_id) FROM forensic_reports").fetchone()[0],
            "actas_con_anomalia":conn.execute("SELECT COUNT(*) FROM forensic_reports WHERE tiene_anomalia=1").fetchone()[0],
            "discrepancias":    conn.execute("SELECT COUNT(*) FROM comparisons WHERE estado='alerta'").fetchone()[0],
            # Nuevas métricas
            "mesas_barridas":   conn.execute("SELECT COUNT(DISTINCT codigo_mesa) FROM batch_results").fetchone()[0],
            "mesas_jee":        conn.execute("SELECT COUNT(*) FROM batch_results WHERE es_jee=1").fetchone()[0],
            "anomalias_svm":    conn.execute("SELECT COUNT(*) FROM anomaly_results WHERE is_anomaly=1").fetchone()[0],
        }
        return stats


# ── CRUD: Batch Jobs ──────────────────────────────────────────────────────────

def upsert_batch_job(job_id: str, config: dict, estado: str = "pendiente",
                     progreso: int = 0, mesa_actual: int = 0,
                     stats: dict = None, iniciado_en: str = None,
                     finalizado_en: str = None):
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO batch_jobs
                (job_id, rango_inicio, rango_fin, estado, config_json, stats_json,
                 iniciado_en, finalizado_en, progreso, mesa_actual)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                estado        = excluded.estado,
                stats_json    = excluded.stats_json,
                finalizado_en = excluded.finalizado_en,
                progreso      = excluded.progreso,
                mesa_actual   = excluded.mesa_actual
        """, (
            job_id,
            config.get("rango_inicio", 1),
            config.get("rango_fin", 999999),
            estado,
            json.dumps(config, ensure_ascii=False),
            json.dumps(stats or {}, ensure_ascii=False),
            iniciado_en,
            finalizado_en,
            progreso,
            mesa_actual,
        ))


def get_batch_job(job_id: str) -> Optional[Dict]:
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM batch_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row:
            r = dict(row)
            r["config_json"] = json.loads(r.get("config_json") or "{}")
            r["stats_json"]  = json.loads(r.get("stats_json")  or "{}")
            return r
        return None


def get_all_batch_jobs() -> List[Dict]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM batch_jobs ORDER BY iniciado_en DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_batch_result(job_id: str, codigo_mesa: str, status: str, result: dict):
    actas_count = len(result.get("actas", []))
    tiene_pdf   = 1 if result.get("pdfs") else 0
    es_jee      = 1 if status == "PARA_JEE" else 0
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO batch_results
                (job_id, codigo_mesa, status, actas_count, tiene_pdf, es_jee, result_json, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id, codigo_mesa) DO UPDATE SET
                status      = excluded.status,
                actas_count = excluded.actas_count,
                tiene_pdf   = excluded.tiene_pdf,
                es_jee      = excluded.es_jee,
                result_json = excluded.result_json,
                timestamp   = excluded.timestamp
        """, (
            job_id, codigo_mesa, status, actas_count, tiene_pdf, es_jee,
            json.dumps(result, ensure_ascii=False, default=str),
            datetime.now().isoformat(),
        ))


def upsert_batch_mesa(codigo_mesa: str, status: str, job_id: str, actas: list):
    """Versión simplificada para registrar el estado de una mesa en barrido."""
    upsert_batch_result(job_id, codigo_mesa, status, {"actas": actas, "pdfs": []})


def get_batch_results_by_status(status: str, limit: int = 500) -> List[Dict]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM batch_results WHERE status=? ORDER BY codigo_mesa LIMIT ?",
            (status, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def get_barrido_stats() -> Dict:
    with _get_conn() as conn:
        total = conn.execute("SELECT COUNT(DISTINCT codigo_mesa) FROM batch_results").fetchone()[0]
        by_status = {}
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM batch_results GROUP BY status"
        ).fetchall()
        for row in rows:
            by_status[row["status"]] = row["cnt"]
        jobs = conn.execute(
            "SELECT job_id, estado, progreso, rango_inicio, rango_fin, iniciado_en FROM batch_jobs ORDER BY iniciado_en DESC LIMIT 5"
        ).fetchall()
        return {
            "total_mesas_barridas": total,
            "por_estado":           by_status,
            "ultimos_jobs":         [dict(j) for j in jobs],
        }


# ── CRUD: Anomalías SVM ───────────────────────────────────────────────────────

def save_anomaly_result(
    codigo_mesa: str,
    id_eleccion: int,
    is_anomaly: bool,
    score: float,
    score_normalized: float,
    confidence: str,
    features: dict,
    explanation: list,
    model_version: str = "",
):
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO anomaly_results
                (codigo_mesa, id_eleccion, is_anomaly, score, score_normalized,
                 confidence, features_json, explanation_json, model_version, fecha_analisis)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(codigo_mesa, id_eleccion) DO UPDATE SET
                is_anomaly        = excluded.is_anomaly,
                score             = excluded.score,
                score_normalized  = excluded.score_normalized,
                confidence        = excluded.confidence,
                features_json     = excluded.features_json,
                explanation_json  = excluded.explanation_json,
                model_version     = excluded.model_version,
                fecha_analisis    = excluded.fecha_analisis
        """, (
            codigo_mesa, id_eleccion,
            1 if is_anomaly else 0,
            score, score_normalized, confidence,
            json.dumps(features, ensure_ascii=False),
            json.dumps(explanation, ensure_ascii=False),
            model_version,
            datetime.now().isoformat(),
        ))


def get_anomaly_result(codigo_mesa: str, id_eleccion: int) -> Optional[Dict]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM anomaly_results WHERE codigo_mesa=? AND id_eleccion=?",
            (codigo_mesa, id_eleccion)
        ).fetchone()
        if row:
            r = dict(row)
            r["features_json"]     = json.loads(r.get("features_json")     or "{}")
            r["explanation_json"]  = json.loads(r.get("explanation_json")  or "[]")
            return r
        return None


def get_all_anomalies(only_positive: bool = False, limit: int = 1000) -> List[Dict]:
    with _get_conn() as conn:
        q = "SELECT * FROM anomaly_results"
        if only_positive:
            q += " WHERE is_anomaly=1"
        q += " ORDER BY score ASC LIMIT ?"
        rows = conn.execute(q, (limit,)).fetchall()
        result = []
        for row in rows:
            r = dict(row)
            r["features_json"]    = json.loads(r.get("features_json")    or "{}")
            r["explanation_json"] = json.loads(r.get("explanation_json") or "[]")
            result.append(r)
        return result


# ── Dashboard de Resultados Masivos ──────────────────────────────────────────

def get_actas_full_list(
    page: int = 1,
    limit: int = 100,
    filtro: str = "todos",
    mesa: str = "",
    tipo_eleccion: str = "",
    sort: str = "mesa",
) -> Dict:
    """
    Retorna lista paginada de actas con datos completos joined de OCR,
    forense, comparación y anomalías. Usado por el dashboard de resultados.
    """
    offset = (page - 1) * limit

    conditions: list = []
    params_where: list = []

    if mesa:
        conditions.append("a.codigo_mesa LIKE ?")
        params_where.append(f"%{mesa.strip()}%")

    if tipo_eleccion:
        try:
            conditions.append("a.id_eleccion = ?")
            params_where.append(int(tipo_eleccion))
        except ValueError:
            pass

    if filtro == "con_pdf":
        conditions.append("a.pdf_path IS NOT NULL AND a.pdf_path != ''")
    elif filtro == "con_ocr":
        conditions.append(
            "EXISTS (SELECT 1 FROM ocr_results WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion)"
        )
    elif filtro == "con_anomalia":
        conditions.append(
            "(EXISTS (SELECT 1 FROM forensic_reports WHERE codigo_mesa=a.codigo_mesa"
            "         AND id_eleccion=a.id_eleccion AND tiene_anomalia=1)"
            " OR EXISTS (SELECT 1 FROM anomaly_results WHERE codigo_mesa=a.codigo_mesa"
            "            AND id_eleccion=a.id_eleccion AND is_anomaly=1))"
        )
    elif filtro == "con_discrepancia":
        conditions.append(
            "EXISTS (SELECT 1 FROM comparisons WHERE codigo_mesa=a.codigo_mesa"
            "        AND id_eleccion=a.id_eleccion AND estado='alerta')"
        )
    elif filtro == "sin_procesar":
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM ocr_results WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion)"
            " AND NOT EXISTS (SELECT 1 FROM forensic_reports WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion)"
        )

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    sort_map = {
        "mesa":         "a.codigo_mesa ASC",
        "anomalia":     (
            "COALESCE((SELECT tiene_anomalia FROM forensic_reports"
            "  WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion"
            "  ORDER BY fecha_analisis DESC LIMIT 1), 0) DESC, a.codigo_mesa ASC"
        ),
        "discrepancia": (
            "CASE WHEN EXISTS (SELECT 1 FROM comparisons WHERE codigo_mesa=a.codigo_mesa"
            "  AND id_eleccion=a.id_eleccion AND estado='alerta') THEN 0 ELSE 1 END ASC,"
            " a.codigo_mesa ASC"
        ),
        "fecha": "COALESCE(a.fecha_descarga, '0000-00-00') DESC",
        "tipo":  "a.id_eleccion ASC, a.codigo_mesa ASC",
    }
    order_by = sort_map.get(sort, "a.codigo_mesa ASC")

    select_sql = f"""
        SELECT
            a.codigo_mesa,
            a.id_eleccion,
            a.tipo_eleccion,
            a.estado,
            a.pdf_path,
            a.api_json,
            a.fecha_descarga,
            CASE WHEN a.pdf_path IS NOT NULL AND a.pdf_path != '' THEN 1 ELSE 0 END AS tiene_pdf,
            (SELECT 1 FROM ocr_results
             WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion LIMIT 1)          AS tiene_ocr,
            (SELECT es_escaneado FROM ocr_results
             WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion
             ORDER BY fecha_proceso DESC LIMIT 1)                                             AS es_escaneado,
            (SELECT metodo_ocr FROM ocr_results
             WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion
             ORDER BY fecha_proceso DESC LIMIT 1)                                             AS metodo_ocr,
            (SELECT datos_json FROM ocr_results
             WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion
             ORDER BY fecha_proceso DESC LIMIT 1)                                             AS datos_json_ocr,
            (SELECT datos_json FROM ocr_results
             WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion
             AND (metodo_ocr IS NULL OR metodo_ocr NOT LIKE 'google%')
             ORDER BY fecha_proceso DESC LIMIT 1)                                             AS datos_json_ocr_local,
            (SELECT datos_json FROM ocr_results
             WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion
             AND metodo_ocr LIKE 'google%'
             ORDER BY fecha_proceso DESC LIMIT 1)                                             AS datos_json_ocr_google,
            (SELECT 1 FROM forensic_reports
             WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion LIMIT 1)          AS tiene_forense,
            (SELECT tiene_anomalia FROM forensic_reports
             WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion
             ORDER BY fecha_analisis DESC LIMIT 1)                                            AS anomalia_forense,
            (SELECT alertas_json FROM forensic_reports
             WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion
             ORDER BY fecha_analisis DESC LIMIT 1)                                            AS alertas_forense,
            (SELECT estado FROM comparisons
             WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion
             ORDER BY fecha_comparacion DESC LIMIT 1)                                         AS comp_estado,
            (SELECT discrepancias_json FROM comparisons
             WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion
             ORDER BY fecha_comparacion DESC LIMIT 1)                                         AS discrepancias_json,
            (SELECT is_anomaly FROM anomaly_results
             WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion LIMIT 1)          AS is_anomaly_svm,
            (SELECT score FROM anomaly_results
             WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion LIMIT 1)          AS score_svm,
            (SELECT confidence FROM anomaly_results
             WHERE codigo_mesa=a.codigo_mesa AND id_eleccion=a.id_eleccion LIMIT 1)          AS confidence_svm
        FROM actas a
        {where}
    """

    json_obj_fields = {"api_json", "datos_json_ocr", "datos_json_ocr_local", "datos_json_ocr_google"}
    json_arr_fields = {"alertas_forense", "discrepancias_json"}

    with _get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM actas a {where}", params_where
        ).fetchone()[0]

        rows = conn.execute(
            f"{select_sql} ORDER BY {order_by} LIMIT ? OFFSET ?",
            params_where + [limit, offset],
        ).fetchall()

        result = []
        for row in rows:
            r = dict(row)
            for f in json_obj_fields:
                try:
                    r[f] = json.loads(r.get(f) or "{}")
                except Exception:
                    r[f] = {}
            for f in json_arr_fields:
                try:
                    r[f] = json.loads(r.get(f) or "[]")
                except Exception:
                    r[f] = []
            r["tiene_ocr"]     = bool(r.get("tiene_ocr"))
            r["tiene_forense"] = bool(r.get("tiene_forense"))
            result.append(r)

        # Stats globales (sin filtros) para las tarjetas superiores
        gs = conn.execute("""
            SELECT
                COUNT(*)                                                                   AS total_actas,
                SUM(CASE WHEN pdf_path IS NOT NULL AND pdf_path != '' THEN 1 ELSE 0 END)  AS con_pdf,
                (SELECT COUNT(DISTINCT codigo_mesa||'-'||id_eleccion) FROM ocr_results)   AS con_ocr,
                (SELECT COUNT(*) FROM forensic_reports WHERE tiene_anomalia=1)             AS con_anomalia,
                (SELECT COUNT(*) FROM comparisons WHERE estado='alerta')                   AS con_discrepancia,
                (SELECT COUNT(*) FROM ocr_results WHERE es_escaneado=0)                    AS digitales,
                (SELECT COUNT(*) FROM ocr_results WHERE es_escaneado=1)                    AS escaneados
            FROM actas
        """).fetchone()
        global_stats = dict(gs) if gs else {}

    return {
        "actas":        result,
        "total":        total,
        "page":         page,
        "limit":        limit,
        "pages":        max(1, (total + limit - 1) // limit),
        "global_stats": global_stats,
    }


def get_resultados_charts() -> Dict:
    """Datos para las gráficas del dashboard de resultados masivos."""
    with _get_conn() as conn:
        tipo_doc = {
            "digital":   conn.execute("SELECT COUNT(*) FROM ocr_results WHERE es_escaneado=0").fetchone()[0],
            "escaneado": conn.execute("SELECT COUNT(*) FROM ocr_results WHERE es_escaneado=1").fetchone()[0],
            "sin_ocr":   conn.execute(
                "SELECT COUNT(*) FROM actas WHERE NOT EXISTS "
                "(SELECT 1 FROM ocr_results WHERE codigo_mesa=actas.codigo_mesa AND id_eleccion=actas.id_eleccion)"
            ).fetchone()[0],
        }

        anom_rows = conn.execute("""
            SELECT a.id_eleccion,
                   COALESCE(a.tipo_eleccion, 'Elección '||a.id_eleccion) AS tipo_eleccion,
                   COUNT(*) AS total,
                   SUM(CASE WHEN f.tiene_anomalia=1 THEN 1 ELSE 0 END) AS con_anomalia
            FROM actas a
            LEFT JOIN forensic_reports f
                   ON f.codigo_mesa=a.codigo_mesa AND f.id_eleccion=a.id_eleccion
            GROUP BY a.id_eleccion, a.tipo_eleccion
            ORDER BY total DESC
            LIMIT 10
        """).fetchall()

        disc_alerta = conn.execute(
            "SELECT COUNT(*) FROM comparisons WHERE estado='alerta'"
        ).fetchone()[0]
        disc_ok = conn.execute(
            "SELECT COUNT(*) FROM comparisons WHERE estado != 'alerta' OR estado IS NULL"
        ).fetchone()[0]

        # Mesas con más discrepancias
        top_disc = conn.execute("""
            SELECT codigo_mesa,
                   COUNT(*) AS num_disc
            FROM comparisons
            WHERE estado='alerta'
            GROUP BY codigo_mesa
            ORDER BY num_disc DESC
            LIMIT 10
        """).fetchall()

    return {
        "tipo_documento":      tipo_doc,
        "anomalias_por_tipo":  [dict(r) for r in anom_rows],
        "discrepancias":       {"alerta": disc_alerta, "ok": disc_ok},
        "top_discrepancias":   [dict(r) for r in top_disc],
    }


# ── Resumen General Cache (ONPE API) ─────────────────────────────────────────

def save_resumen_general(id_eleccion: int, tipo_eleccion: str, totales: dict, participantes: list):
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO resumen_general_cache
                (id_eleccion, tipo_eleccion, totales_json, participantes_json, fecha_captura)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id_eleccion) DO UPDATE SET
                tipo_eleccion      = excluded.tipo_eleccion,
                totales_json       = excluded.totales_json,
                participantes_json = excluded.participantes_json,
                fecha_captura      = excluded.fecha_captura
        """, (
            id_eleccion,
            tipo_eleccion,
            json.dumps(totales, ensure_ascii=False),
            json.dumps(participantes, ensure_ascii=False),
            datetime.now().isoformat(),
        ))


def get_resumen_general(id_eleccion: int) -> Optional[Dict]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM resumen_general_cache WHERE id_eleccion=?",
            (id_eleccion,)
        ).fetchone()
        if row:
            r = dict(row)
            r["totales_json"]       = json.loads(r.get("totales_json")       or "{}")
            r["participantes_json"] = json.loads(r.get("participantes_json") or "[]")
            return r
        return None


def get_all_resumen_general() -> List[Dict]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM resumen_general_cache ORDER BY id_eleccion"
        ).fetchall()
        result = []
        for row in rows:
            r = dict(row)
            r["totales_json"]       = json.loads(r.get("totales_json")       or "{}")
            r["participantes_json"] = json.loads(r.get("participantes_json") or "[]")
            result.append(r)
        return result


# ── Participación ciudadana cache ─────────────────────────────────────────────

def save_participacion(totales: dict, ubigeos: list):
    """Guarda (o reemplaza) los datos de participación ciudadana."""
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO participacion_cache (id, totales_json, ubigeos_json, fecha_captura)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                totales_json  = excluded.totales_json,
                ubigeos_json  = excluded.ubigeos_json,
                fecha_captura = excluded.fecha_captura
        """, (json.dumps(totales), json.dumps(ubigeos), datetime.now().isoformat()))


def get_participacion() -> Optional[Dict]:
    """Retorna los datos de participación ciudadana guardados en cache."""
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM participacion_cache WHERE id=1").fetchone()
        if row:
            r = dict(row)
            r["totales_json"] = json.loads(r.get("totales_json") or "{}")
            r["ubigeos_json"] = json.loads(r.get("ubigeos_json") or "[]")
            return r
        return None

