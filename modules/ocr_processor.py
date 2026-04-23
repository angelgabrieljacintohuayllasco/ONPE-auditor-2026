"""
Procesador OCR para actas electorales ONPE 2026.
Soporta:
  - PDFs con texto seleccionable (PyMuPDF directo)
  - PDFs escaneados (PyMuPDF render → preprocesamiento → Tesseract OCR)
Extrae: votos por partido, totales, metadata del acta.

Preprocesamiento de imagen:
  1. Escala a alto DPI (400)
  2. Conversión a escala de grises
  3. Binarización adaptativa (Otsu + adaptiveThreshold)
  4. Eliminación de ruido (morphological opening)
  5. OCR multi-pasada: texto completo + zona de tabla + dígitos
"""

import re
import logging
import os
import numpy as np
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
    PYMUPDF_OK = True
except ImportError:
    PYMUPDF_OK = False
    logger.warning("PyMuPDF no instalado. OCR limitado.")

try:
    import pytesseract
    from PIL import Image, ImageFilter, ImageEnhance
    import io
    TESSERACT_OK = True
    # En Windows, busca Tesseract en la ruta estándar
    tesseract_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(tesseract_path):
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
except ImportError:
    TESSERACT_OK = False
    logger.warning("pytesseract/Pillow no instalado. OCR por imagen no disponible.")

# Verificar si scipy está disponible (para limpieza morfológica)
try:
    from scipy.ndimage import binary_erosion, binary_dilation
    SCIPY_OK = True
except ImportError:
    SCIPY_OK = False
    logger.info("scipy no disponible — se usará limpieza básica PIL")


# ──────────────────────────────────────────────────────────────────────────────
# Partidos oficiales EG2026 (orden en acta)
# ──────────────────────────────────────────────────────────────────────────────
PARTIDOS_ACTA = [
    (1,  "ALIANZA ELECTORAL VENCEREMOS"),
    (2,  "PARTIDO PATRIOTICO DEL PERU"),
    (3,  "PARTIDO CIVICO OBRAS"),         # "PARTIDO CÍVICO OBRAS"
    (4,  ""),
    (5,  "PARTIDO DEMOCRATA VERDE"),
    (6,  "PARTIDO DEL BUEN GOBIERNO"),
    (7,  "PARTIDO POLITICO PERU ACCION"),
    (8,  "PARTIDO POLITICO PRIN"),
    (9,  "PROGRESEMOS"),
    (10, "PARTIDO SICREO"),                # OCR leía "BIOREO" pero es SICREO
    (11, "PARTIDO PAIS PARA TODOS"),
    (12, "FRENTE DE LA ESPERANZA 2021"),
    (13, "PARTIDO POLITICO NACIONAL PERU LIBRE"),
    (14, ""),
    (15, "PRIMERO LA GENTE"),              # "PRIMERO LA GENTE - COMUNIDAD..."
    (16, "JUNTOS POR EL PERU"),
    (17, "PODEMOS PERU"),
    (18, "PARTIDO DEMOCRATICO FEDERAL"),
    (19, "FE EN EL PERU"),
    (20, "PARTIDO POLITICO INTEGRIDAD DEMOCRATICA"),
    (21, "FUERZA POPULAR"),
    (22, "ALIANZA PARA EL PROGRESO"),
    (23, "PARTIDO POLITICO COOPERACION POPULAR"),
    (24, "AHORA NACION - AN"),
    (25, "LIBERTAD POPULAR"),
    (26, "UN CAMINO DIFERENTE"),
    (27, "AVANZA PAIS"),
    (28, "PERU MODERNO"),
    (29, "PARTIDO POLITICO PERU PRIMERO"),
    (30, "SALVEMOS AL PERU"),
    (31, "PARTIDO DEMOCRATICO SOMOS PERU"),
    (32, "PARTIDO APRISTA PERUANO"),
    (33, "RENOVACION POPULAR"),
    (34, "PARTIDO DEMOCRATA UNIDO PERU"),
    (35, "FUERZA Y LIBERTAD"),
    (36, "PTE - PERU"),                    # "PARTIDO DE LOS TRABAJADORES Y EMPRENDEDORES PTE - PERÚ"
    (37, "UNIDAD NACIONAL"),
    (38, "PARTIDO MORADO"),
]

# ──────────────────────────────────────────────────────────────────────────────
# Patrones regex — flexibles para tolerar errores OCR
# ──────────────────────────────────────────────────────────────────────────────

# Mesa: busca "054938" o similar cerca de "MESA"
RE_MESA = re.compile(
    r'(?:MESA|N[°º.]?\s*(?:DE\s+)?(?:SUFRAGIO|MESA))[:\s.]*[NnºO°]*[:\s.]*(\d{5,6})',
    re.IGNORECASE
)
RE_MESA_ALT = re.compile(r'(\d{6})\s*[-–]\s*\d{2}\s*[-–]\s*[A-Z]', re.IGNORECASE)  # "054938-05-V"

# Electores hábiles — muy flexible
RE_ELECTORES = re.compile(
    r'(?:TOTAL\s*(?:DE\s*)?)?ELECTORES?\s*H[ÁAa]BILES?\s*[:\s|]+(\d+)',
    re.IGNORECASE
)
RE_ELECTORES_ALT = re.compile(r'ELECTORESHABILES\s*[|:]\s*(\d+)', re.IGNORECASE)

# Total de ciudadanos que votaron
RE_VOTANTES = re.compile(
    r'(?:TOTAL\s*(?:DE\s*)?)?(?:CIUDADANOS\s*QUE\s*VOTARON|VOTANTES|ASISTENTES)\s*[:\s|]+(\d+)',
    re.IGNORECASE
)
RE_VOTANTES_ALT = re.compile(r'VOTARON\s*[:\s|]*(\d+)', re.IGNORECASE)

# Total de votos emitidos
RE_EMITIDOS = re.compile(
    r'TOTAL\s*(?:DE\s*)?VOTOS?\s*EMITIDOS?\s*[:\s→|]+(\d+)',
    re.IGNORECASE
)

# Votos válidos
RE_VALIDOS = re.compile(r'VOTOS?\s*V[ÁAa]LIDOS?\s*[:\s|]+(\d+)', re.IGNORECASE)

