"""
Google Cloud Document AI — Enterprise Document OCR
===================================================
Documentación: https://cloud.google.com/document-ai/docs/enterprise-document-ocr
SDK Python:    google-cloud-documentai >= 2.20.0

Precio Enterprise OCR:
  - Hasta 200,000 páginas: USD 1.50 / 1,000 páginas
  - 92,766 páginas ≈ USD 139.15

═══════════════════════════════════════════════════════
  AUTENTICACIÓN — ORDEN DE PRIORIDAD (backend only)
═══════════════════════════════════════════════════════

  1. Application Default Credentials via gcloud CLI  [RECOMENDADO]
     - Instalar: https://cloud.google.com/sdk/docs/install
     - Autenticar CLI:  gcloud auth login
     - Configurar ADC:  gcloud auth application-default login

  2. Impersonación de cuenta de servicio             [SIN CLAVE PRIVADA]
     - GOOGLE_IMPERSONATE_SERVICE_ACCOUNT=sa@project.iam.gserviceaccount.com

  3. Archivo JSON de cuenta de servicio              [SOLO FALLBACK LOCAL]
     - Variable de entorno: GOOGLE_APPLICATION_CREDENTIALS=/ruta/a/key.json
     - NUNCA se configura desde el frontend
     - NUNCA se sube al repositorio

Variables de entorno necesarias:
  GOOGLE_CLOUD_PROJECT      = "tu-proyecto"         # nombre estándar del SDK
  DOCUMENTAI_LOCATION       = "us"                  # o "eu"
  DOCUMENTAI_PROCESSOR_ID   = "xxxx"                # ID del processor Enterprise OCR
  GOOGLE_APPLICATION_CREDENTIALS = "/ruta/key.json" # solo si fallback JSON

Variables legacy (también soportadas para compatibilidad):
  GOOGLE_CLOUD_PROJECT_ID   = "tu-proyecto"
  GOOGLE_CLOUD_LOCATION     = "us"
  GOOGLE_CLOUD_PROCESSOR_ID = "xxxx"
"""

import io
import os
import logging
import subprocess
from typing import Optional, Dict, List, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

# Procesadores que NO soportan OcrConfig (e.g. Custom Extractor).
# Se rellena en tiempo de ejecución tras el primer fallo 400.
_skip_ocr_config: set = set()

# ── Disponibilidad del SDK ───────────────────────────────────────────────────
try:
    from google.cloud import documentai
    from google.api_core.client_options import ClientOptions
    DOCAI_OK = True
except ImportError:
    DOCAI_OK = False
    logger.warning("google-cloud-documentai no instalado. OCR Enterprise desactivado.")

try:
    from google.oauth2 import service_account
    GOOGLE_AUTH_OK = True
except ImportError:
    GOOGLE_AUTH_OK = False


# ── Configuración ─────────────────────────────────────────────────────────────

def _get_config() -> Dict:
    """
    Lee la configuración de Document AI.

    Prioridad:
      1. Variables de entorno estándar del SDK  (GOOGLE_CLOUD_PROJECT, DOCUMENTAI_*)
      2. Variables de entorno legacy             (GOOGLE_CLOUD_PROJECT_ID, GOOGLE_CLOUD_*)
      3. Atributos de config.py                  (sobreescritos en caliente por la app)

    Las credenciales (JSON de cuenta de servicio) SOLO se leen desde la variable
    de entorno GOOGLE_APPLICATION_CREDENTIALS.  Nunca se aceptan del frontend.
    """
    try:
        import config as cfg
        project_id   = getattr(cfg, "GOOGLE_CLOUD_PROJECT_ID", None)
        location     = getattr(cfg, "GOOGLE_CLOUD_LOCATION", "us")
        processor_id = getattr(cfg, "GOOGLE_CLOUD_PROCESSOR_ID", None)
    except ImportError:
        project_id   = None
        location     = "us"
        processor_id = None

    # Nombres estándar del SDK tienen prioridad
    project_id   = os.environ.get("GOOGLE_CLOUD_PROJECT",      # nombre estándar
                   os.environ.get("GOOGLE_CLOUD_PROJECT_ID",   # legacy
                   project_id))
    location     = os.environ.get("DOCUMENTAI_LOCATION",       # preferido
                   os.environ.get("GOOGLE_CLOUD_LOCATION",     # legacy
                   location or "us"))
    processor_id = os.environ.get("DOCUMENTAI_PROCESSOR_ID",   # preferido
                   os.environ.get("GOOGLE_CLOUD_PROCESSOR_ID", # legacy
                   processor_id))

    # Credenciales: SOLO desde variable de entorno — nunca del frontend
    credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

    return {
        "project_id":   project_id,
        "location":     location,
        "processor_id": processor_id,
        "credentials":  credentials,
    }


