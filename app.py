"""
Servidor Flask local - Analizador Forense de Actas ONPE 2026
"""

import os
import json
import base64
import logging
import threading
from datetime import datetime
from typing import Dict

from flask import Flask, jsonify, request, render_template, send_file, abort

# ── Configuración de logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Importaciones locales ─────────────────────────────────────────────────────
from config import (
    MESAS_PRUEBA, CATEGORIA_COLORES, CATEGORIA_LABELS,
    ACTAS_DIR, ELECCION_IDS, OCR_DPI,
    OCR_ENGINE, OCSVM_NU, OCSVM_KERNEL, OCSVM_GAMMA,
    BARRIDO_CONCURRENCIA, BARRIDO_DELAY_SEG,
)
from modules.api_client  import ONPEApiClient
from modules.downloader  import download_pdf, get_local_path, pdf_info
from modules.ocr_processor import process_pdf, get_page_images
from modules.forensics   import full_forensic_analysis
from modules.comparator  import compare_api_vs_ocr, compare_votos_api_vs_grafico, compare_hibrido
from modules import database as db
from modules import automation as auto_mod
from modules import anomaly_detector as anomaly_mod
from modules import google_ocr as gocr_mod

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["JSON_ENSURE_ASCII"] = False

# Lock para operaciones pesadas
_processing_lock = threading.Lock()

# Inicializar DB al arrancar
db.init_db()


# ── Helpers ───────────────────────────────────────────────────────────────────

ELECCION_NOMBRES = {v: k for k, v in ELECCION_IDS.items()}

def _tipo_eleccion(id_eleccion: int) -> str:
    return ELECCION_NOMBRES.get(id_eleccion, f"Elección {id_eleccion}")


def _success(data=None, **kwargs):
    r = {"ok": True}
    if data is not None:
        r["data"] = data
    r.update(kwargs)
    return jsonify(r)


def _error(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


# ── Rutas de UI ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html",
                           mesas_prueba=MESAS_PRUEBA,
                           categoria_colores=CATEGORIA_COLORES,
                           categoria_labels=CATEGORIA_LABELS)


@app.route("/proceso")
def proceso():
    """Página de ejecución masiva (recibe parámetros por query string)."""
    return render_template("proceso.html")


# ── API: Datos ONPE ───────────────────────────────────────────────────────────

@app.route("/api/proceso")
def api_proceso():
    """Retorna el proceso electoral activo."""
    try:
        client = ONPEApiClient()
        data = client.get_proceso_activo()
        client.close()
        return _success(data)
    except Exception as e:
        return _error(str(e))


@app.route("/api/buscar_mesa")
def api_buscar_mesa():
    """
    Busca actas en la API ONPE por número de mesa.
    ?codigo_mesa=054938
    """
    codigo = request.args.get("codigo_mesa", "").strip().zfill(6)
    if not codigo or len(codigo) > 6:
        return _error("Parámetro 'codigo_mesa' inválido")

    try:
        client = ONPEApiClient()
        actas = client.buscar_por_mesa(codigo)
        client.close()
        # Enriquecer con tipo de elección
        for a in actas:
            a["tipoEleccion"] = _tipo_eleccion(a.get("idEleccion", 0))
        return _success(actas)
    except Exception as e:
        logger.error(f"Error buscando mesa {codigo}: {e}", exc_info=True)
        return _error(str(e))


@app.route("/api/acta_detalle/<int:acta_id>")
def api_acta_detalle(acta_id: int):
    """Retorna el detalle completo de un acta por su ID numérico."""
    try:
        client = ONPEApiClient()
        data = client.get_acta_detalle(acta_id)
        client.close()
        return _success(data)
    except Exception as e:
        return _error(str(e))


@app.route("/api/acta_file_url")
def api_acta_file_url():
    """
    Retorna la presigned URL del PDF para un acta.
    ?acta_id=5493814013610
    """
    acta_id = request.args.get("acta_id")
    if not acta_id:
        return _error("Parámetro 'acta_id' requerido")
    try:
        client = ONPEApiClient()
        url = client.get_acta_file_url(int(acta_id))
        client.close()
        if url:
            return _success({"url": url})
        return _error("No se pudo obtener la URL del PDF", 404)
    except Exception as e:
        return _error(str(e))


# ── API: Procesamiento local ──────────────────────────────────────────────────

@app.route("/api/descargar_mesa", methods=["POST"])
def api_descargar_mesa():
    """
    Descarga todas las actas de una mesa desde ONPE.
    Body JSON: {"codigo_mesa": "054938"}
    """
    body = request.get_json(silent=True) or {}
    codigo = body.get("codigo_mesa", "").strip().zfill(6)
    if not codigo:
        return _error("'codigo_mesa' requerido")

    categoria   = body.get("categoria", "")
    descripcion = body.get("descripcion", "")

    # Mantener el cliente vivo durante la descarga para reutilizar cookies
    client = ONPEApiClient()
    try:
        mesa_data = client.get_mesa_completa(codigo)
    except Exception as e:
        client.close()
        return _error(f"Error consultando API: {e}")

    if not mesa_data.get("actas"):
        client.close()
        return _error(f"Mesa {codigo} no encontrada en ONPE", 404)

    # Guardar en DB
    db.upsert_mesa(codigo, categoria, descripcion, mesa_data)
    resultados = []
    session = client.get_session()   # reutilizar sesión con cookies ONPE

    for acta in mesa_data["actas"]:
        id_elec   = acta["idEleccion"]
        tipo      = _tipo_eleccion(id_elec)
        estado    = acta["estado"]
        file_url  = acta.get("file_url")
        pdf_path  = None
        error_msg = None

        # Descargar PDF si hay URL
        if file_url:
            dest = get_local_path(codigo, id_elec, "pdf")
            pdf_path = download_pdf(file_url, dest, session=session)
            if pdf_path is None:
                error_msg = "Descarga fallida (403 o error de red). Ver logs."
        else:
            error_msg = "Sin URL de PDF disponible para este tipo de elección"

        acta_id = db.upsert_acta(
            codigo_mesa    = codigo,
            id_eleccion    = id_elec,
            tipo_eleccion  = tipo,
            estado         = estado,
            codigo_estado  = acta.get("codigoEstado", ""),
            api_json       = acta.get("detalle", acta.get("resumen", {})),
            file_url       = file_url,
            pdf_path       = pdf_path,
        )

        resultados.append({
            "id_eleccion":    id_elec,
            "tipo":           tipo,
            "estado":         estado,
            "tiene_url":      file_url is not None,
            "pdf_descargado": pdf_path is not None,
            "pdf_path":       pdf_path,
            "acta_db_id":     acta_id,
            "error":          error_msg,
        })

    client.close()
    return _success({
        "codigo_mesa": codigo,
        "actas_procesadas": len(resultados),
        "pdfs_descargados": sum(1 for r in resultados if r["pdf_descargado"]),
        "resultados": resultados,
    })


@app.route("/api/procesar_ocr", methods=["POST"])
def api_procesar_ocr():
    """
    Ejecuta OCR sobre el PDF de un acta descargada.
    Body: {"codigo_mesa": "054938", "id_eleccion": 10}
    """
    body = request.get_json(silent=True) or {}
    codigo     = body.get("codigo_mesa", "").strip().zfill(6)
    id_eleccion = int(body.get("id_eleccion", 0))

    if not codigo or not id_eleccion:
        return _error("'codigo_mesa' e 'id_eleccion' requeridos")

    pdf_path = get_local_path(codigo, id_eleccion, "pdf")
    if not os.path.exists(pdf_path):
        return _error(f"PDF no encontrado: {pdf_path}. Descarga primero el acta.", 404)

    resultado_ocr = process_pdf(pdf_path, dpi=OCR_DPI)
    if "error" in resultado_ocr:
        return _error(resultado_ocr["error"])

    # Guardar en DB
    actas = db.get_actas_by_mesa(codigo)
    acta_row = next((a for a in actas if a["id_eleccion"] == id_eleccion), None)
    if acta_row:
        db.save_ocr_result(
            acta_id     = acta_row["id"],
            codigo_mesa = codigo,
            id_eleccion = id_eleccion,
            texto_crudo = resultado_ocr.get("texto_crudo", ""),
            datos       = resultado_ocr.get("datos_extraidos", {}),
            es_escaneado= resultado_ocr.get("es_escaneado", True),
            metodo      = resultado_ocr.get("metodo", ""),
        )

    # Devolver resultado completo al frontend (texto crudo incluido para debug)
    resultado_web = {k: v for k, v in resultado_ocr.items() if k != "texto_crudo"}
    resultado_web["texto_preview"] = resultado_ocr.get("texto_crudo", "")[:3000]
    resultado_web["texto_completo_len"] = len(resultado_ocr.get("texto_crudo", ""))

    return _success(resultado_web)