# Votos nulos
RE_NULOS = re.compile(r'VOTOS?\s*(?:EN\s+)?NULOS?\s*[:\s|]+(\d+)', re.IGNORECASE)

# Votos en blanco
RE_BLANCO = re.compile(r'(?:VOTOS?\s*(?:EN\s+)?)?BLANCO\s*[:\s|]+(\d+)', re.IGNORECASE)

# Votos impugnados
RE_IMPUGNADOS = re.compile(r'VOTOS?\s*IMPUGNADOS?\s*[:\s|]+(\d+)', re.IGNORECASE)

# Línea: número de fila + texto + votos al final
RE_FILA_VOTOS = re.compile(
    r'^\s*(\d{1,2})\s+(.{3,60}?)\s+(\d{1,4})\s*$',
    re.MULTILINE
)

# Línea genérica: texto largo + número(s) al final
RE_VOTOS_LINEA = re.compile(r'(.{5,60}?)\s+(\d{1,4})\s*$', re.MULTILINE)

# DNI
RE_DNI = re.compile(r'D\.?N\.?I\.?\s*[:\s]?\s*(\d{8})', re.IGNORECASE)

# Hora
RE_HORA = re.compile(
    r'(?:HORA|las)\s*[:\s]*(\d{1,2})\s*[:h]\s*(\d{2})',
    re.IGNORECASE
)

# Firma/miembro
RE_FIRMA = re.compile(r'FIRMA|RUBRICA|MIEMBRO|PRESIDENTE|SECRETARIO|ESCRUTADOR|PERSONERO', re.IGNORECASE)


# ──────────────────────────────────────────────────────────────────────────────
# Preprocesamiento de imagen
# ──────────────────────────────────────────────────────────────────────────────

def _preprocess_for_ocr(img: Image.Image, strategy: str = "adaptive") -> Image.Image:
    """
    Preprocesa una imagen PIL para mejorar OCR.
    Estrategias:
      - "adaptive": binarización Otsu (mejor para actas)
      - "contrast": alto contraste + sharpen
      - "raw": sin procesamiento
    """
    if strategy == "raw":
        return img.convert("L") if img.mode != "L" else img

    gray = img.convert("L")

    if strategy == "contrast":
        enhancer = ImageEnhance.Contrast(gray)
        gray = enhancer.enhance(2.5)
        enhancer = ImageEnhance.Sharpness(gray)
        gray = enhancer.enhance(2.0)
        return gray

    # strategy == "adaptive"
    arr = np.array(gray)
    threshold = _otsu_threshold(arr)
    binary = ((arr > threshold) * 255).astype(np.uint8)

    # Limpieza morfológica si scipy está disponible
    if SCIPY_OK:
        try:
            binary = _morphological_clean(binary)
        except Exception:
            pass

    return Image.fromarray(binary)


def _otsu_threshold(arr: np.ndarray) -> int:
    """Calcula umbral Otsu sin OpenCV."""
    hist, _ = np.histogram(arr.flatten(), bins=256, range=(0, 256))
    total = arr.size
    sum_total = np.sum(np.arange(256) * hist)

    best_thresh = 0
    best_var = 0
    sum_bg = 0
    weight_bg = 0

    for t in range(256):
        weight_bg += hist[t]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += t * hist[t]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_total - sum_bg) / weight_fg
        var_between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if var_between > best_var:
            best_var = var_between
            best_thresh = t

    return best_thresh


def _morphological_clean(binary: np.ndarray, kernel_size: int = 2) -> np.ndarray:
    """Limpieza morfológica (erosión + dilatación) para eliminar ruido."""
    from scipy.ndimage import binary_erosion, binary_dilation
    kernel = np.ones((kernel_size, kernel_size), dtype=bool)
    mask = binary > 128
    cleaned = binary_dilation(binary_erosion(mask, structure=kernel), structure=kernel)
    return (cleaned * 255).astype(np.uint8)


# ──────────────────────────────────────────────────────────────────────────────
# Zonas del acta electoral ONPE EG2026 (proporciones normalizadas 0-1)
# Calibradas desde imagen real: acta presidencial formato 4b
# ──────────────────────────────────────────────────────────────────────────────

# Encabezado: ONPE logo, código barras, N° mesa, electores hábiles, depto/prov/dist
ZONA_HEADER = {"x1": 0.0, "y1": 0.0, "x2": 0.65, "y2": 0.13}

# Tabla completa de partidos (filas 1-38 + nombres + números)
ZONA_TABLA = {"x1": 0.01, "y1": 0.145, "x2": 0.55, "y2": 0.82}

# Solo nombres de partidos (col izquierda de la tabla, sin números)
ZONA_PARTIDOS_COL = {"x1": 0.02, "y1": 0.22, "x2": 0.45, "y2": 0.81}

# Solo columna de votos (dígitos manuscritos en casillas de 3 dígitos)
# Medido por detección de bordes: x=0.457-0.547, empezar bajo la línea de ejemplo
ZONA_VOTOS_COL = {"x1": 0.455, "y1": 0.22, "x2": 0.55, "y2": 0.81}

# Totales: votos en blanco, nulos, impugnados, total emitidos, ciudadanos que votaron
ZONA_TOTALES = {"x1": 0.01, "y1": 0.80, "x2": 0.56, "y2": 0.95}


def _crop_zone(img: Image.Image, zona: dict) -> Image.Image:
    """Recorta una zona de la imagen usando proporciones normalizadas."""
    w, h = img.size
    box = (
        int(zona["x1"] * w),
        int(zona["y1"] * h),
        int(zona["x2"] * w),
        int(zona["y2"] * h),
    )
    return img.crop(box)