def diagnose_auth() -> Dict:
    """
    Diagnóstico completo del estado de autenticación y configuración de Google Cloud.
    Solo lectura — no modifica ningún estado.

    Returns dict con:
      - sdk_instalado          : bool
      - gcloud_cli_disponible  : bool
      - gcloud_account         : str|None  — cuenta activa en gcloud CLI
      - adc_configurado        : bool      — google.auth.default() tuvo éxito
      - adc_modo               : str       — usuario_adc | service_account_json |
                                             service_account_impersonation |
                                             compute_engine | no_configurado | desconocido
      - adc_email              : str|None
      - project_id_configurado : bool
      - project_id             : str|None  (nunca incluye secretos)
      - processor_id_configurado: bool
      - processor_id           : str|None
      - location               : str
      - listo_para_usar        : bool
      - pasos_sugeridos        : list[str]
      - error                  : str|None
    """
    result: Dict = {
        "sdk_instalado":           DOCAI_OK,
        "gcloud_cli_disponible":   False,
        "gcloud_account":          None,
        "adc_configurado":         False,
        "adc_modo":                "no_configurado",
        "adc_email":               None,
        "project_id_configurado":  False,
        "project_id":              None,
        "processor_id_configurado": False,
        "processor_id":            None,
        "location":                "us",
        "listo_para_usar":         False,
        "pasos_sugeridos":         [],
        "error":                   None,
    }

    # ── 1. Google Cloud CLI ───────────────────────────────────────────────────
    try:
        proc = subprocess.run(
            ["gcloud", "config", "get-value", "account"],
            capture_output=True, text=True, timeout=6,
        )
        if proc.returncode == 0:
            result["gcloud_cli_disponible"] = True
            account = proc.stdout.strip()
            if account and account.lower() not in ("(unset)", ""):
                result["gcloud_account"] = account
    except FileNotFoundError:
        pass  # gcloud no instalado — ver pasos_sugeridos
    except Exception as e:
        logger.debug(f"[DocAI diagnose] gcloud check error: {e}")

    # ── 1b. Archivo ADC local (creado por gcloud auth application-default login) ──
    # Ubicación oficial: https://cloud.google.com/docs/authentication/application-default-credentials
    #   Windows : %APPDATA%\gcloud\application_default_credentials.json
    #   Linux/Mac: $HOME/.config/gcloud/application_default_credentials.json
    _adc_file_windows = Path(os.environ.get("APPDATA", "")) / "gcloud" / "application_default_credentials.json"
    _adc_file_unix    = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    result["adc_file_existe"] = _adc_file_windows.exists() or _adc_file_unix.exists()

    # ── 2. Application Default Credentials ───────────────────────────────────
    try:
        import google.auth
        creds, _project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        result["adc_configurado"] = True

        # Detectar modo según tipo de credencial
        creds_type = f"{type(creds).__module__}.{type(creds).__name__}"

        if "impersonated_credentials" in creds_type:
            result["adc_modo"]  = "service_account_impersonation"
            result["adc_email"] = getattr(creds, "service_account_email", None)
        elif "service_account" in creds_type:
            result["adc_modo"]  = "service_account_json"
            result["adc_email"] = getattr(creds, "service_account_email", None)
        elif "compute_engine" in creds_type or "metadata_server" in creds_type:
            result["adc_modo"]  = "compute_engine"
        elif "authorized_user" in creds_type or "oauth2" in creds_type:
            # ADC configurado con gcloud auth application-default login
            result["adc_modo"]  = "usuario_adc"
            # El email está en el token: no lo exponemos, usamos el de gcloud
            result["adc_email"] = result.get("gcloud_account")
        else:
            result["adc_modo"] = f"desconocido"
            logger.debug(f"[DocAI diagnose] tipo de cred desconocido: {creds_type}")

    except ImportError:
        result["error"] = "google-auth no instalado (pip install google-auth)"
    except Exception as e:
        err = str(e).lower()
        if "could not automatically determine" in err or "application default" in err:
            result["adc_modo"] = "no_configurado"
        else:
            result["error"] = str(e)

    # ── 3. Configuración de proyecto / procesador ─────────────────────────────
    cfg = _get_config()
    if cfg["project_id"]:
        result["project_id_configurado"] = True
        result["project_id"]             = cfg["project_id"]
    if cfg["processor_id"]:
        result["processor_id_configurado"] = True
        result["processor_id"]             = cfg["processor_id"]
    result["location"] = cfg.get("location") or "us"

    # ── 4. Listo para usar ────────────────────────────────────────────────────
    result["listo_para_usar"] = (
        result["sdk_instalado"]
        and result["adc_configurado"]
        and result["project_id_configurado"]
        and result["processor_id_configurado"]
    )

    # ── 5. Pasos sugeridos ────────────────────────────────────────────────────
    # Guía precisa según el estado real detectado
    pasos = []

    if not result["sdk_instalado"]:
        pasos.append("Instalar SDK: pip install google-cloud-documentai google-auth")

    if not result["gcloud_cli_disponible"]:
        pasos.append(
            "Instalar Google Cloud CLI para Windows: "
            "https://cloud.google.com/sdk/docs/install-sdk#windows  "
            "(solo necesitas hacer esto una vez)"
        )
        pasos.append(
            "Después de instalar, abre una terminal nueva y ejecuta:  "
            "gcloud init"
        )
        pasos.append(
            "Luego genera el archivo ADC (credenciales para librerías Python):  "
            "gcloud auth application-default login  "
            "→ Esto abrirá el navegador para que inicies sesión con tu cuenta Google"
        )
    else:
        # gcloud instalada — verificar si ya tiene cuenta configurada
        if not result["gcloud_account"]:
            pasos.append(
                "gcloud está instalada pero no tiene cuenta configurada.  "
                "Ejecuta:  gcloud init  (te pedirá iniciar sesión)"
            )

        # Verificar si el archivo ADC local existe
        if not result.get("adc_file_existe") and not result["adc_configurado"]:
            pasos.append(
                "Genera las credenciales ADC para las librerías Python (paso obligatorio):  "
                "gcloud auth application-default login  "
                "→ Esto abrirá el navegador. Completa el inicio de sesión con tu cuenta Google.  "
                "Nota: este comando es distinto de 'gcloud auth login' — ambos son necesarios."
            )
        elif result.get("adc_file_existe") and not result["adc_configurado"]:
            pasos.append(
                "El archivo ADC existe pero google.auth.default() falló. "
                "Prueba regenerarlo:  gcloud auth application-default login"
            )

    if not result["project_id_configurado"]:
        pasos.append("Introduce tu Project ID en el campo de configuración y clic Guardar")
    if not result["processor_id_configurado"]:
        pasos.append(
            "Crea un procesador 'Enterprise Document OCR' en:  "
            "https://console.cloud.google.com/ai/document-ai  "
            "y pega su ID en el campo Processor ID"
        )

    result["pasos_sugeridos"] = pasos
    return result