@app.route("/api/analisis_forense", methods=["POST"])
def api_analisis_forense():
    """
    Análisis forense completo (metadatos, puntos amarillos, artefactos).
    Body: {"codigo_mesa": "054938", "id_eleccion": 10}
    """
    body = request.get_json(silent=True) or {}
    codigo      = body.get("codigo_mesa", "").strip().zfill(6)
    id_eleccion = int(body.get("id_eleccion", 0))

    if not codigo or not id_eleccion:
        return _error("'codigo_mesa' e 'id_eleccion' requeridos")

    pdf_path = get_local_path(codigo, id_eleccion, "pdf")
    if not os.path.exists(pdf_path):
        return _error(f"PDF no encontrado: {pdf_path}", 404)

    reporte = full_forensic_analysis(pdf_path)

    actas = db.get_actas_by_mesa(codigo)
    acta_row = next((a for a in actas if a["id_eleccion"] == id_eleccion), None)
    if acta_row:
        db.save_forensic_report(
            acta_id     = acta_row["id"],
            codigo_mesa = codigo,
            id_eleccion = id_eleccion,
            reporte     = reporte,
        )

    return _success(reporte)


@app.route("/api/comparar", methods=["POST"])
def api_comparar():
    """
    Compara datos de la API vs OCR.
    Body: {"codigo_mesa": "054938", "id_eleccion": 10}
    """
    body = request.get_json(silent=True) or {}
    codigo      = body.get("codigo_mesa", "").strip().zfill(6)
    id_eleccion = int(body.get("id_eleccion", 0))

    if not codigo or not id_eleccion:
        return _error("'codigo_mesa' e 'id_eleccion' requeridos")

    # Datos API
    actas = db.get_actas_by_mesa(codigo)
    acta_row = next((a for a in actas if a["id_eleccion"] == id_eleccion), None)
    if not acta_row:
        return _error("Acta no encontrada en DB. Descarga primero.", 404)

    api_data = json.loads(acta_row.get("api_json") or "{}")

    # Datos OCR
    ocr_row = db.get_latest_ocr(codigo, id_eleccion)
    if not ocr_row:
        return _error("Sin datos OCR. Procesa el OCR primero.", 404)
    ocr_data = ocr_row.get("datos_json", {})

    import config as cfg
    modo = getattr(cfg, "COMPARACION_MODO", "simple")

    if modo in ("hibrido", "hibrido_ia"):
        ocr_local_row  = db.get_ocr_by_method(codigo, id_eleccion, "local")
        ocr_google_row = db.get_ocr_by_method(codigo, id_eleccion, "google")
        ocr_local      = ocr_local_row.get("datos_json",  {}) if ocr_local_row  else {}
        ocr_google     = ocr_google_row.get("datos_json", {}) if ocr_google_row else {}
        comparacion    = compare_hibrido(api_data, ocr_local, ocr_google, codigo)
    else:
        comparacion = compare_api_vs_ocr(api_data, ocr_data, codigo)

    consistencia = compare_votos_api_vs_grafico(api_data)

    resultado = {
        "comparacion_api_ocr": comparacion,
        "consistencia_interna_api": consistencia,
    }

    # Guardar
    db.save_comparison(
        acta_id     = acta_row["id"],
        codigo_mesa = codigo,
        id_eleccion = id_eleccion,
        comparacion = comparacion,
    )

    return _success(resultado)


@app.route("/api/comparar/gemini", methods=["POST"])
def api_comparar_gemini():
    """
    Análisis de un acta con Gemini Flash 2.5 (modo hibrido_ia).
    Requiere que la acta tenga PDF descargado y OCR procesado.
    Body: {"codigo_mesa": "054938", "id_eleccion": 10}
    """
    import config as cfg
    body = request.get_json(silent=True) or {}
    codigo      = body.get("codigo_mesa", "").strip().zfill(6)
    id_eleccion = int(body.get("id_eleccion", 0))

    if not codigo or not id_eleccion:
        return _error("'codigo_mesa' e 'id_eleccion' requeridos")

    api_key = getattr(cfg, "GEMINI_API_KEY", "")
    if not api_key:
        return _error("GEMINI_API_KEY no configurada — ingresa tu clave en Configuración → Modo Híbrido IA")

    # Obtener acta con PDF
    actas = db.get_actas_by_mesa(codigo)
    acta_row = next((a for a in actas if a["id_eleccion"] == id_eleccion), None)
    if not acta_row:
        return _error("Acta no encontrada", 404)

    pdf_path = acta_row.get("pdf_path") or ""
    if not pdf_path or not os.path.exists(pdf_path):
        return _error("PDF no disponible — descarga el acta primero")

    api_data = json.loads(acta_row.get("api_json") or "{}") if isinstance(acta_row.get("api_json"), str) else (acta_row.get("api_json") or {})

    ocr_local_row  = db.get_ocr_by_method(codigo, id_eleccion, "local")
    ocr_google_row = db.get_ocr_by_method(codigo, id_eleccion, "google")
    ocr_local      = ocr_local_row.get("datos_json",  {}) if ocr_local_row  else {}
    ocr_google     = ocr_google_row.get("datos_json", {}) if ocr_google_row else {}

    from modules import gemini_client
    resultado = gemini_client.analizar_con_gemini(
        pdf_path   = pdf_path,
        api_data   = api_data,
        ocr_local  = ocr_local,
        ocr_google = ocr_google,
        api_key    = api_key,
    )

    if resultado.get("error"):
        return _error(resultado["error"])

    return _success(resultado)


@app.route("/api/procesar_todo", methods=["POST"])
def api_procesar_todo():
    """
    Pipeline completo para una mesa:
    1. Descargar actas de ONPE
    2. OCR de todos los PDFs descargados
    3. Análisis forense
    4. Comparación API vs OCR

    Body: {"codigo_mesa": "054938", "categoria": "normal", "descripcion": "..."}
    """
    body = request.get_json(silent=True) or {}
    codigo = body.get("codigo_mesa", "").strip().zfill(6)
    if not codigo:
        return _error("'codigo_mesa' requerido")

    resultados = {}

    # 1. Descargar — mantener cliente vivo para reutilizar cookies ONPE
    client = ONPEApiClient()
    try:
        mesa_data = client.get_mesa_completa(codigo)
    except Exception as e:
        client.close()
        return _error(f"Error API: {e}")

    if not mesa_data.get("actas"):
        client.close()
        return _error(f"Mesa {codigo} no encontrada", 404)

    db.upsert_mesa(codigo, body.get("categoria", ""), body.get("descripcion", ""), mesa_data)
    resultados["descarga"] = []
    session = client.get_session()

    for acta in mesa_data["actas"]:
        id_elec  = acta["idEleccion"]
        tipo     = _tipo_eleccion(id_elec)
        file_url = acta.get("file_url")
        pdf_path = None

        if file_url:
            dest     = get_local_path(codigo, id_elec, "pdf")
            pdf_path = download_pdf(file_url, dest, session=session)

        acta_id = db.upsert_acta(
            codigo_mesa   = codigo,
            id_eleccion   = id_elec,
            tipo_eleccion = tipo,
            estado        = acta["estado"],
            codigo_estado = acta.get("codigoEstado", ""),
            api_json      = acta.get("detalle", acta.get("resumen", {})),
            file_url      = file_url,
            pdf_path      = pdf_path,
        )

        acta_info = {
            "id_eleccion": id_elec,
            "tipo": tipo,
            "pdf_descargado": pdf_path is not None,
            "ocr": None,
            "forense": None,
            "comparacion": None,
        }

        if pdf_path:
            # 2. OCR
            try:
                ocr_result = process_pdf(pdf_path, dpi=OCR_DPI)
                db.save_ocr_result(
                    acta_id     = acta_id,
                    codigo_mesa = codigo,
                    id_eleccion = id_elec,
                    texto_crudo = ocr_result.get("texto_crudo", ""),
                    datos       = ocr_result.get("datos_extraidos", {}),
                    es_escaneado= ocr_result.get("es_escaneado", True),
                    metodo      = ocr_result.get("metodo", ""),
                )
                acta_info["ocr"] = {
                    "metodo":       ocr_result.get("metodo"),
                    "es_escaneado": ocr_result.get("es_escaneado"),
                    "datos":        ocr_result.get("datos_extraidos", {}),
                }
            except Exception as e:
                acta_info["ocr"] = {"error": str(e)}

            # 3. Forense
            try:
                reporte = full_forensic_analysis(pdf_path)
                db.save_forensic_report(acta_id, codigo, id_elec, reporte)
                acta_info["forense"] = {
                    "alertas": reporte.get("alertas", []),
                    "resumen": reporte.get("resumen", ""),
                }
            except Exception as e:
                acta_info["forense"] = {"error": str(e)}

            # 4. Comparación
            try:
                api_data = acta.get("detalle", acta.get("resumen", {}))
                ocr_data = acta_info.get("ocr", {}).get("datos", {}) if acta_info.get("ocr") else {}
                if isinstance(api_data, dict) and isinstance(ocr_data, dict):
                    comp = compare_api_vs_ocr(api_data, ocr_data, codigo)
                    consist = compare_votos_api_vs_grafico(api_data)
                    db.save_comparison(acta_id, codigo, id_elec, comp)
                    acta_info["comparacion"] = {
                        "estado": comp.get("estado"),
                        "discrepancias": len(comp.get("discrepancias", [])),
                        "alertas": comp.get("alertas", []),
                        "consistencia_interna": consist.get("consistente"),
                    }
            except Exception as e:
                acta_info["comparacion"] = {"error": str(e)}

        resultados["descarga"].append(acta_info)

    client.close()
    return _success({
        "codigo_mesa": codigo,
        "actas_procesadas": len(resultados["descarga"]),
        "pdfs_descargados": sum(1 for r in resultados["descarga"] if r.get("pdf_descargado")),
        "resultados": resultados["descarga"],
    })