def _detect_table_rows(img_gray: np.ndarray, w: int, h: int,
                       x_left_norm: float = 0.460,
                       x_right_norm: float = 0.544,
                       y_min_norm: float = 0.14,
                       y_max_norm: float = 0.86) -> List[Tuple[int, int]]:
    """
    Detecta las filas de la tabla de partidos buscando bordes horizontales
    dentro de la columna de dígitos.

    Retorna lista de (y_top, y_bottom) para cada fila de partido.
    Filtra filas muy pequeñas (< 50px) que son sub-bordes de la fila de ejemplo.
    """
    x_left = int(x_left_norm * w)
    x_right = int(x_right_norm * w)
    y_min = int(y_min_norm * h)
    y_max = int(y_max_norm * h)

    # Promedio de brillo por fila horizontal en la franja de dígitos
    stripe = img_gray[y_min:y_max, x_left:x_right]
    row_avg = stripe.mean(axis=1)

    # Filas oscuras = bordes (promedio < 160)
    dark_mask = row_avg < 160
    dark_indices = np.where(dark_mask)[0] + y_min

    if len(dark_indices) < 2:
        return []

    # Agrupar píxeles oscuros consecutivos
    borders = []
    start = dark_indices[0]
    prev = dark_indices[0]
    for y in dark_indices[1:]:
        if y - prev > 3:
            borders.append((start, prev))
            start = y
        prev = y
    borders.append((start, prev))

    # Extraer filas entre bordes, filtrar las muy pequeñas
    rows = []
    min_row_height = 50  # Mínimo para ser una fila de partido real
    for i in range(len(borders) - 1):
        y_top = borders[i][1] + 1
        y_bot = borders[i + 1][0] - 1
        if y_bot - y_top >= min_row_height:
            rows.append((y_top, y_bot))

    return rows


def _ocr_digit_cells(img_high: Image.Image, rows: List[Tuple[int, int]],
                     x_left_norm: float = 0.460,
                     x_right_norm: float = 0.544,
                     debug_dir: str = None) -> List[Optional[int]]:
    """
    OCR celda por celda: para cada fila detectada, recorta la casilla de
    dígitos, limpia las líneas internas de la grilla, y lee el número.

    Retorna lista de valores (int o None) por cada fila.
    """
    w, h = img_high.size
    x_left = int(x_left_norm * w)
    x_right = int(x_right_norm * w)

    results = []
    for idx, (y_top, y_bot) in enumerate(rows):
        # Recortar celda con margen interior (evitar bordes)
        margin_y = max(5, int((y_bot - y_top) * 0.10))
        margin_x = 8
        cell = img_high.crop((
            x_left + margin_x,
            y_top + margin_y,
            x_right - margin_x,
            y_bot - margin_y
        ))

        # Convertir a grayscale y binarizar
        cell_gray = np.array(cell.convert("L"))
        # Alto contraste para captar trazos manuscritos
        threshold = _otsu_threshold(cell_gray)
        # Binarizar: negro=texto, blanco=fondo
        binary = ((cell_gray > threshold - 10) * 255).astype(np.uint8)

        # Detectar y remover líneas verticales internas
        # (separadores de casillas de centenas/decenas/unidades)
        cell_h, cell_w = binary.shape
        for x in range(cell_w):
            col = binary[:, x]
            dark_count = np.sum(col < 128)
            # Si >60% de la columna es oscura, es una línea vertical → blanquear
            if dark_count > cell_h * 0.60:
                binary[: , max(0, x-1):min(cell_w, x+2)] = 255

        cell_img = Image.fromarray(binary)

        # Guardar debug
        if debug_dir:
            cell_img.save(os.path.join(debug_dir, f"cell_{idx+1:02d}.png"))

        # OCR: PSM 7 = single text line, solo dígitos
        try:
            txt = pytesseract.image_to_string(
                cell_img,
                config="--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789"
            ).strip()

            # Si PSM 7 falla, probar PSM 13 (raw line)
            if not txt:
                txt = pytesseract.image_to_string(
                    cell_img,
                    config="--oem 3 --psm 13 -c tessedit_char_whitelist=0123456789"
                ).strip()

            digits = re.sub(r'[^\d]', '', txt)
            if digits:
                val = int(digits)
                results.append(val if val <= 999 else None)
                logger.info(f"    Cell {idx+1:2d}: OCR='{txt}' → {val}")
            else:
                results.append(0)  # Casilla vacía = 0 votos
                logger.info(f"    Cell {idx+1:2d}: vacía (OCR='{txt}')")
        except Exception as e:
            results.append(None)
            logger.warning(f"    Cell {idx+1:2d}: Error OCR: {e}")

    return results


# ──────────────────────────────────────────────────────────────────────────────
# Extracción de texto
# ──────────────────────────────────────────────────────────────────────────────

def _extract_text_pymupdf(pdf_path: str) -> Tuple[str, bool]:
    """Extrae texto directo de un PDF con PyMuPDF."""
    if not PYMUPDF_OK:
        return "", True

    doc = fitz.open(pdf_path)
    texto_total = []
    for page in doc:
        texto_total.append(page.get_text("text"))
    doc.close()

    texto = "\n".join(texto_total).strip()
    es_escaneado = len(texto) < 100
    logger.info(f"PyMuPDF texto directo: {len(texto)} chars, escaneado={es_escaneado}")
    return texto, es_escaneado