def is_configured() -> Tuple[bool, str]:
    """
    Verificación rápida (sin ejecutar gcloud) de si Document AI puede usarse.
    Retorna (ok: bool, mensaje: str).
    """
    if not DOCAI_OK:
        return False, "SDK no instalado (pip install google-cloud-documentai)"

    cfg = _get_config()
    if not cfg["project_id"]:
        return False, "Project ID no configurado — guárdalo en el panel de configuración"
    if not cfg["processor_id"]:
        return False, "Processor ID no configurado — guárdalo en el panel de configuración"

    return True, "Configuración OK"


def _build_client(location: str):
    """
    Construye el DocumentProcessorServiceClient usando ADC.

    La autenticación sigue el orden estándar de google.auth.default():
      1. GOOGLE_APPLICATION_CREDENTIALS (JSON de SA como fallback)
      2. ADC de gcloud (gcloud auth application-default login)
      3. Metadata server (Compute Engine / Cloud Run)

    No acepta rutas de credenciales como parámetro — eso lo resuelve el SDK.
    """
    opts = ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
    return documentai.DocumentProcessorServiceClient(client_options=opts)


# ── OCR Enterprise ────────────────────────────────────────────────────────────

def process_pdf_enterprise(
    pdf_path: str,
    enable_native_pdf_parsing: bool = True,
) -> Dict:
    """
    Procesa un PDF con Google Document AI Enterprise Document OCR.

    Args:
        pdf_path: Ruta local al PDF
        enable_native_pdf_parsing: Si True, extrae texto de PDFs digitales
                                   sin hacer OCR (más preciso y barato).

    Returns:
        dict con las claves:
          - texto_crudo: texto completo extraído
          - paginas: lista de páginas con su texto y bloques
          - palabras_totales: int
          - confidence_media: float (0-1)
          - metodo: "google_enterprise_ocr"
          - costo_estimado_usd: float
          - error: str (si hubo error)
    """
    if not DOCAI_OK:
        return {"error": "SDK google-cloud-documentai no instalado"}

    ok, msg = is_configured()
    if not ok:
        return {"error": f"Document AI no configurado: {msg}"}

    cfg = _get_config()

    try:
        client = _build_client(cfg["location"])

        # Leer el PDF
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        # Calcular costo estimado
        # Enterprise OCR: USD 1.50 / 1,000 páginas
        # Estimamos # páginas desde el tamaño del archivo (aprox.)
        file_size_mb = len(pdf_bytes) / (1024 * 1024)
        pages_estimate = max(1, round(file_size_mb / 0.5))   # ~0.5 MB/página escaneada
        costo_estimado = round(pages_estimate * 1.50 / 1000, 4)

        # Construir nombre completo del procesador
        processor_name = client.processor_path(
            cfg["project_id"],
            cfg["location"],
            cfg["processor_id"],
        )

        # Documento inline
        raw_document = documentai.RawDocument(
            content   = pdf_bytes,
            mime_type = "application/pdf",
        )

        # Configuración del proceso
        process_options = documentai.ProcessOptions(
            ocr_config=documentai.OcrConfig(
                enable_native_pdf_parsing = enable_native_pdf_parsing,
                enable_image_quality_scores = True,
                enable_symbol = False,   # No necesitamos carácter por carácter
            )
        )

        # Enviar solicitud
        processor_id = cfg["processor_id"]
        logger.info(f"[DocAI] Enviando {pdf_path} → {cfg['location']} / {processor_id}")

        # Si ya sabemos que este procesador no acepta OcrConfig, omitirla directamente.
        if processor_id in _skip_ocr_config:
            request = documentai.ProcessRequest(
                name         = processor_name,
                raw_document = raw_document,
            )
        else:
            request = documentai.ProcessRequest(
                name            = processor_name,
                raw_document    = raw_document,
                process_options = process_options,
            )
        try:
            response = client.process_document(request=request)
        except Exception as _first_err:
            _first_err_str = str(_first_err)
            # Custom Extractor y algunos procesadores v1.x rechazan OcrConfig.
            # Detectamos por el reason code o por el mensaje de texto.
            # Ref: https://cloud.google.com/document-ai/docs/handle-response
            _needs_retry = (
                "OCR_CONFIG_UNSUPPORTED"         in _first_err_str
                or "ocrconfig"                   in _first_err_str.lower()
                or "entity_types"                in _first_err_str
                or "invalid argument"            in _first_err_str.lower()
            )
            if _needs_retry:
                # Cachear para evitar el round-trip fallido en futuras llamadas
                _skip_ocr_config.add(processor_id)
                logger.warning(
                    f"[DocAI] OcrConfig no compatible con este procesador "
                    f"({_first_err_str[:140]}). "
                    "Reintentando sin process_options (Custom Extractor / procesador v1.x)."
                )
                request_minimal = documentai.ProcessRequest(
                    name         = processor_name,
                    raw_document = raw_document,
                )
                response = client.process_document(request=request_minimal)
            else:
                raise
        document = response.document

        # ── Extraer texto crudo ────────────────────────────────────────────
        texto_crudo    = document.text or ""

        # ── Extraer entidades estructuradas (Custom Extractor) ─────────────
        # Cuando el procesador es un Custom Extractor con foundation model,
        # document.entities contiene los campos definidos en el schema
        # (mesa_de_sufragio, votos, candidatos, etc.).
        entidades_raw: List[Dict] = []
        if hasattr(document, "entities") and document.entities:
            for ent in document.entities:
                ent_dict: Dict = {
                    "tipo":        ent.type_,
                    "texto":       ent.mention_text or "",
                    "confidence":  round(float(ent.confidence), 3),
                    "normalizado": (
                        ent.normalized_value.text
                        if ent.normalized_value and ent.normalized_value.text
                        else None
                    ),
                    "sub_entidades": [
                        {
                            "tipo":   p.type_,
                            "texto":  p.mention_text or "",
                            "conf":   round(float(p.confidence), 3),
                        }
                        for p in (ent.properties or [])
                    ],
                }
                entidades_raw.append(ent_dict)
            logger.info(f"[DocAI] {len(entidades_raw)} entidades extraídas por Custom Extractor")

        # Si el texto crudo está vacío pero hay entidades, construir texto desde ellas
        if not texto_crudo and entidades_raw:
            texto_crudo = "\n".join(
                f"{e['tipo']}: {e['texto']}" for e in entidades_raw
            )

        palabras_total = 0
        confidence_sum = 0.0
        confidence_cnt = 0
        paginas        = []

        for page in document.pages:
            page_text_parts = []
            page_tokens     = 0
            page_blocks     = []

            for block in page.blocks:
                block_text  = _extract_text_from_layout(block.layout, texto_crudo)
                block_conf  = block.layout.confidence if block.layout else 0.0
                page_blocks.append({
                    "texto":      block_text,
                    "confidence": round(float(block_conf), 3),
                })
                page_text_parts.append(block_text)
                confidence_sum += block_conf
                confidence_cnt += 1

            for token in page.tokens:
                page_tokens += 1
                palabras_total += 1

            paginas.append({
                "numero": page.page_number,
                "texto":  " ".join(page_text_parts),
                "tokens": page_tokens,
                "bloques": page_blocks,
                "calidad_imagen": (
                    round(float(page.image_quality_scores.quality_score), 3)
                    if hasattr(page, "image_quality_scores") and page.image_quality_scores
                    else None
                ),
            })

        confidence_media = (confidence_sum / confidence_cnt) if confidence_cnt > 0 else 0.0

        logger.info(
            f"[DocAI] OK: {len(paginas)} págs, {palabras_total} tokens, "
            f"conf={confidence_media:.3f}, ~${costo_estimado} USD"
        )

        return {
            "texto_crudo":         texto_crudo,
            "paginas":             paginas,
            "palabras_totales":    palabras_total,
            "confidence_media":    round(confidence_media, 4),
            "metodo":              "google_enterprise_ocr",
            "costo_estimado_usd":  costo_estimado,
            "n_paginas":           len(paginas),
            "entidades":           entidades_raw,   # vacío si no es Custom Extractor
        }

    except Exception as e:
        logger.error(f"[DocAI] Error procesando {pdf_path}: {e}", exc_info=True)
        return {"error": str(e)}