@app.route("/api/procesar_lote", methods=["POST"])
def api_procesar_lote():
    """
    Procesa todas las mesas de prueba predefinidas.
    """
    resultados = []
    for mesa in MESAS_PRUEBA:
        try:
            # Simplificado: solo consultar API y descargar
            client = ONPEApiClient()
            actas = client.buscar_por_mesa(mesa["codigo"])
            client.close()
            tiene_actas = len(actas) > 0
        except Exception as e:
            tiene_actas = False
            actas = []

        resultados.append({
            "codigo_mesa": mesa["codigo"],
            "categoria": mesa["categoria"],
            "tiene_actas": tiene_actas,
            "num_actas": len(actas),
        })

    return _success(resultados)


# ── API: Datos locales (DB) ───────────────────────────────────────────────────

@app.route("/api/db/stats")
def api_db_stats():
    return _success(db.get_dashboard_stats())


@app.route("/api/db/mesas")
def api_db_mesas():
    return _success(db.get_all_mesas())


@app.route("/api/db/mesa/<codigo_mesa>")
def api_db_mesa(codigo_mesa: str):
    codigo_mesa = codigo_mesa.strip().zfill(6)
    actas = db.get_actas_by_mesa(codigo_mesa)
    mesa  = db.get_mesa(codigo_mesa)
    return _success({"mesa": mesa, "actas": actas})


@app.route("/api/db/ocr/<codigo_mesa>/<int:id_eleccion>")
def api_db_ocr(codigo_mesa: str, id_eleccion: int):
    codigo_mesa = codigo_mesa.strip().zfill(6)
    row = db.get_latest_ocr(codigo_mesa, id_eleccion)
    if not row:
        return _error("Sin datos OCR para esta acta", 404)
    return _success(row)


@app.route("/api/db/forense/<codigo_mesa>/<int:id_eleccion>")
def api_db_forense(codigo_mesa: str, id_eleccion: int):
    codigo_mesa = codigo_mesa.strip().zfill(6)
    row = db.get_latest_forensic(codigo_mesa, id_eleccion)
    if not row:
        return _error("Sin reporte forense para esta acta", 404)
    return _success(row)


@app.route("/api/pdf_preview/<codigo_mesa>/<int:id_eleccion>/<int:page>")
def api_pdf_preview(codigo_mesa: str, id_eleccion: int, page: int):
    """Retorna la página del PDF como imagen base64 para previsualizar en la web."""
    codigo_mesa = codigo_mesa.strip().zfill(6)
    pdf_path = get_local_path(codigo_mesa, id_eleccion, "pdf")
    if not os.path.exists(pdf_path):
        return _error("PDF no encontrado", 404)

    try:
        images = get_page_images(pdf_path, dpi=150)
        if page < 1 or page > len(images):
            return _error(f"Página {page} fuera de rango (1-{len(images)})", 404)
        img_b64 = base64.b64encode(images[page - 1]).decode()
        return _success({
            "page": page,
            "total_pages": len(images),
            "image_b64": img_b64,
        })
    except Exception as e:
        return _error(str(e))


@app.route("/api/pdf_download/<codigo_mesa>/<int:id_eleccion>")
def api_pdf_download(codigo_mesa: str, id_eleccion: int):
    """Descarga directa del PDF guardado localmente."""
    codigo_mesa = codigo_mesa.strip().zfill(6)
    pdf_path = get_local_path(codigo_mesa, id_eleccion, "pdf")
    if not os.path.exists(pdf_path):
        abort(404)
    return send_file(pdf_path, as_attachment=True,
                     download_name=f"acta_{codigo_mesa}_e{id_eleccion}.pdf")


# ── Resultados masivos ────────────────────────────────────────────────────────

@app.route("/resultados")
def page_resultados():
    return render_template("resultados.html")


@app.route("/api/resultados/actas")
def api_resultados_actas():
    page   = max(1, int(request.args.get("page",   1)))
    limit  = min(500, max(10, int(request.args.get("limit", 100))))
    filtro = request.args.get("filtro", "todos")
    mesa   = request.args.get("mesa",   "").strip()
    tipo   = request.args.get("tipo",   "").strip()
    sort   = request.args.get("sort",   "mesa")
    try:
        data = db.get_actas_full_list(
            page=page, limit=limit, filtro=filtro,
            mesa=mesa, tipo_eleccion=tipo, sort=sort,
        )
        return _success(data)
    except Exception as e:
        logger.exception("Error en api_resultados_actas")
        return _error(str(e))


@app.route("/api/resultados/charts")
def api_resultados_charts():
    try:
        return _success(db.get_resultados_charts())
    except Exception as e:
        logger.exception("Error en api_resultados_charts")
        return _error(str(e))


# ── Debug ─────────────────────────────────────────────────────────────────────

