"""
Cliente Gemini Flash 2.5 para análisis final de actas electorales.

Se usa en modo 'hibrido_ia' cuando los dos OCR (local + Google) discrepan
con los datos de la API ONPE.  El modelo recibe la imagen del acta + los
números de referencia y decide si hay anomalía real.
"""

import base64
import json
import logging
import re
from typing import Dict, Optional

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


# ── Conversión PDF → imagen base64 ───────────────────────────────────────────

def _pdf_page_to_base64(pdf_path: str, page_index: int = 0, dpi: int = 180) -> Optional[str]:
    """Convierte una página del PDF a JPEG en base64."""
    try:
        doc = fitz.open(pdf_path)
        if page_index >= len(doc):
            page_index = 0
        page = doc[page_index]
        mat  = fitz.Matrix(dpi / 72, dpi / 72)
        pix  = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("jpeg")
        doc.close()
        return base64.b64encode(img_bytes).decode("utf-8")
    except Exception as e:
        logger.error(f"[gemini] Error convirtiendo PDF a imagen: {e}")
        return None


# ── Construcción del prompt ───────────────────────────────────────────────────

def _build_prompt(api_data: Dict, ocr_local: Dict, ocr_google: Dict) -> str:
    electores     = api_data.get("totalElectoresHabiles", "?")
    total_emit    = api_data.get("totalVotosEmitidos",    "?")
    total_validos = api_data.get("totalVotosValidos",     "?")

    partidos_api = {}
    for item in api_data.get("detalle", []):
        nom = (item.get("descripcion") or item.get("adDescripcion") or "").strip()
        v   = item.get("nvotos") if item.get("nvotos") is not None else item.get("adVotos")
        if v is not None and nom:
            partidos_api[nom] = int(v)

    def _fmt_partidos(d: dict) -> str:
        if not d:
            return "  (sin datos)"
        return "\n".join(f"  {k}: {v}" for k, v in d.items())

    lineas_api    = _fmt_partidos(partidos_api)
    lineas_local  = _fmt_partidos(ocr_local.get("votos_por_partido") or {})
    lineas_google = _fmt_partidos(ocr_google.get("votos_por_partido") or {})

    return (
        "Eres un auditor electoral. Analiza esta imagen de un acta electoral peruana ONPE 2026.\n\n"
        "DATOS DE REFERENCIA:\n\n"
        f"API ONPE (datos digitalizados oficiales):\n"
        f"  Electores hábiles:     {electores}\n"
        f"  Total votos emitidos:  {total_emit}\n"
        f"  Votos válidos:         {total_validos}\n"
        f"  Votos por partido:\n{lineas_api}\n\n"
        f"OCR Local (Tesseract/CNN):\n"
        f"  Total votantes: {ocr_local.get('total_votantes', '?')}\n"
        f"  Votos válidos:  {ocr_local.get('votos_validos', '?')}\n"
        f"  Partidos:\n{lineas_local}\n\n"
        f"OCR Google Document AI:\n"
        f"  Total votantes: {ocr_google.get('total_votantes', '?')}\n"
        f"  Votos válidos:  {ocr_google.get('votos_validos', '?')}\n"
        f"  Partidos:\n{lineas_google}\n\n"
        "TAREA:\n"
        "1. Lee directamente los números escritos en el acta de la imagen.\n"
        "2. Compáralos con los datos de la API ONPE.\n"
        "3. Determina si hay anomalía real (números alterados, tachones, correcciones, "
        "diferencias entre lo escrito y lo que dice la API).\n"
        "4. Responde ÚNICAMENTE con JSON con este formato exacto:\n"
        '{"anomalia": true/false, "confianza": "alta/media/baja", '
        '"anotacion": "descripción breve de lo que observas en el acta"}'
    )


# ── Parseo de respuesta ───────────────────────────────────────────────────────

def _parse_response(text: str) -> Dict:
    m = re.search(r'\{[^{}]*"anomalia"[^{}]*\}', text, re.DOTALL | re.IGNORECASE)
    if m:
        try:
            data = json.loads(m.group(0))
            return {
                "anomalia":  bool(data.get("anomalia", False)),
                "confianza": str(data.get("confianza", "")).lower(),
                "anotacion": str(data.get("anotacion", ""))[:800],
                "error":     None,
            }
        except Exception:
            pass
    # Fallback: devolver texto crudo como anotación
    return {
        "anomalia":  None,
        "confianza": "baja",
        "anotacion": text[:600],
        "error":     None,
    }


# ── Función principal ─────────────────────────────────────────────────────────

def analizar_con_gemini(
    pdf_path: str,
    api_data: Dict,
    ocr_local: Dict,
    ocr_google: Dict,
    api_key: str,
) -> Dict:
    """
    Envía la primera página del acta (imagen) + datos de referencia
    a Gemini Flash 2.5 para análisis forense final.

    Retorna:
        {"anomalia": bool|None, "confianza": str, "anotacion": str, "error": str|None}
    """
    if not api_key:
        return {"anomalia": None, "confianza": "", "anotacion": "",
                "error": "GEMINI_API_KEY no configurada"}

    img_b64 = _pdf_page_to_base64(pdf_path)
    if not img_b64:
        return {"anomalia": None, "confianza": "", "anotacion": "",
                "error": "No se pudo convertir el PDF a imagen"}

    prompt = _build_prompt(api_data, ocr_local, ocr_google)

    try:
        import google.generativeai as genai  # importación tardía — opcional

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")

        response = model.generate_content([
            {
                "parts": [
                    {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
                    {"text": prompt},
                ]
            }
        ])

        text = response.text.strip() if response.text else ""
        return _parse_response(text)

    except ImportError:
        return {"anomalia": None, "confianza": "", "anotacion": "",
                "error": "Paquete 'google-generativeai' no instalado. Ejecuta: pip install google-generativeai"}
    except Exception as e:
        logger.error(f"[gemini] Error llamando a Gemini: {e}")
        return {"anomalia": None, "confianza": "", "anotacion": "", "error": str(e)}