def _extract_text_from_layout(layout, full_text: str) -> str:
    """Extrae el texto de un Layout usando los text_segments del documento."""
    if not layout or not full_text:
        return ""
    parts = []
    for seg in layout.text_anchor.text_segments:
        start = int(seg.start_index)
        end   = int(seg.end_index)
        parts.append(full_text[start:end])
    return "".join(parts)


# ── Integración con ocr_processor ────────────────────────────────────────────

import re as _re

# Campos del schema del Custom Extractor → nombres estándar internos
_ENTITY_FIELD_MAP = {
    "mesa_de_sufragio":          "mesa",
    "mesa":                      "mesa",
    "total_electores_habiles":   "electores_habiles",
    "electores_habiles":         "electores_habiles",
    "total_ciudadanos_votaron":  "total_votantes",
    "total_votantes":            "total_votantes",
    "total_votos_emitidos":      "votos_emitidos",
    "votos_emitidos":            "votos_emitidos",
    "votos_en_blanco":           "votos_blanco",
    "votos_blanco":              "votos_blanco",
    "votos_blancos":             "votos_blanco",
    "votos_nulos":               "votos_nulos",
    "votos_impugnados":          "votos_impugnados",
    "departamento":              "departamento",
    "provincia":                 "provincia",
    "distrito":                  "distrito",
    "fecha_escrutinio":          "fecha_escrutinio",
    "fecha_fin_escrutinio":      "fecha_fin_escrutinio",
    "hora_inicio":               "hora_inicio",
    "hora_inicio_escrutinio":    "hora_inicio",
    "hora_fin_escrutinio":       "hora_fin",
    "observaciones":             "observaciones",
    "0.000":                     None,    # label reservado del schema, ignorar
}