@app.route("/api/debug/mesa/<codigo_mesa>")
def api_debug_mesa(codigo_mesa: str):
    """
    Diagnóstico completo para una mesa.
    Muestra: MongoDB IDs, URLs presigned, accesibilidad, body del 403.
    """
    import requests as req_lib
    codigo_mesa = codigo_mesa.strip().zfill(6)
    client = ONPEApiClient()
    debug_info = {"codigo_mesa": codigo_mesa, "actas": []}

    try:
        actas_lista = client.buscar_por_mesa(codigo_mesa)
    except Exception as e:
        client.close()
        return _error(f"Error buscar_por_mesa: {e}")

    for acta in actas_lista[:10]:
        acta_id = acta.get("id")
        id_elec = acta.get("idEleccion")
        info = {
            "acta_id":       acta_id,
            "id_eleccion":   id_elec,
            "estado":        acta.get("descripcionEstadoActa"),
            "mongo_ids":     [],
            "file_url_full": None,
            "file_endpoint": {},
            "download_test": {},
            "error":         None,
        }

        try:
            detalle = client.get_acta_detalle(acta_id)
            info["detalle_keys"] = list(detalle.keys()) if isinstance(detalle, dict) else str(type(detalle))

            # Buscar TODOS los mongo IDs recursivamente
            from modules.api_client import _MONGO_ID_RE
            def _find_all(obj, path="", depth=0):
                found = []
                if depth > 6: return found
                if isinstance(obj, str) and len(obj) == 24 and _MONGO_ID_RE.match(obj):
                    found.append({"value": obj, "path": path})
                elif isinstance(obj, dict):
                    for k, v in obj.items():
                        found.extend(_find_all(v, f"{path}.{k}", depth+1))
                elif isinstance(obj, list):
                    for i, item in enumerate(obj[:10]):
                        found.extend(_find_all(item, f"{path}[{i}]", depth+1))
                return found

            all_hex = _find_all(detalle)
            info["mongo_ids"] = all_hex

            # Intentar obtener URL del endpoint /actas/file
            session = client.get_session()
            mongo_id = all_hex[0]["value"] if all_hex else None

            if mongo_id:
                raw_resp = session.get(
                    f"https://resultadoelectoral.onpe.gob.pe/presentacion-backend/actas/file",
                    params={"id": mongo_id},
                    timeout=10
                )
                info["file_endpoint"] = {
                    "status": raw_resp.status_code,
                    "content_type": raw_resp.headers.get("Content-Type", ""),
                }
                try:
                    resp_json = raw_resp.json()
                    info["file_endpoint"]["json"] = resp_json if isinstance(resp_json, (str, dict)) else str(resp_json)[:500]
                except Exception:
                    info["file_endpoint"]["text"] = raw_resp.text[:500]

                # Extraer URL
                file_url = None
                try:
                    resp_data = raw_resp.json()
                    if isinstance(resp_data, str) and resp_data.startswith("http"):
                        file_url = resp_data
                    elif isinstance(resp_data, dict):
                        # Buscar URL en cualquier campo
                        for k, v in resp_data.items():
                            if isinstance(v, str) and v.startswith("http"):
                                file_url = v
                                info["file_endpoint"]["url_field"] = k
                                break
                except Exception:
                    pass

                if file_url:
                    info["file_url_full"] = file_url  # URL COMPLETA para copiar

                    # Test de descarga con múltiples estrategias
                    test_strategies = [
                        ("sin_headers", {}),
                        ("con_origin", {
                            "Origin": "https://resultadoelectoral.onpe.gob.pe",
                            "Referer": "https://resultadoelectoral.onpe.gob.pe/",
                        }),
                        ("browser_full", {
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                            "Accept": "*/*",
                            "Origin": "https://resultadoelectoral.onpe.gob.pe",
                            "Referer": "https://resultadoelectoral.onpe.gob.pe/",
                            "Sec-Fetch-Dest": "empty",
                            "Sec-Fetch-Mode": "cors",
                            "Sec-Fetch-Site": "cross-site",
                        }),
                    ]

                    for sname, shdrs in test_strategies:
                        try:
                            tr = req_lib.get(file_url, headers=shdrs, timeout=10, stream=True)
                            result = {
                                "status": tr.status_code,
                                "content_type": tr.headers.get("Content-Type", ""),
                                "content_length": tr.headers.get("Content-Length", ""),
                            }
                            if tr.status_code == 403:
                                result["body_403"] = tr.text[:500]
                            elif tr.status_code == 200:
                                # Leer solo los primeros bytes para verificar si es PDF
                                first_bytes = tr.raw.read(10)
                                result["is_pdf"] = first_bytes[:5] == b"%PDF-"
                                result["first_bytes"] = str(first_bytes)
                            info["download_test"][sname] = result
                            tr.close()
                        except Exception as e2:
                            info["download_test"][sname] = {"error": str(e2)}
            else:
                info["file_endpoint"]["note"] = "Sin MongoDB ID — no se puede obtener URL"

        except Exception as e:
            info["error"] = str(e)

        debug_info["actas"].append(info)

    client.close()
    return _success(debug_info)


@app.route("/api/debug/test_url")
def api_debug_test_url():
    """
    Proxy para probar una URL de S3 directamente.
    ?url=https://...&strategy=browser_full
    Retorna los primeros bytes y headers de la respuesta.
    """
    import requests as req_lib
    url = request.args.get("url", "")
    if not url.startswith("https://"):
        return _error("URL inválida")

    strategy = request.args.get("strategy", "browser_full")
    headers_map = {
        "none": {},
        "origin": {
            "Origin": "https://resultadoelectoral.onpe.gob.pe",
            "Referer": "https://resultadoelectoral.onpe.gob.pe/",
        },
        "browser_full": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Origin": "https://resultadoelectoral.onpe.gob.pe",
            "Referer": "https://resultadoelectoral.onpe.gob.pe/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        },
    }
    hdrs = headers_map.get(strategy, {})

    try:
        r = req_lib.get(url, headers=hdrs, timeout=15, stream=True)
        result = {
            "status": r.status_code,
            "headers": dict(list(r.headers.items())[:10]),
        }
        if r.status_code == 403:
            result["body_403"] = r.text[:1000]
        elif r.status_code == 200:
            chunk = r.raw.read(20)
            result["is_pdf"] = chunk[:5] == b"%PDF-"
            result["first_bytes_hex"] = chunk.hex()
            result["content_length"] = r.headers.get("Content-Length")
        r.close()
        return _success(result)
    except Exception as e:
        return _error(str(e))


# ══════════════════════════════════════════════════════════════════════════════
# ── SISTEMA AUTOMÁTICO DE BARRIDO ─────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/auto/config")
def api_auto_config():
    """Retorna la configuración actual del sistema (OCR engine, OCSVM, barrido)."""
    import config as cfg
    docai_ok, docai_msg = gocr_mod.is_configured()
    gemini_key = getattr(cfg, "GEMINI_API_KEY", "")
    return _success({
        "ocr_engine":       getattr(cfg, "OCR_ENGINE", "local"),
        "ocr_engines":      ["local", "google"],
        "google_docai": {
            "configurado": docai_ok,
            "mensaje":     docai_msg,
            "estimacion_costo": gocr_mod.estimate_cost(
                n_mesas=170000, actas_por_mesa=4.2, paginas_por_acta=1.0
            ),
        },
        "ocsvm": {
            "nu":     getattr(cfg, "OCSVM_NU", 0.05),
            "kernel": getattr(cfg, "OCSVM_KERNEL", "rbf"),
            "gamma":  getattr(cfg, "OCSVM_GAMMA", "scale"),
        },
        "barrido": {
            "concurrencia":  getattr(cfg, "BARRIDO_CONCURRENCIA", 3),
            "delay_seg":     getattr(cfg, "BARRIDO_DELAY_SEG", 0.5),
            "rango_inicio":  getattr(cfg, "BARRIDO_RANGO_INICIO", 1),
            "rango_fin":     getattr(cfg, "BARRIDO_RANGO_FIN", 999999),
        },
        "comparacion_modo":  getattr(cfg, "COMPARACION_MODO", "simple"),
        "gemini_configured": bool(gemini_key),
    })


@app.route("/api/google/auth_status")
def api_google_auth_status():
    """
    Diagnóstico completo de autenticación y configuración de Google Cloud / Document AI.

    Solo lectura — no expone secretos. El campo adc_email puede ser None.
    Usado por el panel de configuración del frontend para mostrar estado y pasos sugeridos.
    """
    return _success(gocr_mod.diagnose_auth())


@app.route("/api/auto/config", methods=["POST"])
def api_auto_config_update():
    """
    Actualiza parámetros de configuración en caliente (en memoria).
    Body JSON aceptado: ocr_engine, google_project_id, google_location,
                        google_processor_id, ocsvm_nu, barrido_concurrencia,
                        barrido_delay_seg.

    NOTA DE SEGURIDAD: google_credentials NO se acepta desde el frontend.
    Las credenciales deben configurarse mediante la variable de entorno
    GOOGLE_APPLICATION_CREDENTIALS en el servidor, o usando ADC (gcloud CLI).
    """
    import config as cfg
    body = request.get_json(silent=True) or {}

    # Rechazar explícitamente credenciales desde el frontend
    if "google_credentials" in body:
        return _error(
            "Las credenciales de Google Cloud no se pueden configurar desde el frontend. "
            "Usa la variable de entorno GOOGLE_APPLICATION_CREDENTIALS en el servidor, "
            "o configura ADC con: gcloud auth application-default login"
        ), 400

    if "ocr_engine" in body:
        engine = body["ocr_engine"]
        if engine not in ("local", "google"):
            return _error("ocr_engine debe ser 'local' o 'google'")
        cfg.OCR_ENGINE = engine

    if "ocsvm_nu" in body:
        nu = float(body["ocsvm_nu"])
        if not (0 < nu <= 1):
            return _error("ocsvm_nu debe estar en (0, 1]")
        cfg.OCSVM_NU = nu

    if "google_project_id" in body:
        cfg.GOOGLE_CLOUD_PROJECT_ID = body["google_project_id"]
    if "google_location" in body:
        cfg.GOOGLE_CLOUD_LOCATION = body["google_location"]
    if "google_processor_id" in body:
        cfg.GOOGLE_CLOUD_PROCESSOR_ID = body["google_processor_id"]

    if "barrido_concurrencia" in body:
        cfg.BARRIDO_CONCURRENCIA = int(body["barrido_concurrencia"])
    if "barrido_delay_seg" in body:
        cfg.BARRIDO_DELAY_SEG = float(body["barrido_delay_seg"])

    if "comparacion_modo" in body:
        modo = body["comparacion_modo"]
        if modo not in ("simple", "hibrido", "hibrido_ia"):
            return _error("comparacion_modo debe ser 'simple', 'hibrido' o 'hibrido_ia'")
        cfg.COMPARACION_MODO = modo

    if "gemini_api_key" in body:
        # La clave no se loguea ni expone — solo se guarda en memoria / JSON local
        cfg.GEMINI_API_KEY = body["gemini_api_key"].strip()

    # Persistir en disco para que sobreviva reinicios
    try:
        cfg.save_user_config()
    except Exception as _e:
        logger.warning(f"No se pudo guardar user_config: {_e}")

    return _success({"updated": list(body.keys())})