def _parse_electronic_pdf(text: str) -> Dict:
    """
    Parsea el texto embebido de un PDF electrónico de acta ONPE.
    Formato: nombre de partido en una línea, votos en la siguiente.
    Retorna dict estructurado con todos los campos.
    """
    import unicodedata

    result = {
        "mesa": None,
        "electores_habiles": None,
        "total_votantes": None,
        "votos_validos": None,
        "votos_nulos": None,
        "votos_blanco": None,
        "votos_impugnados": None,
        "votos_emitidos": None,
        "firmas_detectadas": [],
        "dnis_detectados": [],
        "horas_detectadas": [],
        "votos_por_partido": {},
    }

    def _norm(s):
        s = unicodedata.normalize('NFD', s)
        return ''.join(c for c in s if unicodedata.category(c) != 'Mn').upper().strip()

    # --- Metadata ---
    m = RE_MESA.search(text) or RE_MESA_ALT.search(text)
    if m:
        result["mesa"] = m.group(1).zfill(6)

    for pat in [RE_ELECTORES, RE_ELECTORES_ALT]:
        m = pat.search(text)
        if m:
            result["electores_habiles"] = int(m.group(1))
            break

    for pat in [RE_VOTANTES, RE_VOTANTES_ALT]:
        m = pat.search(text)
        if m:
            result["total_votantes"] = int(m.group(1))
            break

    # --- Votos por partido ---
    # El texto embebido tiene líneas alternadas: nombre_partido / votos
    lines = text.split('\n')
    votos = {}

    # Buscar los nombres de PARTIDOS_ACTA en el texto y capturar el número
    # que aparece en la línea siguiente
    for i, line in enumerate(lines):
        lu = line.strip()
        if not lu:
            continue

        # Totales especiales
        lu_upper = lu.upper()
        if i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            next_is_num = re.fullmatch(r'\d{1,4}', next_line)
        else:
            next_is_num = None

        if next_is_num:
            val = int(next_is_num.group())
            if 'VOTOS EN BLANCO' in lu_upper:
                result["votos_blanco"] = val
                votos['VOTOS EN BLANCO'] = val
                continue
            elif 'VOTOS NULOS' in lu_upper or lu_upper == 'VOTOS NULOS':
                result["votos_nulos"] = val
                votos['VOTOS NULOS'] = val
                continue
            elif 'VOTOS IMPUGNADOS' in lu_upper:
                result["votos_impugnados"] = val
                votos['VOTOS IMPUGNADOS'] = val
                continue
            elif 'TOTAL DE VOTOS EMITIDOS' in lu_upper:
                result["votos_emitidos"] = val
                votos['TOTAL EMITIDOS'] = val
                continue
            elif 'CIUDADANOS QUE VOTARON' in lu_upper:
                result["total_votantes"] = val
                continue

    # Mapear partidos: buscar cada partido oficial en el texto
    from difflib import SequenceMatcher

    # Construir lista de (line_idx, partido_idx) donde el nombre del partido
    # aparece en el texto, y la línea siguiente es un número
    for pidx, (pnum, pname) in enumerate(PARTIDOS_ACTA):
        if not pname:
            continue
        pname_norm = _norm(pname)

        best_line = -1
        best_ratio = 0.0
        for i, line in enumerate(lines):
            ln = _norm(line.strip())
            if len(ln) < 4:
                continue
            # Saltar líneas que son solo números o etiquetas de totales
            if re.fullmatch(r'\d{1,4}', line.strip()):
                continue
            if any(kw in ln for kw in ['VOTOS EN BLANCO', 'VOTOS NULOS',
                                        'VOTOS IMPUGNADOS', 'TOTAL DE VOTOS',
                                        'ORGANIZACIONES', 'MESA DE SUFRAGIO',
                                        'ACTA ELECTORAL', 'ELECCIONES',
                                        'OBSERVACIONES', 'FIRMA', 'PERSONERO']):
                continue

            ratio = SequenceMatcher(None, pname_norm, ln).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_line = i

        if best_ratio > 0.55 and best_line >= 0 and best_line + 1 < len(lines):
            next_l = lines[best_line + 1].strip()
            m_num = re.fullmatch(r'\d{1,4}', next_l)
            if m_num:
                votos[pname] = int(m_num.group())
                logger.info(f"  ✓ {pname} = {votos[pname]} (ratio={best_ratio:.2f})")

    result["votos_por_partido"] = votos

    # Horas
    result["horas_detectadas"] = [f"{m[0]}:{m[1]}" for m in RE_HORA.findall(text)]
    result["firmas_detectadas"] = list(set(RE_FIRMA.findall(text)))
    result["dnis_detectados"] = RE_DNI.findall(text)

    logger.info(f"  Electronic PDF: mesa={result['mesa']}, "
                f"electores={result['electores_habiles']}, "
                f"partidos={len(votos)}, votantes={result['total_votantes']}")
    return result