# Campos que son estrictamente numéricos en el schema
_NUMERIC_FIELDS = {
    "electores_habiles", "total_votantes", "votos_emitidos",
    "votos_blanco", "votos_nulos", "votos_impugnados",
}

# Nombres de partido que NO deben entrar en votos_por_partido
_EXCLUIR_NO_PARTIDOS = {
    "TOTAL EMITIDOS", "TOTAL DE VOTOS EMITIDOS",
    "VOTOS EN BLANCO", "VOTOS BLANCOS", "EN BLANCO",
    "VOTOS NULOS", "NULOS",
    "VOTOS IMPUGNADOS", "IMPUGNADOS",
}


def _clean_int(val: str) -> Optional[int]:
    """
    Convierte un string a entero tolerando errores OCR comunes:
    - Ο (omicron griego U+039F) → 0
    - О (cirílico) → 0
    - O mayúscula sola → 0
    - Comas/puntos en números → ignorar
    Devuelve None si no se puede limpiar o el resultado parece irreal (>999).
    """
    if not val:
        return None
    v = val.strip()
    # Sustituir letras parecidas al 0
    v = v.replace("Ο", "0").replace("О", "0").replace("O", "0")
    # Sustituir letra I/l parecida al 1
    v = v.replace("I", "1").replace("l", "1")
    # Dejar solo dígitos
    digits = _re.sub(r"[^\d]", "", v)
    if not digits:
        return 0           # sin dígitos → 0 votos
    n = int(digits)
    return n if n <= 999 else None