@app.route("/api/auto/google_ocr/test")
def api_google_ocr_test():
    """Verifica la conexión con Google Document AI."""
    return _success(gocr_mod.test_connection())


@app.route("/api/auto/google_ocr/estimate")
def api_google_ocr_estimate():
    """Estima el costo de procesar el universo electoral con Google OCR."""
    n_mesas       = int(request.args.get("n_mesas", 170000))
    actas_por_mesa = float(request.args.get("actas_por_mesa", 4.2))
    paginas       = float(request.args.get("paginas_por_acta", 1.0))
    return _success(gocr_mod.estimate_cost(n_mesas, actas_por_mesa, paginas))


# ── Barrido individual ────────────────────────────────────────────────────────

@app.route("/api/auto/consultar_mesa")
def api_auto_consultar_mesa():
    """
    Consulta individual rápida de una mesa con clasificación de estado.
    ?codigo_mesa=013064
    Incluye línea de tiempo de las actas.
    """
    codigo = request.args.get("codigo_mesa", "").strip().zfill(6)
    if not codigo or len(codigo) > 6:
        return _error("Parámetro 'codigo_mesa' inválido")

    import config as cfg
    client = ONPEApiClient()
    try:
        actas_raw = client.buscar_por_mesa(codigo)
    except Exception as e:
        client.close()
        return _error(str(e))

    if not actas_raw:
        client.close()
        return _success({
            "codigo_mesa": codigo,
            "status":      auto_mod.STATUS_NO_EXISTE,
            "actas":       [],
            "timeline":    [],
        })

    # Enriquecer con file_url y línea de tiempo
    actas_enriquecidas = []
    timeline = []
    session = client.get_session()

    for acta in actas_raw[:10]:
        acta_id = acta.get("id")
        id_elec = acta.get("idEleccion", 0)
        tipo    = _tipo_eleccion(id_elec)

        file_url = None
        try:
            file_url = client.get_acta_file_url(int(acta_id)) if acta_id else None
        except Exception:
            pass

        # Extraer línea de tiempo desde el detalle del acta
        try:
            detalle = client.get_acta_detalle(int(acta_id)) if acta_id else {}
        except Exception:
            detalle = {}

        acta_info = {
            **acta,
            "file_url":     file_url,
            "tipoEleccion": tipo,
        }
        actas_enriquecidas.append(acta_info)

        # Construir eventos de línea de tiempo
        _build_timeline_events(acta, detalle, tipo, id_elec, timeline)

    client.close()

    status = auto_mod._classify_acta_status(actas_enriquecidas)

    # Anomalía OCSVM si hay datos en DB
    anomalia_info = None
    try:
        db_actas = db.get_actas_by_mesa(codigo)
        if db_actas:
            acta_row = db_actas[0]
            api_data  = json.loads(acta_row.get("api_json") or "{}")
            ocr_row   = db.get_latest_ocr(codigo, acta_row["id_eleccion"])
            forense   = db.get_latest_forensic(codigo, acta_row["id_eleccion"])
            anomalia_info = anomaly_mod.analyze_acta(
                api_data  = api_data,
                ocr_data  = ocr_row.get("datos_json", {}) if ocr_row else {},
                forense   = forense,
            )
    except Exception as e:
        anomalia_info = {"error": str(e)}

    return _success({
        "codigo_mesa":     codigo,
        "status":          status,
        "status_label":    _status_label(status),
        "actas":           actas_enriquecidas,
        "timeline":        sorted(timeline, key=lambda x: x.get("fecha", "")),
        "anomalia":        anomalia_info,
    })


def _build_timeline_events(acta: dict, detalle: dict, tipo: str, id_elec: int, timeline: list):
    """Extrae eventos de la línea de tiempo desde los datos de un acta."""
    # Campos de fecha comunes en la API ONPE
    fecha_fields = [
        ("digitalizacion",  ["fechaDigitalizacion", "fechaDigitacion", "fecha_digitalizacion"]),
        ("escrutinio",      ["fechaEscrutinio", "fecha_escrutinio"]),
        ("envio_jee",       ["fechaEnvioJEE", "fecha_envio_jee"]),
        ("observacion",     ["fechaObservacion"]),
        ("resolucion",      ["fechaResolucion"]),
    ]

    for evento, campos in fecha_fields:
        for campo in campos:
            fecha = acta.get(campo) or detalle.get(campo)
            if fecha:
                timeline.append({
                    "tipo_eleccion": tipo,
                    "id_eleccion":   id_elec,
                    "evento":        evento,
                    "campo":         campo,
                    "fecha":         str(fecha),
                    "descripcion":   acta.get("descripcionEstadoActa") or acta.get("estado", ""),
                })
                break

    # Estado actual siempre va en la línea de tiempo
    estado = acta.get("descripcionEstadoActa") or acta.get("estado", "")
    if estado:
        timeline.append({
            "tipo_eleccion": tipo,
            "id_eleccion":   id_elec,
            "evento":        "estado_actual",
            "fecha":         acta.get("fechaUltimaActualizacion", ""),
            "descripcion":   estado,
            "codigo_estado": acta.get("codigoEstado", ""),
        })


def _status_label(status: str) -> str:
    labels = {
        auto_mod.STATUS_NO_EXISTE:      "No existe en ONPE",
        auto_mod.STATUS_EXISTE_SIN_PDF: "Registrada (sin PDF)",
        auto_mod.STATUS_EXISTE_CON_PDF: "Registrada (con PDF)",
        auto_mod.STATUS_PARA_JEE:       "Para envío al JEE",
        auto_mod.STATUS_CON_ERROR:      "Con error en acta",
        auto_mod.STATUS_OBSERVADA:      "Observada / impugnada",
        auto_mod.STATUS_ERROR_API:      "Error de consulta",
    }
    return labels.get(status, status)


# ── Barrido automático ────────────────────────────────────────────────────────

@app.route("/api/auto/barrido/iniciar", methods=["POST"])
def api_barrido_iniciar():
    """
    Inicia un nuevo job de barrido automático.
    Body JSON:
    {
      "rango_inicio": 1,
      "rango_fin": 999999,
      "descargar_pdf": false,
      "ejecutar_ocr": false,
      "ejecutar_forense": false,
      "ocr_engine": "local",
      "concurrencia": 3,
      "delay_segundos": 0.5
    }
    """
    body = request.get_json(silent=True) or {}

    # Validaciones
    inicio = int(body.get("rango_inicio", 1))
    fin    = int(body.get("rango_fin", 999999))
    if inicio < 1 or fin > 999999 or inicio > fin:
        return _error("rango_inicio y rango_fin deben estar en [1, 999999] y inicio ≤ fin")
    if fin - inicio > 999999:
        return _error("Rango máximo: 999,999 mesas")

    engine = body.get("ocr_engine", OCR_ENGINE)
    if engine == "google":
        ok, msg = gocr_mod.is_configured()
        if not ok:
            return _error(f"Google OCR no configurado: {msg}")

    job = auto_mod.create_job(
        rango_inicio     = inicio,
        rango_fin        = fin,
        descargar_pdf    = bool(body.get("descargar_pdf", False)),
        ejecutar_ocr     = bool(body.get("ejecutar_ocr", False)),
        ejecutar_forense = bool(body.get("ejecutar_forense", False)),
        ocr_engine       = engine,
        concurrencia     = int(body.get("concurrencia", BARRIDO_CONCURRENCIA)),
        delay_segundos   = float(body.get("delay_segundos", BARRIDO_DELAY_SEG)),
        max_vacios_consecutivos = int(body.get("max_vacios_consecutivos", 200)),
        carpeta_destino  = body.get("carpeta_destino") or None,
    )

    # Persistir en DB
    db.upsert_batch_job(
        job_id       = job.job_id,
        config       = job.to_dict(),
        estado       = "ejecutando",
        iniciado_en  = datetime.now().isoformat() if hasattr(job, 'iniciado_en') else None,
    )

    # Callback de progreso: actualizar DB cada 100 mesas
    _counter = {"n": 0}
    def _on_progress(job_dict):
        _counter["n"] += 1
        if _counter["n"] % 100 == 0:
            db.upsert_batch_job(
                job_id     = job_dict["job_id"],
                config     = job_dict,
                estado     = job_dict["estado"],
                progreso   = job_dict["progreso"],
                mesa_actual= job_dict["mesa_actual"],
                stats      = job_dict["stats"],
                finalizado_en = job_dict.get("finalizado_en"),
            )

    auto_mod.start_job_async(job, on_progress=_on_progress)

    return _success({
        "job_id":     job.job_id,
        "estado":     "ejecutando",
        "total_mesas": job.total_mesas,
        "mensaje":    f"Barrido iniciado: mesas {inicio:06d} → {fin:06d} ({job.total_mesas:,} mesas)",
    })