def _extract_text_ocr(pdf_path: str, dpi: int = 300) -> str:
    """
    OCR por zonas: recorta las regiones clave del acta y aplica
    OCR especializado a cada una.

    Estrategia:
      1. Header a DPI normal → extraer mesa, electores
      2. Tabla completa → extraer nombres de partidos con votos
      3. Columna de votos a DPI alto (500) → dígitos manuscritos
      4. Totales → blanco, nulos, impugnados, emitidos, ciudadanos que votaron
      5. Página completa → fallback
    """
    if not PYMUPDF_OK or not TESSERACT_OK:
        return ""

    doc = fitz.open(pdf_path)
    all_texts = []

    for page_num, page in enumerate(doc):
        logger.info(f"OCR página {page_num + 1}/{len(doc)}")

        textos_zona = []

        # ── Renderizar a DPI normal (300) para texto impreso ──
        mat_normal = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat_normal, colorspace=fitz.csRGB)
        img_full = Image.open(io.BytesIO(pix.tobytes("png")))
        w, h = img_full.size
        logger.info(f"  Imagen {w}x{h} px ({dpi} DPI)")

        # ── Renderizar a DPI alto (500) para dígitos manuscritos ──
        dpi_digits = 500
        mat_high = fitz.Matrix(dpi_digits / 72, dpi_digits / 72)
        pix_high = page.get_pixmap(matrix=mat_high, colorspace=fitz.csRGB)
        img_high = Image.open(io.BytesIO(pix_high.tobytes("png")))
        wh, hh = img_high.size
        logger.info(f"  Imagen alta res {wh}x{hh} px ({dpi_digits} DPI)")

        # ── Guardar crops de debug ──
        debug_dir = os.path.join(os.path.dirname(pdf_path), "debug_ocr")
        os.makedirs(debug_dir, exist_ok=True)

        # ── ZONA 1: HEADER ──
        try:
            img_header = _crop_zone(img_full, ZONA_HEADER)
            img_header.save(os.path.join(debug_dir, f"p{page_num+1}_header.png"))
            img_h_proc = _preprocess_for_ocr(img_header, "contrast")
            txt_header = pytesseract.image_to_string(
                img_h_proc, config="--oem 3 --psm 6 -l spa+eng"
            ).strip()
            logger.info(f"  [HEADER] {len(txt_header)} chars: {txt_header[:120]!r}")
            textos_zona.append(f"--- ENCABEZADO ---\n{txt_header}")
        except Exception as e:
            logger.warning(f"  [HEADER] Error: {e}")

        # ── ZONA 2: TABLA COMPLETA (nombre + votos en misma línea) ──
        try:
            img_tabla = _crop_zone(img_full, ZONA_TABLA)
            img_tabla.save(os.path.join(debug_dir, f"p{page_num+1}_tabla.png"))
            # Probar con contraste alto y psm 6 (bloque uniforme de texto)
            img_t_proc = _preprocess_for_ocr(img_tabla, "contrast")
            txt_tabla = pytesseract.image_to_string(
                img_t_proc, config="--oem 3 --psm 6 -l spa+eng"
            ).strip()
            logger.info(f"  [TABLA] {len(txt_tabla)} chars: {txt_tabla[:150]!r}")
            textos_zona.append(f"--- TABLA DE PARTIDOS ---\n{txt_tabla}")
        except Exception as e:
            logger.warning(f"  [TABLA] Error: {e}")

        # ── ZONA 3: VOTOS POR FILA (CNN MNIST + filtro template) ──
        try:
            from modules.digit_classifier import classify_all_rows, classify_3digit_cell

            # Usar imagen alta resolución para detectar bordes y leer dígitos
            arr_gray = np.array(img_high.convert("L"))
            hh_full, wh_full = arr_gray.shape

            dark_threshold = 160
            y_start_detect = int(0.14 * hh_full)
            y_end_detect = int(0.93 * hh_full)  # Extendido para capturar filas de totales

            # ── Auto-detección del rango X de la columna de votos ──
            # Distintos tipos de elección (Senadores, Diputados, Parlamento Andino)
            # tienen la columna de dígitos en posiciones X distintas.
            # Probamos varios rangos candidatos y elegimos el que detecta más bordes.
            _candidate_x = [
                (0.456, 0.546),   # calibrado e12 Parlamento Andino
                (0.500, 0.590),
                (0.480, 0.570),
                (0.520, 0.610),
                (0.440, 0.530),
                (0.560, 0.650),
                (0.400, 0.490),
            ]

            def _count_borders(xl_n, xr_n):
                xl = int(xl_n * wh_full)
                xr = int(xr_n * wh_full)
                _stripe = arr_gray[:, xl:xr]
                _ravg = _stripe.mean(axis=1)
                _dark = np.where(_ravg < dark_threshold)[0]
                _dark = _dark[(_dark >= y_start_detect) & (_dark <= y_end_detect)]
                if len(_dark) == 0:
                    return 0
                n = 1
                prev = _dark[0]
                for y in _dark[1:]:
                    if y - prev > 3:
                        n += 1
                    prev = y
                return n

            best_xl_n, best_xr_n = _candidate_x[0]
            best_n_borders = _count_borders(*_candidate_x[0])
            for xl_n, xr_n in _candidate_x[1:]:
                nb = _count_borders(xl_n, xr_n)
                if nb > best_n_borders:
                    best_n_borders = nb
                    best_xl_n, best_xr_n = xl_n, xr_n

            x_left = int(best_xl_n * wh_full)
            x_right = int(best_xr_n * wh_full)
            logger.info(f"  [VOTOS] Columna X detectada: {best_xl_n:.3f}-{best_xr_n:.3f}")

            # Calcular promedio de brillo por fila en la franja elegida
            stripe = arr_gray[:, x_left:x_right]
            row_avg = stripe.mean(axis=1)

            # Encontrar bordes horizontales (filas con promedio oscuro)
            dark_rows_mask = row_avg < dark_threshold

            dark_indices = np.where(dark_rows_mask)[0]
            dark_indices = dark_indices[(dark_indices >= y_start_detect) & (dark_indices <= y_end_detect)]

            h_borders = []
            if len(dark_indices) > 0:
                start = dark_indices[0]
                prev = dark_indices[0]
                for yy in dark_indices[1:]:
                    if yy - prev > 3:
                        h_borders.append((start, prev))
                        start = yy
                    prev = yy
                h_borders.append((start, prev))

            logger.info(f"  [VOTOS-FILAS] {len(h_borders)} bordes horizontales detectados")

            # Calcular filas entre bordes
            all_rows = []
            for i in range(len(h_borders) - 1):
                top = h_borders[i][1] + 2
                bot = h_borders[i + 1][0] - 2
                row_h = bot - top
                if row_h > 50:  # Solo filas reales (>50px)
                    all_rows.append((top, bot))

            # Fila [0] = cabecera ejemplo, [1]-[38] = 38 partidos, [39+] = totales
            if len(all_rows) > 39:
                party_rows = all_rows[1:39]   # 38 partidos
                totals_rows = all_rows[39:]    # blanco, nulos, impugnados, emitidos, total
            elif len(all_rows) > 38:
                party_rows = all_rows[1:39]
                totals_rows = []
            else:
                party_rows = all_rows[:38]
                totals_rows = []

            logger.info(f"  [VOTOS-FILAS] {len(party_rows)} partidos, {len(totals_rows)} totales (de {len(all_rows)} total)")

            # Guardar imagen de la columna completa para debug
            img_votos_full = img_high.crop((x_left, y_start_detect, x_right, y_end_detect))
            img_votos_full.save(os.path.join(debug_dir, f"p{page_num+1}_votos.png"))

            # Clasificar filas de partidos con CNN MNIST + filtro template
            row_results = classify_all_rows(
                full_img=img_high,
                x_left=x_left,
                x_right=x_right,
                party_rows=party_rows,
                debug_dir=debug_dir,
            )

            digit_results = []
            for row_idx, (val, details) in enumerate(row_results):
                partido_num = row_idx + 1
                if val is not None:
                    logger.info(f"    Fila {partido_num:2d}: MNIST → {val}  {details}")
                    digit_results.append(str(val))
                else:
                    digit_results.append("")

            votos_text = "\n".join(digit_results)
            filled = [d for d in digit_results if d]
            logger.info(f"  [VOTOS-FILAS] {len(filled)} valores detectados: {filled}")
            textos_zona.append(f"--- COLUMNA VOTOS (DIGITOS) ---\n{votos_text}")

            # Clasificar filas de totales (sin filtro template)
            TOTALS_LABELS = ["VOTOS EN BLANCO", "VOTOS NULOS", "VOTOS IMPUGNADOS",
                             "TOTAL DE VOTOS EMITIDOS", "TOTAL CIUDADANOS VOTARON"]
            totals_text_parts = []
            for ti, (ttop, tbot) in enumerate(totals_rows):
                label = TOTALS_LABELS[ti] if ti < len(TOTALS_LABELS) else f"TOTAL FILA {ti+1}"
                val, details = classify_3digit_cell(
                    cell_img=None, x_left=x_left, x_right=x_right,
                    y_top=ttop, y_bot=tbot, full_img=img_high,
                    debug_dir=debug_dir, row_idx=38 + ti + 1,
                )
                if val is not None:
                    logger.info(f"    {label}: MNIST → {val}  {details}")
                    totals_text_parts.append(f"{label} {val}")
                else:
                    logger.info(f"    {label}: no detectado  {details}")
            if totals_text_parts:
                textos_zona.append(f"--- TOTALES MNIST ---\n" + "\n".join(totals_text_parts))
        except Exception as e:
            logger.warning(f"  [VOTOS] Error: {e}", exc_info=True)

        # ── ZONA 4: TOTALES (blanco, nulos, impugnados, emitidos, votaron) ──
        try:
            img_totales = _crop_zone(img_full, ZONA_TOTALES)
            img_totales.save(os.path.join(debug_dir, f"p{page_num+1}_totales.png"))
            img_tot_proc = _preprocess_for_ocr(img_totales, "contrast")
            txt_totales = pytesseract.image_to_string(
                img_tot_proc, config="--oem 3 --psm 6 -l spa+eng"
            ).strip()
            logger.info(f"  [TOTALES] {len(txt_totales)} chars: {txt_totales[:150]!r}")
            textos_zona.append(f"--- TOTALES ---\n{txt_totales}")
        except Exception as e:
            logger.warning(f"  [TOTALES] Error: {e}")

        # ── ZONA 5: PÁGINA COMPLETA (fallback para regex) ──
        try:
            img_full_proc = _preprocess_for_ocr(img_full.copy(), "contrast")
            txt_full = pytesseract.image_to_string(
                img_full_proc, config="--oem 3 --psm 4 -l spa+eng"
            ).strip()
            logger.info(f"  [FULL] {len(txt_full)} chars, primeras líneas:")
            for fl in txt_full.split('\n')[:10]:
                if fl.strip():
                    logger.info(f"    > {fl.strip()[:100]}")
            textos_zona.append(f"--- PAGINA COMPLETA ---\n{txt_full}")
        except Exception as e:
            logger.warning(f"  [FULL] Error: {e}")

        combined = "\n\n".join(textos_zona)
        all_texts.append(f"=== PÁGINA {page_num + 1} ===\n{combined}")

    doc.close()
    return "\n".join(all_texts)