def _extract_num_from_text(mention_text: str) -> Optional[int]:
    """
    Extrae el primer número entero de 1-3 dígitos del mention_text de un `voto`.
    Ej.: "(22 FUERZA POPULAR)" → 22
         "(PARTIDO MORADO 1)"  → 1
         "(FE EN EL PERÚ)"     → None  (no hay número)
    """
    m = _re.search(r'\b(\d{1,3})\b', mention_text)
    if m:
        n = int(m.group(1))
        return n if n <= 999 else None
    return None


def _parse_entities_to_datos(entidades_raw: List[Dict]) -> Dict:
    """
    Convierte las entidades del Custom Extractor al formato estándar del sistema:
      - Campos simples (electores_habiles, votos_nulos, etc.) → nombres internos
      - Entidades `voto` repetidas → votos_por_partido dict
      - Sub-entidades compuestas (miembro_mesa.*) → campo plano
    También calcula votos_validos si no viene directamente.
    """
    datos: Dict = {}
    votos_por_partido: Dict = {}

    for ent in entidades_raw:
        tipo    = ent.get("tipo", "").strip()
        texto   = (ent.get("normalizado") or ent.get("texto") or "").strip()
        subs    = ent.get("sub_entidades", [])

        if tipo in ("voto", "organizacion_politica"):
            # Entidad repetida por partido — dos schemas posibles:
            #   Schema A (legacy):  voto → {organizacion_politica, total_votos}
            #   Schema B (actual):  organizacion_politica → {nombre, total_votos}
            org    = None
            votos  = None

            for sub in subs:
                st = sub.get("tipo", "").strip()
                sv = sub.get("texto", "").strip()
                # nombre del partido puede venir como "organizacion_politica" o "nombre"
                if st in ("organizacion_politica", "nombre"):
                    org = sv.strip()
                elif st == "total_votos":
                    votos = _clean_int(sv)

            # Si no hay sub-entidades (entidad plana), el texto ES el nombre
            if not subs and texto and tipo == "organizacion_politica":
                org = texto

            # Si total_votos no llegó como sub-entidad, intentar extraerlo
            # del mention_text del propio voto (ej. "(22 FUERZA POPULAR)")
            if votos is None and texto:
                votos = _extract_num_from_text(texto)

            if org:
                org_norm = org.strip()
                if org_norm.upper() not in _EXCLUIR_NO_PARTIDOS:
                    # Si sigue siendo None → 0 (OCR no leyó ningún dígito)
                    votos_por_partido[org_norm] = votos if votos is not None else 0

        elif tipo in ("miembro_mesa", "miembro_de_mesa", "personero"):
            # Entidad compuesta con sub-campos: guardar cada sub por separado
            for sub in subs:
                clave = f"{tipo}.{sub.get('tipo', '')}"
                sv    = sub.get("texto", "").strip()
                if sv and clave not in datos:
                    datos[clave] = sv
            # También guardar el mention_text del padre
            if texto:
                datos[tipo] = texto

        else:
            # Campo simple: mapear al nombre estándar
            campo_std = _ENTITY_FIELD_MAP.get(tipo, tipo)
            # Ignorar labels reservados del schema (None en el mapa)
            if campo_std is None:
                continue

            if texto:
                val_parsed = texto
                if campo_std in _NUMERIC_FIELDS:
                    val_parsed = _clean_int(texto)

                # Solo sobreescribir si aún no está (primera entidad gana)
                if campo_std not in datos:
                    datos[campo_std] = val_parsed
                # Guardar también el nombre original del campo para la UI
                if tipo != campo_std and tipo not in datos:
                    datos[tipo] = texto

            # Sub-entidades de campos compuestos no-voto
            for sub in subs:
                clave = f"{tipo}.{sub.get('tipo', '')}"
                sv    = sub.get("texto", "").strip()
                if sv and clave not in datos:
                    datos[clave] = sv

    datos["votos_por_partido"] = votos_por_partido

    # ── Derivar campos que pueden faltar ──────────────────────────────────
    # votos_validos = suma de votos por partido (si no llegó directamente)
    if datos.get("votos_validos") is None and votos_por_partido:
        suma = sum(
            v for v in votos_por_partido.values()
            if isinstance(v, int)
        )
        if suma > 0:
            datos["votos_validos"] = suma

    # total_votantes puede venir como "total_ciudadanos_votaron"
    if datos.get("total_votantes") is None and datos.get("votos_emitidos") is not None:
        datos["total_votantes"] = datos["votos_emitidos"]

    logger.info(
        f"[DocAI] parse_entities: {len(datos)} campos, "
        f"{len(votos_por_partido)} partidos en votos_por_partido"
    )
    return datos