@app.route("/api/auto/barrido/estado/<job_id>")
def api_barrido_estado(job_id: str):
    """Retorna el estado actual de un job de barrido."""
    job = auto_mod.get_job(job_id)
    if job:
        return _success(job.to_dict())
    # Buscar en DB
    row = db.get_batch_job(job_id)
    if row:
        return _success(row)
    return _error(f"Job {job_id} no encontrado", 404)


@app.route("/api/auto/barrido/pausar/<job_id>", methods=["POST"])
def api_barrido_pausar(job_id: str):
    job = auto_mod.get_job(job_id)
    if not job:
        return _error("Job no encontrado", 404)
    job.pause()
    return _success({"job_id": job_id, "estado": "pausado"})


@app.route("/api/auto/barrido/reanudar/<job_id>", methods=["POST"])
def api_barrido_reanudar(job_id: str):
    job = auto_mod.get_job(job_id)
    if not job:
        return _error("Job no encontrado", 404)
    job.resume()
    return _success({"job_id": job_id, "estado": "ejecutando"})


@app.route("/api/auto/barrido/cancelar/<job_id>", methods=["POST"])
def api_barrido_cancelar(job_id: str):
    job = auto_mod.get_job(job_id)
    if not job:
        return _error("Job no encontrado", 404)
    job.cancel()
    return _success({"job_id": job_id, "estado": "cancelado"})


@app.route("/api/auto/barrido/jobs")
def api_barrido_jobs():
    """Lista todos los jobs activos e históricos."""
    activos    = auto_mod.list_jobs()
    historicos = db.get_all_batch_jobs()
    # Unir evitando duplicados
    ids_activos = {j["job_id"] for j in activos}
    combinado   = activos + [h for h in historicos if h["job_id"] not in ids_activos]
    return _success(combinado)


@app.route("/api/auto/barrido/stats")
def api_barrido_stats():
    """Estadísticas agregadas de todos los barridos."""
    return _success(auto_mod.get_barrido_stats())


@app.route("/api/auto/barrido/mesas/<status>")
def api_barrido_mesas_por_estado(status: str):
    """Lista mesas filtradas por estado (PARA_JEE, CON_ERROR_ACTA, etc.)."""
    limit = int(request.args.get("limit", 200))
    mesas = auto_mod.get_mesas_by_status(status.upper(), limit)
    return _success({
        "status":      status.upper(),
        "total":       len(mesas),
        "mesas":       mesas,
    })


# ── Anomalías OCSVM ──────────────────────────────────────────────────────────

@app.route("/api/anomalias/entrenar", methods=["POST"])
def api_anomalias_entrenar():
    """
    Entrena el modelo One-Class SVM con todos los datos en la DB.
    Body opcional: {"nu": 0.05}
    """
    body = request.get_json(silent=True) or {}
    nu   = float(body.get("nu", OCSVM_NU))
    if not (0 < nu <= 1):
        return _error("nu debe estar en (0, 1]")
    result = anomaly_mod.train_from_db(nu=nu)
    return _success(result) if result.get("ok") else _error(result.get("error", "Error entrenando"))


@app.route("/api/anomalias/analizar", methods=["POST"])
def api_anomalias_analizar():
    """
    Analiza una acta específica con el modelo OCSVM.
    Body: {"codigo_mesa": "054938", "id_eleccion": 10}
    """
    body        = request.get_json(silent=True) or {}
    codigo      = body.get("codigo_mesa", "").strip().zfill(6)
    id_eleccion = int(body.get("id_eleccion", 0))
    if not codigo or not id_eleccion:
        return _error("'codigo_mesa' e 'id_eleccion' requeridos")

    actas = db.get_actas_by_mesa(codigo)
    acta_row = next((a for a in actas if a["id_eleccion"] == id_eleccion), None)
    if not acta_row:
        return _error("Acta no encontrada en DB. Descarga primero.", 404)

    api_data    = json.loads(acta_row.get("api_json") or "{}")
    ocr_row     = db.get_latest_ocr(codigo, id_eleccion)
    forense_row = db.get_latest_forensic(codigo, id_eleccion)

    result = anomaly_mod.analyze_acta(
        api_data  = api_data,
        ocr_data  = ocr_row.get("datos_json", {}) if ocr_row else {},
        forense   = forense_row,
    )

    # Guardar en DB
    if result.get("modelo_disponible"):
        db.save_anomaly_result(
            codigo_mesa      = codigo,
            id_eleccion      = id_eleccion,
            is_anomaly       = result.get("is_anomaly", False),
            score            = result.get("score", 0.0),
            score_normalized = result.get("score_normalized", 0.0),
            confidence       = result.get("confidence", "baja"),
            features         = result.get("features", {}),
            explanation      = result.get("explanation", []),
        )

    return _success(result)


@app.route("/api/anomalias/analizar_todas", methods=["POST"])
def api_anomalias_analizar_todas():
    """
    Analiza todas las actas en la DB con el modelo OCSVM.
    Retorna un resumen de anomalías encontradas.
    """
    mesas = db.get_all_mesas()
    resultados = []
    n_anomalias = 0

    for mesa in mesas:
        codigo = mesa["codigo_mesa"]
        actas  = db.get_actas_by_mesa(codigo)
        for acta in actas:
            id_elec     = acta["id_eleccion"]
            api_data    = json.loads(acta.get("api_json") or "{}")
            ocr_row     = db.get_latest_ocr(codigo, id_elec)
            forense_row = db.get_latest_forensic(codigo, id_elec)

            res = anomaly_mod.analyze_acta(
                api_data = api_data,
                ocr_data = ocr_row.get("datos_json", {}) if ocr_row else {},
                forense  = forense_row,
            )

            if res.get("modelo_disponible") and res.get("is_anomaly"):
                n_anomalias += 1
                db.save_anomaly_result(
                    codigo_mesa      = codigo,
                    id_eleccion      = id_elec,
                    is_anomaly       = True,
                    score            = res.get("score", 0.0),
                    score_normalized = res.get("score_normalized", 0.0),
                    confidence       = res.get("confidence", "baja"),
                    features         = res.get("features", {}),
                    explanation      = res.get("explanation", []),
                )
                resultados.append({
                    "codigo_mesa": codigo,
                    "id_eleccion": id_elec,
                    "score":       res.get("score"),
                    "confidence":  res.get("confidence"),
                    "explanation": res.get("explanation", []),
                })

    return _success({
        "total_analizadas": sum(len(db.get_actas_by_mesa(m["codigo_mesa"])) for m in mesas),
        "n_anomalias":      n_anomalias,
        "anomalias":        sorted(resultados, key=lambda x: x.get("score", 0)),
    })


