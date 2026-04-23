"""
Configuración central para el Analizador de Actas ONPE
Elecciones Generales 2026 - Herramienta de auditoría local
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
ACTAS_DIR = os.path.join(DATA_DIR, "actas")
OCR_DIR = os.path.join(DATA_DIR, "ocr")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")
DB_PATH = os.path.join(DATA_DIR, "actas.db")

# ─── ONPE API ─────────────────────────────────────────────────────────────────
ONPE_BASE = "https://resultadoelectoral.onpe.gob.pe/presentacion-backend"

ENDPOINTS = {
    "proceso_activo":  f"{ONPE_BASE}/proceso/proceso-electoral-activo",
    "elecciones":      f"{ONPE_BASE}/proceso/{{idProceso}}/elecciones",
    "buscar_mesa":     f"{ONPE_BASE}/actas/buscar/mesa",
    "buscar_dni":      f"{ONPE_BASE}/actas/buscar/dni",
    "acta_detalle":    f"{ONPE_BASE}/actas/{{id}}",
    "acta_file":       f"{ONPE_BASE}/actas/file",
    "resumen_totales":    f"{ONPE_BASE}/resumen-general/totales",
    "resumen_participantes": f"{ONPE_BASE}/resumen-general/participantes",
    "resumen_elecciones": f"{ONPE_BASE}/resumen-general/elecciones",
    "distrito_electoral": f"{ONPE_BASE}/distrito-electoral/distritos",
    "mesa_totales":       f"{ONPE_BASE}/mesa/totales",
}

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "es-419,es;q=0.9",
    "Referer": "https://resultadoelectoral.onpe.gob.pe/main/actas",
    "content-type": "application/json",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

# ─── MESAS DE PRUEBA con clasificación ───────────────────────────────────────
MESAS_PRUEBA = [
    {
        "codigo": "054938",
        "alias": "054938/053938",
        "categoria": "normal",
        "descripcion": "PTE-Perú tenía los 61 votos, NO Renovación Popular. Denuncia desmentida.",
    },
    {
        "codigo": "053938",
        "alias": "053938 (variante)",
        "categoria": "normal",
        "descripcion": "Variante del caso 054938. Verificador mezcla ambos números.",
    },
    {
        "codigo": "013571",
        "alias": "013571 (Jaén, Cajamarca)",
        "categoria": "sospecha",
        "descripcion": "RP alegó nulidad por falta de firmas/DNI; JEE declaró improcedente por no pagar tasa. Acta digitalizada SÍ tiene los datos.",
    },
    {
        "codigo": "013064",
        "alias": "013064 (Huambos, Chota)",
        "categoria": "sospecha",
        "descripcion": "Casilleros vacíos donde debía ir 0, dudas sobre firmas. Sin resolución final pública.",
    },
    {
        "codigo": "033019",
        "alias": "033019 (Colegio Excelencia, Chiclayo)",
        "categoria": "sospecha",
        "descripcion": "PBG: 26 votos en acta del local vs 0 en acta digitalizada. Sin resolución final.",
    },
    {
        "codigo": "050618",
        "alias": "050618 (Surco, cajas halladas)",
        "categoria": "incidencia",
        "descripcion": "ONPE reconoció que esta mesa estaba en cajas fuera de resguardo. Votos consignados en acta procesada.",
    },
    {
        "codigo": "050619",
        "alias": "050619 (Surco, cajas halladas)",
        "categoria": "incidencia",
        "descripcion": "Misma incidencia que 050618.",
    },
    {
        "codigo": "050620",
        "alias": "050620 (Surco, cajas halladas)",
        "categoria": "incidencia",
        "descripcion": "Misma incidencia que 050618.",
    },
    {
        "codigo": "050627",
        "alias": "050627 (Surco, cajas halladas)",
        "categoria": "incidencia",
        "descripcion": "Misma incidencia que 050618.",
    },
    {
        "codigo": "036940",
        "alias": "036940 (PDF electrónico)",
        "categoria": "analisis",
        "descripcion": "PDF generado electrónicamente con firma digital. No es un escán físico.",
    },
    {
        "codigo": "027420",
        "alias": "027420 (Acta física normal)",
        "categoria": "referencia",
        "descripcion": "Acta física escaneada normal, sirve como referencia.",
    },
    {
        "codigo": "055709",
        "alias": "055709 (Impugnada → JEE)",
        "categoria": "observada",
        "descripcion": "Acta impugnada enviada al JEE para resolución.",
    },
    {
        "codigo": "050569",
        "alias": "050569 (Sin firmas → JEE)",
        "categoria": "observada",
        "descripcion": "Acta sin firmas enviada al JEE.",
    },
]

CATEGORIA_COLORES = {
    "normal":     "#28a745",
    "sospecha":   "#ffc107",
    "incidencia": "#fd7e14",
    "observada":  "#dc3545",
    "analisis":   "#17a2b8",
    "referencia": "#6c757d",
}

CATEGORIA_LABELS = {
    "normal":     "Normal / Desmentida",
    "sospecha":   "Sospecha / Disputa no resuelta",
    "incidencia": "Incidencia real de custodia",
    "observada":  "Observada / Enviada al JEE",
    "analisis":   "Para análisis forense",
    "referencia": "Referencia",
}

# Tipos de elección y sus IDs — verificados contra /proceso/2/elecciones
ELECCION_IDS = {
    "Presidencial":         10,
    "Senadores DEU":        15,   # Distrito Electoral Único (Nacional)
    "Senadores DEM":        14,   # Distrito Electoral Múltiple (Regional)
    "Diputados":            13,
    "Parlamento Andino":    12,
}

# Timeout para peticiones HTTP (segundos)
REQUEST_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 120

# Resolución DPI para conversión de PDF a imagen (OCR + forensics)
OCR_DPI = 300
FORENSICS_DPI = 600   # Alta resolución para detectar puntos amarillos

# ─── Motor OCR (selector global) ─────────────────────────────────────────────
# Opciones: "local" | "google"
#   "local"  → usa Tesseract + MNIST CNN (actual, gratuito)
#   "google" → usa Google Cloud Document AI Enterprise OCR (USD 1.50/1000 págs)
OCR_ENGINE = "local"

# ─── Google Cloud Document AI ────────────────────────────────────────────────
#
# AUTENTICACIÓN (backend — no configures credenciales desde el frontend):
#
#   Opción A - Recomendada (ADC con gcloud CLI):
#     1. Instalar gcloud CLI: https://cloud.google.com/sdk/docs/install
#     2. gcloud auth login
#     3. gcloud auth application-default login
#
#   Opción B - Fallback (solo si no puedes usar gcloud):
#     Definir variable de entorno ANTES de iniciar la app:
#       Windows:  set GOOGLE_APPLICATION_CREDENTIALS=C:\ruta\a\key.json
#       Linux:    export GOOGLE_APPLICATION_CREDENTIALS=/ruta/a/key.json
#
# Variables estándar del SDK (toman precedencia sobre los valores de abajo):
#   GOOGLE_CLOUD_PROJECT       = "tu-proyecto"
#   DOCUMENTAI_LOCATION        = "us"
#   DOCUMENTAI_PROCESSOR_ID    = "xxxx"
#   GOOGLE_APPLICATION_CREDENTIALS = "/ruta/key.json"  # solo fallback

# Valores de respaldo en config.py (se sobreescriben con las env vars de arriba)
GOOGLE_CLOUD_PROJECT_ID   = ""   # ej. "mi-proyecto-gcp"
GOOGLE_CLOUD_LOCATION     = "us" # "us" o "eu"
GOOGLE_CLOUD_PROCESSOR_ID = ""   # ID del procesador Enterprise Document OCR
# NOTA: GOOGLE_APPLICATION_CREDENTIALS nunca se define aquí — usar variable de entorno

# ─── Parámetros del sistema automático de barrido ────────────────────────────
BARRIDO_CONCURRENCIA  = 3      # hilos simultáneos (cuidado con rate-limits de ONPE)
BARRIDO_DELAY_SEG     = 0.5    # segundos de espera entre peticiones
BARRIDO_RANGO_INICIO  = 1      # mesa de inicio por defecto
BARRIDO_RANGO_FIN     = 999999 # mesa de fin por defecto

# ─── One-Class SVM ─────────────────────────────────────────────────────────
OCSVM_NU     = 0.05   # fracción esperada de outliers (5%)
OCSVM_KERNEL = "rbf"
OCSVM_GAMMA  = "scale"

# ─── Modo de comparación OCR ──────────────────────────────────────────────────
# "simple"     → API vs último OCR procesado (comportamiento original)
# "hibrido"    → API vs OCR local + OCR Google; OK si al menos uno coincide
# "hibrido_ia" → igual que hibrido, pero si ambos OCR fallan → análisis por Gemini Flash 2.5
COMPARACION_MODO = "simple"

# ─── Gemini Flash 2.5 (para modo hibrido_ia) ─────────────────────────────────
# Obtener clave en https://aistudio.google.com/
GEMINI_API_KEY = ""

# ─── Persistencia de configuración de usuario ────────────────────────────────
_USER_CONFIG_PATH = os.path.join(DATA_DIR, "user_config.json")

def load_user_config():
    """Carga configuración persistida por el usuario desde JSON."""
    import json
    if not os.path.exists(_USER_CONFIG_PATH):
        return
    try:
        with open(_USER_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        global OCR_ENGINE, GOOGLE_CLOUD_PROJECT_ID, GOOGLE_CLOUD_LOCATION
        global GOOGLE_CLOUD_PROCESSOR_ID, BARRIDO_CONCURRENCIA, BARRIDO_DELAY_SEG, OCSVM_NU
        global COMPARACION_MODO, GEMINI_API_KEY
        if "ocr_engine" in cfg:
            OCR_ENGINE = cfg["ocr_engine"]
        if "google_cloud_project_id" in cfg:
            GOOGLE_CLOUD_PROJECT_ID = cfg["google_cloud_project_id"]
        if "google_cloud_location" in cfg:
            GOOGLE_CLOUD_LOCATION = cfg["google_cloud_location"]
        if "google_cloud_processor_id" in cfg:
            GOOGLE_CLOUD_PROCESSOR_ID = cfg["google_cloud_processor_id"]
        if "barrido_concurrencia" in cfg:
            BARRIDO_CONCURRENCIA = int(cfg["barrido_concurrencia"])
        if "barrido_delay_seg" in cfg:
            BARRIDO_DELAY_SEG = float(cfg["barrido_delay_seg"])
        if "ocsvm_nu" in cfg:
            OCSVM_NU = float(cfg["ocsvm_nu"])
        if "comparacion_modo" in cfg:
            COMPARACION_MODO = cfg["comparacion_modo"]
        if "gemini_api_key" in cfg:
            GEMINI_API_KEY = cfg["gemini_api_key"]
    except Exception:
        pass  # Si el archivo está corrupto, ignorar

def save_user_config():
    """Guarda la configuración actual en JSON para persistirla entre reinicios."""
    import json
    import sys
    # Importar el módulo config actual (este mismo módulo) para leer los valores en vivo
    mod = sys.modules[__name__]
    cfg = {
        "ocr_engine":               getattr(mod, "OCR_ENGINE", "local"),
        "google_cloud_project_id":  getattr(mod, "GOOGLE_CLOUD_PROJECT_ID", ""),
        "google_cloud_location":    getattr(mod, "GOOGLE_CLOUD_LOCATION", "us"),
        "google_cloud_processor_id":getattr(mod, "GOOGLE_CLOUD_PROCESSOR_ID", ""),
        "barrido_concurrencia":     getattr(mod, "BARRIDO_CONCURRENCIA", 3),
        "barrido_delay_seg":        getattr(mod, "BARRIDO_DELAY_SEG", 0.5),
        "ocsvm_nu":                 getattr(mod, "OCSVM_NU", 0.05),
        "comparacion_modo":         getattr(mod, "COMPARACION_MODO", "simple"),
        "gemini_api_key":           getattr(mod, "GEMINI_API_KEY", ""),
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(_USER_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

# Cargar config persistida al importar este módulo
load_user_config()