def process_pdf_with_google(pdf_path: str) -> Dict:
    """
    Wrapper que llama a process_pdf_enterprise y adapta la salida
    al mismo formato que devuelve modules.ocr_processor.process_pdf().

    El resultado se puede guardar directamente con db.save_ocr_result().
    """
    raw = process_pdf_enterprise(pdf_path)
    if "error" in raw:
        return {"error": raw["error"], "metodo": "google_enterprise_ocr"}

    texto_crudo    = raw.get("texto_crudo", "")
    entidades_raw  = raw.get("entidades", [])

    # ── Intentar parsear datos de votos ───────────────────────────────────
    # 1) Si hay entidades de Custom Extractor, construir datos_extraidos desde ellas
    # 2) Fallback: parsear el texto crudo con el parser estándar
    datos_extraidos: Dict = {}
    es_escaneado = False

    if entidades_raw:
        # Parsear entidades del Custom Extractor con la lógica correcta
        # (maneja entidades `voto` repetidas, limpia errores OCR en dígitos)
        datos_extraidos = _parse_entities_to_datos(entidades_raw)

    # Fallback: si no hay partidos de entidades, intentar parsear el texto crudo
    if not datos_extraidos.get("votos_por_partido") and texto_crudo:
        try:
            from modules.ocr_processor import _parse_electronic_pdf
            parsed = _parse_electronic_pdf(texto_crudo)
            if parsed:
                # Merge: entidades tienen prioridad, texto llena los huecos
                for k, v in parsed.items():
                    if k == "votos_por_partido":
                        if not datos_extraidos.get("votos_por_partido"):
                            datos_extraidos[k] = v
                    elif k not in datos_extraidos or datos_extraidos[k] is None:
                        datos_extraidos[k] = v
        except Exception as e:
            logger.warning(f"Error parseando texto Google OCR como fallback: {e}")

    return {
        "texto_crudo":         texto_crudo,
        "datos_extraidos":     datos_extraidos,
        "es_escaneado":        es_escaneado,
        "metodo":              "google_enterprise_ocr",
        "confidence_media":    raw.get("confidence_media"),
        "costo_estimado_usd":  raw.get("costo_estimado_usd"),
        "n_paginas":           raw.get("n_paginas", 1),
        "paginas_detalle":     raw.get("paginas", []),
        "entidades":           entidades_raw,
    }