@app.route("/api/acta/refresh_api/<codigo_mesa>/<int:id_eleccion>", methods=["POST"])
def api_acta_refresh_api(codigo_mesa: str, id_eleccion: int):
    """
    Obtiene los datos de la API ONPE para una acta específica,
    los guarda en la DB y los devuelve al cliente.
    """
    codigo_mesa = codigo_mesa.strip().zfill(6)
    if not codigo_mesa or not id_eleccion:
        return _error("Parámetros requeridos")

    try:
        client = ONPEApiClient()
        actas_lista = client.buscar_por_mesa(codigo_mesa)
        if not actas_lista:
            client.close()
            return _error("Mesa no encontrada en la API ONPE")

        # Filtrar la acta por elección
        acta_match = next(
            (a for a in actas_lista if a.get("idEleccion") == id_eleccion), None
        )
        if not acta_match:
            client.close()
            # Intentar con cualquier acta de esa mesa si no hay match exacto
            acta_match = actas_lista[0] if actas_lista else None

        if not acta_match:
            client.close()
            return _error("Acta no encontrada para esa elección")

        acta_id = acta_match.get("id")
        detalle = client.get_acta_detalle(acta_id) if acta_id else {}
        client.close()

        if not detalle:
            return _error("Sin datos de detalle en la API")

        # Guardar api_json actualizado en DB
        db.upsert_acta(
            codigo_mesa   = codigo_mesa,
            id_eleccion   = id_eleccion,
            tipo_eleccion = acta_match.get("tipoEleccion", ""),
            estado        = acta_match.get("descripcionEstadoActa", ""),
            api_json      = detalle,
        )

        return _success({"api_json": detalle})

    except Exception as e:
        logger.exception("Error en refresh api_json")
        return _error(str(e))


def api_anomalias_lista():
    """Retorna todas las anomalías guardadas en DB."""
    solo_positivas = request.args.get("solo_positivas", "1") == "1"
    limit = int(request.args.get("limit", 500))
    rows  = db.get_all_anomalies(only_positive=solo_positivas, limit=limit)
    return _success({
        "total":    len(rows),
        "anomalias": rows,
    })


@app.route("/api/anomalias/stats")
def api_anomalias_stats():
    """Retorna estadísticas agregadas de anomalías."""
    rows = db.get_all_anomalies(only_positive=False, limit=10000)
    total      = len(rows)
    positivas  = sum(1 for r in rows if r.get("is_anomaly"))
    por_conf   = {"alta": 0, "media": 0, "baja": 0}
    for r in rows:
        if r.get("is_anomaly"):
            por_conf[r.get("confidence", "baja")] = por_conf.get(r.get("confidence", "baja"), 0) + 1
    scores = [r["score"] for r in rows if r.get("score") is not None]
    return _success({
        "total_analizadas": total,
        "total_anomalias":  positivas,
        "pct_anomalias":    round(100 * positivas / max(1, total), 1),
        "por_confianza":    por_conf,
        "score_min":        min(scores) if scores else None,
        "score_max":        max(scores) if scores else None,
        "score_media":      round(sum(scores) / len(scores), 4) if scores else None,
    })


# ── Datos para gráficos del dashboard ────────────────────────────────────────

@app.route("/api/charts/distribucion_estados")
def api_charts_distribucion_estados():
    """
    Distribución de mesas por estado de barrido.
    Listo para Chart.js (labels + data).
    """
    stats = auto_mod.get_barrido_stats()
    por_estado = stats.get("por_estado", {})
    labels = list(por_estado.keys())
    data   = list(por_estado.values())
    colors = {
        auto_mod.STATUS_NO_EXISTE:      "#6c757d",
        auto_mod.STATUS_EXISTE_SIN_PDF: "#17a2b8",
        auto_mod.STATUS_EXISTE_CON_PDF: "#28a745",
        auto_mod.STATUS_PARA_JEE:       "#ffc107",
        auto_mod.STATUS_CON_ERROR:      "#fd7e14",
        auto_mod.STATUS_OBSERVADA:      "#dc3545",
        auto_mod.STATUS_ERROR_API:      "#6f42c1",
    }
    bg_colors = [colors.get(l, "#343a40") for l in labels]
    return _success({
        "labels":      labels,
        "data":        data,
        "bg_colors":   bg_colors,
        "tipo":        "doughnut",
        "titulo":      "Distribución de mesas por estado",
    })


@app.route("/api/charts/anomalias_timeline")
def api_charts_anomalias_timeline():
    """
    Anomalías detectadas a lo largo del tiempo.
    Agrupa por fecha de análisis.
    """
    rows = db.get_all_anomalies(only_positive=True, limit=5000)
    por_fecha: Dict[str, int] = {}
    for r in rows:
        fecha = (r.get("fecha_analisis") or "")[:10]
        if fecha:
            por_fecha[fecha] = por_fecha.get(fecha, 0) + 1
    sorted_dates = sorted(por_fecha.keys())
    return _success({
        "labels":  sorted_dates,
        "data":    [por_fecha[d] for d in sorted_dates],
        "tipo":    "line",
        "titulo":  "Anomalías detectadas por fecha",
    })


@app.route("/api/charts/scores_histogram")
def api_charts_scores_histogram():
    """
    Histograma de scores OCSVM de todas las actas analizadas.
    Útil para visualizar la separación inlier/outlier.
    """
    rows   = db.get_all_anomalies(only_positive=False, limit=10000)
    scores = [r["score"] for r in rows if r.get("score") is not None]
    if not scores:
        return _success({"labels": [], "data": [], "tipo": "bar", "titulo": "Histograma de scores OCSVM"})

    import math
    min_s = min(scores)
    max_s = max(scores)
    n_bins = 20
    bin_w  = (max_s - min_s) / n_bins if max_s > min_s else 1
    bins   = [0] * n_bins
    labels = []
    for i in range(n_bins):
        lo = min_s + i * bin_w
        hi = lo + bin_w
        labels.append(f"{lo:.2f}")
        for s in scores:
            if lo <= s < hi:
                bins[i] += 1

    return _success({
        "labels":    labels,
        "data":      bins,
        "threshold": 0.0,  # score < 0 → anomalía
        "tipo":      "bar",
        "titulo":    "Histograma de scores OCSVM (negativo = anómalo)",
    })


@app.route("/api/charts/mesas_por_tipo_eleccion")
def api_charts_mesas_por_tipo():
    """Distribución de actas descargadas por tipo de elección."""
    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT tipo_eleccion, COUNT(*) as cnt FROM actas GROUP BY tipo_eleccion"
        ).fetchall()
    labels = [r["tipo_eleccion"] or "Desconocida" for r in rows]
    data   = [r["cnt"] for r in rows]
    return _success({
        "labels": labels, "data": data,
        "tipo": "bar", "titulo": "Actas por tipo de elección",
    })


@app.route("/api/charts/discrepancias_por_partido")
def api_charts_discrepancias_partido():
    """
    Top partidos con mayor número de discrepancias OCR vs API.
    """
    PARTIDOS_LIST = [
        "ALIANZA PARA EL PROGRESO",
        "AVANZA PAIS",
        "FUERZA POPULAR",
        "PERU LIBRE",
        "SOMOS PERU",
        "PARTIDO DEMOCRATICO SOMOS PERU",
        "PTE-PERU",
        "RENOVACION POPULAR",
        "PARTIDO PAIS PARA TODOS",
    ]
    conteo: Dict[str, int] = {p: 0 for p in PARTIDOS_LIST}

    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT discrepancias_json FROM comparisons WHERE estado='alerta'"
        ).fetchall()

    for row in rows:
        try:
            discrepancias = json.loads(row["discrepancias_json"] or "[]")
            for d in discrepancias:
                partido = d.get("partido", "")
                for p in PARTIDOS_LIST:
                    if p.upper() in partido.upper():
                        conteo[p] += 1
                        break
        except Exception:
            pass

    sorted_items = sorted(conteo.items(), key=lambda x: -x[1])
    labels = [k for k, _ in sorted_items if _ > 0][:15]
    data   = [v for k, v in sorted_items if v > 0][:15]

    return _success({
        "labels": labels, "data": data,
        "tipo": "horizontalBar", "titulo": "Discrepancias OCR-API por partido",
    })


# ── OCR con motor seleccionable ──────────────────────────────────────────────