def _score_ocr_text(text: str) -> int:
    """Puntúa calidad del texto OCR (helper para diagnóstico)."""
    score = 0
    t = text.upper()
    keywords = ["MESA", "ELECCIONES", "PRESIDENTE", "ACTA", "ELECTORAL",
                "ELECTORES", "HABILES", "VOTOS", "NULOS", "BLANCO",
                "PARTIDO", "PERU", "TOTAL", "ESCRUTINIO", "FIRMA", "DNI"]
    for kw in keywords:
        if kw in t:
            score += 5
    nums = re.findall(r'\d+', text)
    score += min(len(nums) * 2, 40)
    score += min(len(text) // 100, 20)
    return max(score, 0)


# ──────────────────────────────────────────────────────────────────────────────
# Extracción de votos por partido — combina nombres + dígitos por zona
# ──────────────────────────────────────────────────────────────────────────────

def _extract_digit_lines(digit_text: str) -> List[Optional[int]]:
    """
    Extrae una lista de números de la columna de votos.
    Preserva posiciones: líneas vacías → None (mantener mapeo a filas).
    """
    result = []
    for line in digit_text.split('\n'):
        line = line.strip()
        if not line:
            result.append(None)  # Preservar posición para mapeo fila→partido
            continue
        # Limpiar caracteres OCR basura, dejar solo dígitos
        digits_only = re.sub(r'[^\d]', '', line)
        if digits_only:
            val = int(digits_only)
            if val <= 999:
                result.append(val)
            else:
                result.append(None)
        else:
            result.append(None)
    return result


def _parse_party_names_from_text(text: str) -> List[Tuple[int, str]]:
    """
    Extrae los nombres de partido del texto OCR de la columna de partidos.
    Intenta detectar el número de fila (1-38) y el nombre.
    """
    partidos = []
    for line in text.split('\n'):
        line = line.strip()
        if not line or len(line) < 3:
            continue
        m = re.match(r'^(\d{1,2})\s+(.{3,})', line)
        if m:
            num = int(m.group(1))
            nombre = m.group(2).strip()
            if 1 <= num <= 38:
                partidos.append((num, nombre))
    return partidos


def extract_votes_from_text(text: str) -> Dict[str, int]:
    """
    Extrae votos por partido combinando múltiples zonas OCR.
    Estrategia:
      1. Buscar sección "COLUMNA VOTOS (DIGITOS)" → dígitos puros
      2. Buscar sección "COLUMNA PARTIDOS" → nombres de partidos
      3. Correlacionar por posición (fila 1-38)
      4. Fallback: buscar en tabla completa y página completa
    """
    votos = {}
    logger.info(f"Extrayendo votos — texto total: {len(text)} chars")

    # ── Extraer secciones del texto por zonas ──
    sections = {}
    current_section = ""
    current_lines = []
    for line in text.split('\n'):
        if line.startswith("--- ") and line.endswith(" ---"):
            if current_section:
                sections[current_section] = '\n'.join(current_lines)
            current_section = line.strip("- ")
            current_lines = []
        else:
            current_lines.append(line)
    if current_section:
        sections[current_section] = '\n'.join(current_lines)

    logger.info(f"  Secciones encontradas: {list(sections.keys())}")

    # ── Estrategia 1: Dígitos de la columna de votos ──
    digit_values = []
    if "COLUMNA VOTOS (DIGITOS)" in sections:
        digit_text = sections["COLUMNA VOTOS (DIGITOS)"]
        digit_values = _extract_digit_lines(digit_text)
        logger.info(f"  Dígitos detectados ({len(digit_values)}): {digit_values[:20]}")

    # ── Estrategia 2: Nombres de partidos ──
    ocr_partidos = []
    if "COLUMNA PARTIDOS" in sections:
        ocr_partidos = _parse_party_names_from_text(sections["COLUMNA PARTIDOS"])
        logger.info(f"  Partidos OCR ({len(ocr_partidos)}): "
                   f"{[(n, p[:25]) for n, p in ocr_partidos[:10]]}")

    # ── Correlacionar: si tenemos dígitos, mapear a partidos por posición ──
    if digit_values:
        for idx, val in enumerate(digit_values):
            if val is None or val == 0:
                continue
            fila = idx + 1

            nombre = None
            for pnum, pnombre in ocr_partidos:
                if pnum == fila:
                    nombre = pnombre
                    break
            if not nombre:
                for pnum, pnombre in PARTIDOS_ACTA:
                    if pnum == fila and pnombre:
                        nombre = pnombre
                        break
            if not nombre:
                nombre = f"PARTIDO FILA {fila}"

            votos[nombre.upper()] = val
            logger.info(f"  ✓ Fila {fila}: {nombre} = {val}")

    # ── Estrategia 3: Tabla combinada (nombres + números en misma línea) ──
    if "TABLA DE PARTIDOS" in sections:
        tabla_text = sections["TABLA DE PARTIDOS"]
        for line in tabla_text.split('\n'):
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^\s*(\d{1,2})\s+(.{3,50}?)\s+(\d{1,3})\s*$', line)
            if m:
                fila = int(m.group(1))
                nombre_ocr = m.group(2).strip()
                vts = int(m.group(3))
                if 1 <= fila <= 38 and vts <= 500:
                    partido = _match_partido(nombre_ocr, fila)
                    if partido not in votos:
                        votos[partido] = vts
                        logger.info(f"  ✓ Tabla fila {fila}: {partido} = {vts}")

    # ── Estrategia 4: Página completa como fallback ──
    if "PAGINA COMPLETA" in sections and len(votos) < 5:
        full_text = sections["PAGINA COMPLETA"]
        _extract_from_fulltext(full_text, votos)

    # ── Estrategia 5: Si no hay secciones (texto plano), buscar directo ──
    if not sections:
        _extract_from_fulltext(text, votos)

    # ── Estrategia 6: Buscar filas especiales en todo el texto ──
    for line in text.split('\n'):
        lu = line.upper().strip()
        m_num = re.search(r'(\d{1,4})\s*$', line.strip())
        if not m_num:
            continue
        val = int(m_num.group(1))
        if val > 500:
            continue

        if 'BLANCO' in lu and 'VOTOS EN BLANCO' not in votos:
            votos['VOTOS EN BLANCO'] = val
            logger.info(f"  VOTOS EN BLANCO = {val}")
        elif 'NULO' in lu and 'VOTOS NULOS' not in votos:
            votos['VOTOS NULOS'] = val
            logger.info(f"  VOTOS NULOS = {val}")
        elif 'IMPUGNAD' in lu and 'VOTOS IMPUGNADOS' not in votos:
            votos['VOTOS IMPUGNADOS'] = val
            logger.info(f"  VOTOS IMPUGNADOS = {val}")
        elif ('EMITID' in lu or 'TOTAL DE VOTOS' in lu) and 'TOTAL EMITIDOS' not in votos:
            votos['TOTAL EMITIDOS'] = val
            logger.info(f"  TOTAL EMITIDOS = {val}")

    logger.info(f"Total extraído: {len(votos)} entradas")
    return votos


def _extract_from_fulltext(text: str, votos: dict):
    """Extrae votos del texto completo usando regex genéricos."""
    for line in text.split('\n'):
        line_clean = line.strip()
        if not line_clean:
            continue

        m = re.match(r'^\s*(\d{1,2})\s+(.{3,55}?)\s+(\d{1,3})\s*$', line_clean)
        if m:
            num_fila = int(m.group(1))
            nombre = m.group(2).strip()
            vts = int(m.group(3))
            if 1 <= num_fila <= 38 and vts <= 500:
                partido = _match_partido(nombre, num_fila)
                if partido not in votos:
                    votos[partido] = vts
                    logger.info(f"  ✓ Full fila {num_fila}: {partido} = {vts}")

        for pnum, pnombre in PARTIDOS_ACTA:
            if not pnombre or pnombre in votos:
                continue
            if _nombre_fuzzy_match(pnombre, line_clean.upper()):
                m_num = re.search(r'(\d{1,3})\s*$', line_clean)
                if m_num:
                    val = int(m_num.group(1))
                    if val <= 500:
                        votos[pnombre] = val
                        logger.info(f"  ✓ Full match '{pnombre}' = {val}")
                break


def _match_partido(nombre_ocr: str, num_fila: int) -> str:
    """Mapea un nombre OCR al partido oficial por número de fila o similitud."""
    # Primero intentar por número de fila
    for pnum, pnombre in PARTIDOS_ACTA:
        if pnum == num_fila and pnombre:
            return pnombre

    # Si no hay match por fila, buscar por similitud de nombre
    nombre_upper = nombre_ocr.upper()
    best_match = nombre_ocr
    best_score = 0
    for _, pnombre in PARTIDOS_ACTA:
        if not pnombre:
            continue
        score = _similitud_jaccard(nombre_upper, pnombre)
        if score > best_score and score > 0.3:
            best_score = score
            best_match = pnombre
    return best_match


def _nombre_fuzzy_match(partido: str, line: str) -> bool:
    """Verifica si una línea contiene un nombre de partido (fuzzy)."""
    # Tomar las 2-3 palabras más significativas del partido
    palabras = [w for w in partido.split() if len(w) >= 4]
    if not palabras:
        return False
    matches = sum(1 for p in palabras if p in line)
    return matches >= max(1, len(palabras) // 2)


def _similitud_jaccard(a: str, b: str) -> float:
    """Similitud Jaccard entre dos cadenas basada en palabras significativas."""
    words_a = {w for w in a.split() if len(w) >= 3}
    words_b = {w for w in b.split() if len(w) >= 3}
    if not words_a or not words_b:
        return 0.0
    inter = words_a & words_b
    union = words_a | words_b
    return len(inter) / len(union) if union else 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Parseo estructurado del acta
# ──────────────────────────────────────────────────────────────────────────────

def parse_acta_text(text: str) -> Dict:
    """
    Parsea texto completo del acta y extrae campos estructurados.
    Usa regex flexibles que toleran errores OCR.
    """
    result = {
        "mesa": None,
        "electores_habiles": None,
        "total_votantes": None,
        "votos_validos": None,
        "votos_nulos": None,
        "votos_blanco": None,
        "votos_impugnados": None,
        "votos_emitidos": None,
        "firmas_detectadas": [],
        "dnis_detectados": [],
        "horas_detectadas": [],
        "votos_por_partido": {},
    }

    logger.info(f"Parseando texto del acta ({len(text)} chars)")

    # Mesa
    for pattern in [RE_MESA, RE_MESA_ALT]:
        m = pattern.search(text)
        if m:
            result["mesa"] = m.group(1).zfill(6)
            logger.info(f"  Mesa: {result['mesa']}")
            break

    # Electores hábiles
    for pattern in [RE_ELECTORES, RE_ELECTORES_ALT]:
        m = pattern.search(text)
        if m:
            result["electores_habiles"] = int(m.group(1))
            logger.info(f"  Electores hábiles: {result['electores_habiles']}")
            break

    # Votantes
    for pattern in [RE_VOTANTES, RE_VOTANTES_ALT]:
        m = pattern.search(text)
        if m:
            result["total_votantes"] = int(m.group(1))
            logger.info(f"  Total votantes: {result['total_votantes']}")
            break

    # Votos emitidos
    m = RE_EMITIDOS.search(text)
    if m:
        result["votos_emitidos"] = int(m.group(1))
        logger.info(f"  Votos emitidos: {result['votos_emitidos']}")

    # Otros campos numéricos
    for field, pattern, label in [
        ("votos_validos", RE_VALIDOS, "Votos válidos"),
        ("votos_nulos", RE_NULOS, "Votos nulos"),
        ("votos_blanco", RE_BLANCO, "Votos blanco"),
        ("votos_impugnados", RE_IMPUGNADOS, "Votos impugnados"),
    ]:
        m = pattern.search(text)
        if m:
            result[field] = int(m.group(1))
            logger.info(f"  {label}: {result[field]}")

    # Firmas, DNIs, horas
    result["firmas_detectadas"] = list(set(RE_FIRMA.findall(text)))
    result["dnis_detectados"] = RE_DNI.findall(text)
    result["horas_detectadas"] = [f"{m[0]}:{m[1]}" for m in RE_HORA.findall(text)]

    logger.info(f"  Firmas: {len(result['firmas_detectadas'])}, "
               f"DNIs: {len(result['dnis_detectados'])}, "
               f"Horas: {result['horas_detectadas']}")

    # Votos por partido
    result["votos_por_partido"] = extract_votes_from_text(text)

    return result


def process_pdf(pdf_path: str, dpi: int = 300) -> Dict:
    """
    Punto de entrada principal.
    Procesa un PDF de acta y retorna todos los datos extraídos.
    """
    if not os.path.exists(pdf_path):
        return {"error": f"Archivo no encontrado: {pdf_path}"}

    logger.info(f"═══ Procesando PDF: {pdf_path} ═══")
    file_size = os.path.getsize(pdf_path) / 1024
    logger.info(f"  Tamaño: {file_size:.0f} KB")

    resultado = {
        "pdf_path": pdf_path,
        "es_escaneado": None,
        "tiene_texto_seleccionable": False,
        "texto_crudo": "",
        "datos_extraidos": {},
        "num_paginas": 0,
        "dpi_usado": dpi,
    }

    # 1. Intentar extracción de texto directo (PDFs electrónicos)
    if PYMUPDF_OK:
        texto_directo, es_escaneado = _extract_text_pymupdf(pdf_path)
        resultado["es_escaneado"] = es_escaneado
        resultado["tiene_texto_seleccionable"] = not es_escaneado

        doc = fitz.open(pdf_path)
        resultado["num_paginas"] = len(doc)
        doc.close()

        if not es_escaneado:
            # PDF con texto seleccionable (electrónico) → parseo directo
            logger.info(f"PDF electrónico con {len(texto_directo)} chars de texto")
            resultado["texto_crudo"] = texto_directo
            resultado["metodo"] = "pymupdf_texto"
            resultado["datos_extraidos"] = _parse_electronic_pdf(texto_directo)

            datos = resultado["datos_extraidos"]
            n_partidos = len(datos.get("votos_por_partido", {}))
            logger.info(f"═══ Resumen: mesa={datos.get('mesa')}, "
                       f"electores={datos.get('electores_habiles')}, "
                       f"partidos={n_partidos}, método=texto_directo ═══")
            return resultado
        else:
            # PDF escaneado → necesita OCR con preprocesamiento
            logger.info(f"PDF escaneado detectado → OCR con preprocesamiento a {dpi} DPI")
            if TESSERACT_OK:
                texto_ocr = _extract_text_ocr(pdf_path, dpi=dpi)
                resultado["texto_crudo"] = texto_ocr
                resultado["metodo"] = "tesseract_ocr"
                logger.info(f"OCR completado: {len(texto_ocr)} chars extraídos")
            else:
                resultado["texto_crudo"] = texto_directo
                resultado["metodo"] = "pymupdf_texto_escaso"
                resultado["advertencia"] = "Tesseract no disponible."
    else:
        resultado["error"] = "PyMuPDF no instalado"
        return resultado

    # 2. Parsear el texto extraído
    resultado["datos_extraidos"] = parse_acta_text(resultado["texto_crudo"])

    # 3. Log resumen
    datos = resultado["datos_extraidos"]
    n_partidos = len(datos.get("votos_por_partido", {}))
    logger.info(f"═══ Resumen OCR: mesa={datos.get('mesa')}, "
               f"electores={datos.get('electores_habiles')}, "
               f"partidos_extraídos={n_partidos}, "
               f"método={resultado['metodo']} ═══")

    return resultado


def get_page_images(pdf_path: str, dpi: int = 200) -> List[bytes]:
    """
    Retorna las páginas del PDF como imágenes PNG (bytes).
    Usado para la visualización en la web UI.
    """
    if not PYMUPDF_OK or not os.path.exists(pdf_path):
        return []
    doc = fitz.open(pdf_path)
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    images = []
    for page in doc:
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        images.append(pix.tobytes("png"))
    doc.close()
    return images