# ── Estimador de costo ─────────────────────────────────────────────────────────

def estimate_cost(
    n_mesas: int = 999999,
    actas_por_mesa: float = 4.2,
    paginas_por_acta: float = 1.0,
) -> Dict:
    """
    Estima el costo de procesar mesas con Google Enterprise OCR.

    Precio: USD 1.50 / 1,000 páginas (hasta 200,000 páginas)
    Mesas del universo electoral peruano: ~170,000 mesas activas de ~999,999 posibles.
    Estimado real con actas reales: ~92,766 páginas → ~USD 139.15
    """
    n_paginas_total = n_mesas * actas_por_mesa * paginas_por_acta
    costo_usd       = n_paginas_total * 1.50 / 1000.0
    return {
        "n_mesas_asumidas":   n_mesas,
        "actas_por_mesa":     actas_por_mesa,
        "paginas_por_acta":   paginas_por_acta,
        "n_paginas_total":    round(n_paginas_total),
        "costo_usd":          round(costo_usd, 2),
        "precio_por_1k":      1.50,
        "tier":               "Enterprise OCR (hasta 200,000 págs)",
        "nota":               "Estimado: ~92,766 páginas reales → ~USD 139.15"
    }


# ── Test de conexión ──────────────────────────────────────────────────────────

def test_connection() -> Dict:
    """Verifica que la configuración y credenciales son válidas."""
    ok, msg = is_configured()
    if not ok:
        return {"ok": False, "error": msg}

    cfg = _get_config()
    try:
        client  = _build_client(cfg["location"])
        parent  = f"projects/{cfg['project_id']}/locations/{cfg['location']}"
        # Listar procesadores disponibles
        procs = list(client.list_processors(parent=parent))
        proc_names = [p.display_name for p in procs[:10]]
        return {
            "ok":          True,
            "project_id":  cfg["project_id"],
            "location":    cfg["location"],
            "procesadores": proc_names,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