@app.route("/api/auto/procesar_ocr", methods=["POST"])
def api_auto_procesar_ocr():
    """
    Ejecuta OCR sobre un PDF usando el motor configurado (local o google).
    Body: {"codigo_mesa": "054938", "id_eleccion": 10, "ocr_engine": "local"}
    """
    import config as cfg
    body        = request.get_json(silent=True) or {}
    codigo      = body.get("codigo_mesa", "").strip().zfill(6)
    id_eleccion = int(body.get("id_eleccion", 0))
    engine      = body.get("ocr_engine", getattr(cfg, "OCR_ENGINE", "local"))

    if not codigo or not id_eleccion:
        return _error("'codigo_mesa' e 'id_eleccion' requeridos")

    pdf_path = get_local_path(codigo, id_eleccion, "pdf")
    if not os.path.exists(pdf_path):
        return _error(f"PDF no encontrado: {pdf_path}. Descarga primero el acta.", 404)

    if engine == "google":
        ok, msg = gocr_mod.is_configured()
        if not ok:
            return _error(f"Google OCR no configurado: {msg}")
        resultado_ocr = gocr_mod.process_pdf_with_google(pdf_path)
    else:
        resultado_ocr = process_pdf(pdf_path, dpi=OCR_DPI)

    if "error" in resultado_ocr:
        return _error(resultado_ocr["error"])

    actas = db.get_actas_by_mesa(codigo)
    acta_row = next((a for a in actas if a["id_eleccion"] == id_eleccion), None)
    if acta_row:
        db.save_ocr_result(
            acta_id     = acta_row["id"],
            codigo_mesa = codigo,
            id_eleccion = id_eleccion,
            texto_crudo = resultado_ocr.get("texto_crudo", ""),
            datos       = resultado_ocr.get("datos_extraidos", {}),
            es_escaneado= resultado_ocr.get("es_escaneado", True),
            metodo      = resultado_ocr.get("metodo", engine),
        )

    resultado_web = {k: v for k, v in resultado_ocr.items() if k != "texto_crudo"}
    resultado_web["texto_preview"]      = resultado_ocr.get("texto_crudo", "")[:3000]
    resultado_web["texto_completo_len"] = len(resultado_ocr.get("texto_crudo", ""))
    resultado_web["ocr_engine_usado"]   = engine
    return _success(resultado_web)


# ══════════════════════════════════════════════════════════════════════════════
# ── MÓDULO DE AUDITORÍA ELECTORAL ─────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/auditoria")
def page_auditoria():
    """Página principal del sistema de auditoría."""
    return render_template("auditoria.html")


@app.route("/api/auditoria/resumen")
def api_auditoria_resumen():
    """Resumen rápido de todas las métricas de auditoría."""
    from modules import auditoria as aud
    try:
        return _success(aud.get_resumen_auditoria())
    except Exception as e:
        logger.exception("auditoria/resumen")
        return _error(str(e))


@app.route("/api/auditoria/timeline")
def api_auditoria_timeline():
    """
    Análisis del timeline de todas las actas:
    distribución por hora, día 12 vs 13, velocidad de procesamiento, anomalías.
    """
    from modules import auditoria as aud
    try:
        return _success(aud.get_timeline_stats())
    except Exception as e:
        logger.exception("auditoria/timeline")
        return _error(str(e))


@app.route("/api/auditoria/inconsistencias")
def api_auditoria_inconsistencias():
    """
    Lista actas donde la suma interna de votos no coincide con el total declarado.
    """
    from modules import auditoria as aud
    try:
        data = aud.get_inconsistencias_suma()
        return _success({"total": len(data), "items": data})
    except Exception as e:
        logger.exception("auditoria/inconsistencias")
        return _error(str(e))


@app.route("/api/auditoria/votos-partido")
def api_auditoria_votos_partido():
    """Suma de votos por partido de todas las actas locales."""
    from modules import auditoria as aud
    try:
        data = aud.get_votos_por_partido_local()
        # Convert keys to str for JSON
        result = {str(k): v for k, v in data.items()}
        return _success(result)
    except Exception as e:
        logger.exception("auditoria/votos-partido")
        return _error(str(e))


@app.route("/api/auditoria/anomalias-partido")
def api_auditoria_anomalias_partido():
    """Detecta mesas con distribución anómala de votos por partido (z-score > 2.5σ)."""
    from modules import auditoria as aud
    try:
        data = aud.get_anomalias_por_partido()
        return _success({"total": len(data), "items": data})
    except Exception as e:
        logger.exception("auditoria/anomalias-partido")
        return _error(str(e))


@app.route("/api/auditoria/mesas-sospechosas")
def api_auditoria_mesas_sospechosas():
    """Ranking de mesas más sospechosas según score de señales de alerta."""
    from modules import auditoria as aud
    limit = int(request.args.get("limit", 50))
    try:
        data = aud.get_mesas_sospechosas(limit=limit)
        return _success({"total": len(data), "items": data})
    except Exception as e:
        logger.exception("auditoria/mesas-sospechosas")
        return _error(str(e))


@app.route("/api/auditoria/resumen-general", methods=["GET"])
def api_auditoria_resumen_general():
    """
    Compara suma de votos locales con los totales nacionales de la API ONPE.
    Usa cache si existe, o solicita sincronización.
    """
    from modules import auditoria as aud
    try:
        cached = db.get_all_resumen_general()
        if not cached:
            return _success({
                "cached": False,
                "mensaje": "Sin datos de API general. Usa 'Sincronizar con ONPE' para obtenerlos.",
                "comparacion": {},
            })
        comparacion = aud.compare_local_vs_general(cached)
        return _success({"cached": True, "comparacion": comparacion})
    except Exception as e:
        logger.exception("auditoria/resumen-general")
        return _error(str(e))


@app.route("/api/auditoria/sincronizar-onpe", methods=["POST"])
def api_auditoria_sincronizar():
    """
    Descarga los totales y participantes de la API ONPE para todas las elecciones
    y los guarda en cache local para análisis comparativo.
    """
    ELECCION_NOMBRES_MAP = {
        10: "Presidencial",
        12: "Parlamento Andino",
        13: "Diputados",
        14: "Senadores DEM",
        15: "Senadores DEU",
    }
    from config import ONPE_BASE as _base

    client  = ONPEApiClient()
    result  = {}
    errores = []

    for id_elec, tipo in ELECCION_NOMBRES_MAP.items():
        try:
            # Totales
            totales = client._get(
                f"{_base}/resumen-general/totales",
                params={"idEleccion": id_elec, "tipoFiltro": "eleccion"},
            ) or {}
            # Participantes (votos por partido)
            participantes = client._get(
                f"{_base}/resumen-general/participantes",
                params={"idEleccion": id_elec, "tipoFiltro": "eleccion"},
            ) or []
            if not isinstance(participantes, list):
                participantes = []

            db.save_resumen_general(id_elec, tipo, totales, participantes)
            result[str(id_elec)] = {
                "tipo":             tipo,
                "total_partidos":   len(participantes),
                "actas_contabilizadas": totales.get("actasContabilizadas"),
                "total_votos_validos":  totales.get("totalVotosValidos"),
            }
        except Exception as e:
            errores.append(f"{tipo}: {e}")

    client.close()
    return _success({
        "sincronizado":   result,
        "errores":        errores,
        "total_elecciones": len(result),
    })


@app.route("/api/auditoria/participacion")
def api_auditoria_participacion():
    """Análisis de participación ciudadana vs actas locales."""
    cache = db.get_participacion()
    data  = aud.get_participacion_analysis(cache)
    return _success({**data, "cached": cache is not None})


@app.route("/api/auditoria/sincronizar-participacion", methods=["POST"])
def api_auditoria_sincronizar_participacion():
    """Sincroniza datos de participación ciudadana desde la API ONPE."""
    from config import ONPE_BASE as _base
    client = ONPEApiClient()
    try:
        totales = client._get(
            f"{_base}/participacion-ciudadana/totales",
            params={"tipoFiltro": "total"}
        ) or {}
        ubigeos = client._get(
            f"{_base}/participacion-ciudadana/ubigeos-total",
            params={"tipoFiltro": "total"}
        ) or []
        if not isinstance(ubigeos, list):
            ubigeos = []
        db.save_participacion(totales, ubigeos)
        return _success({
            "total_electores":   totales.get("totalElectoresHabiles"),
            "total_asistentes":  totales.get("totalAsistentes"),
            "pct_asistentes":    totales.get("porcentajeAsistentes"),
            "ambitos":           len(ubigeos),
        })
    except Exception as e:
        return _error(str(e)), 500
    finally:
        client.close()


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════════╗
║   ANALIZADOR FORENSE DE ACTAS ELECTORALES ONPE 2026             ║
║   Herramienta de auditoría local - Uso legal y transparente     ║
╠══════════════════════════════════════════════════════════════════╣
║   Abre tu navegador en: http://localhost:5000                   ║
╚══════════════════════════════════════════════════════════════════╝
    """)
    # Pre-cargar el modelo MNIST en el hilo principal para que los workers
    # del barrido no intenten entrenarlo simultáneamente bajo demanda.
    try:
        from modules.digit_classifier import _get_model
        _get_model()
    except Exception as _e:
        logger.warning(f"Pre-carga MNIST falló (se reintentará en barrido): {_e}")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
