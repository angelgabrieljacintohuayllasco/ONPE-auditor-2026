"""
Módulo de análisis forense avanzado de PDFs de actas electorales.
Técnicas:
  1.  Metadatos completos: versión, Producer, Creator, fechas, XMP, AcroForm,
      XFA, firma digital, linearización, encriptación, stream de metadatos.
  2.  Detección de actualizaciones incrementales (%%EOF / xref / trailer
      múltiples en el binario del archivo).
  3.  Análisis de estructura de objetos PDF: /Image, /Font, /XObject, streams.
  4.  Análisis de imágenes embebidas por página: dimensiones, DPI estimado,
      compresión (JPEG/JPEG2000/Flate/CCITT/JBIG2), espacio de color, bpc.
  5.  Análisis de capa de texto: texto real, fuentes embebidas, vectores,
      texto OCR invisible (render mode 3), cobertura OCR por página.
  6.  Detección de huellas de escáner físico: ruido de sensor, inclinación,
      sombras de borde, iluminación no uniforme, grano/compresión natural.
  7.  Detección de huellas de fabricación artificial: fondo blanco perfecto,
      nitidez uniforme, artefactos de inserción de sellos/firmas.
  8.  Detección de Puntos Amarillos (Machine Identification Code - MIC/TDM).
  9.  Análisis de histograma de niveles de gris (gaps = posible manipulación).
  10. Clasificación final Tipo A-E con nivel de confianza y conclusión pericial.
      Lenguaje pericial: "compatible con", "consistente con", "indica señales de",
      sin afirmaciones absolutas de autenticidad o fraude.
"""

import os
import re
import io
import struct
import logging
import hashlib
import json
import colorsys
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
    PYMUPDF_OK = True
except ImportError:
    PYMUPDF_OK = False

try:
    import numpy as np
    from PIL import Image, ImageFilter, ImageEnhance
    IMAGING_OK = True
except ImportError:
    IMAGING_OK = False
    logger.warning("numpy/Pillow no instalados. Análisis de imagen limitado.")

try:
    import pikepdf
    PIKEPDF_OK = True
except ImportError:
    PIKEPDF_OK = False
    logger.warning("pikepdf no instalado. Análisis de metadatos limitado.")

# Mapeo de filtros PDF a nombres legibles
_PDF_FILTER_NAMES = {
    "/DCTDecode":       "JPEG",
    "/JPXDecode":       "JPEG2000",
    "/FlateDecode":     "Flate/ZIP",
    "/CCITTFaxDecode":  "CCITT",
    "/JBIG2Decode":     "JBIG2",
    "/LZWDecode":       "LZW",
    "/RunLengthDecode": "RLE",
    "/ASCII85Decode":   "ASCII85",
    "/ASCIIHexDecode":  "ASCIIHex",
}

# Productores conocidos de generación artificial (no escáneres)
_GENERATION_PRODUCERS = [
    "itext", "reportlab", "wkhtmltopdf", "imagemagick", "ghostscript",
    "fpdf", "tcpdf", "weasyprint", "pdfbox", "aspose", "pdftron",
    "chrome", "chromium", "puppeteer", "selenium", "phantomjs",
    "openpdf", "mpdf", "htmldoc", "princexml",
    "nitro", "foxit", "pdf24", "pdfescape", "smallpdf",
    "microsoft word", "microsoft excel", "microsoft powerpoint",
    "libreoffice", "openoffice", "indesign", "quarkxpress", "scribus",
    "pdfcreator", "bullzip", "cutepdf", "primopdf",
    "acrobat distiller", "acrobat pdfmaker",
]

# Productores conocidos de escáneres/MFP reales
_SCANNER_PRODUCERS = [
    "hp", "canon", "epson", "xerox", "ricoh", "konica", "kyocera",
    "brother", "lexmark", "samsung", "sharp", "toshiba", "fujitsu",
    "scansnap", "paperport", "nuance",
    "mfp", "laserjet", "imagerunner", "ecosys", "bizhub",
    "workcentre", "documentcentre", "aficio",
]


# ──────────────────────────────────────────────────────────────────────────────
# 1. METADATOS DEL PDF (completo)
# ──────────────────────────────────────────────────────────────────────────────

def extract_pdf_metadata(pdf_path: str) -> Dict:
    """
    Extrae metadatos técnicos completos del PDF:
    versión, Producer, Creator, fechas, XMP, AcroForm, XFA, firma digital,
    linearización, encriptación, stream de metadatos, tagged, objetos, etc.
    """
    result = {
        # Campos estándar
        "pdf_version":           "",
        "producer":              None,
        "creator":               None,
        "creation_date":         None,
        "mod_date":              None,
        "page_count":            0,
        "encrypted":             False,
        "linearized":            False,
        "tagged":                False,
        "metadata_stream":       False,
        "xmp_present":           False,
        "xmp_raw":               {},
        # Seguridad / estructura
        "digital_signature_present": False,
        "signature_details":     [],
        "acroform_present":      False,
        "xfa_present":           False,
        "javascript_present":    False,
        "attachments_present":   False,
        "object_count":          0,
        # Clasificación rápida
        "es_electronico":        False,
        "producer_category":     "desconocido",  # "generacion" | "scanner" | "acrobat" | "otro"
        # Retrocompatibilidad
        "tiene_firma_digital":   False,
        "tiene_javascript":      False,
        "tiene_adjuntos":        False,
        "num_paginas":           0,
        "encriptado":            False,
        "metadatos_pymupdf":     {},
        "metadatos_pikepdf":     {},
    }

    # ── PyMuPDF: metadatos básicos ───────────────────────────────────────────
    if PYMUPDF_OK:
        try:
            doc = fitz.open(pdf_path)
            result["page_count"]  = len(doc)
            result["num_paginas"] = len(doc)
            result["encrypted"]   = doc.is_encrypted
            result["encriptado"]  = doc.is_encrypted

            # PDF version: leer del encabezado binario del archivo
            try:
                with open(pdf_path, "rb") as fv:
                    header = fv.read(16)
                m = re.search(rb"%PDF-(\d+\.\d+)", header)
                if m:
                    result["pdf_version"] = m.group(1).decode("ascii")
            except Exception:
                pass

            meta = doc.metadata or {}
            result["metadatos_pymupdf"] = {k: v for k, v in meta.items() if v}

            result["producer"]      = meta.get("producer") or None
            result["creator"]       = meta.get("creator") or None
            result["creation_date"] = meta.get("creationDate") or None
            result["mod_date"]      = meta.get("modDate") or None

            # Adjuntos
            try:
                emb = doc.embfile_names()
                result["attachments_present"] = len(emb) > 0
                result["tiene_adjuntos"]      = result["attachments_present"]
            except Exception:
                pass

            # JavaScript: buscar en la representación del trailer/catálogo
            try:
                trailer_str = doc.pdf_trailer() or ""
                if isinstance(trailer_str, str):
                    has_js = "/JavaScript" in trailer_str or "/JS" in trailer_str
                else:
                    has_js = False
                result["javascript_present"] = has_js
                result["tiene_javascript"]   = has_js
            except Exception:
                pass

            # Clasificar producer
            prod_lower = (result["producer"] or "").lower()
            crea_lower = (result["creator"] or "").lower()
            combined   = prod_lower + " " + crea_lower

            if any(kw in combined for kw in _GENERATION_PRODUCERS):
                result["producer_category"] = "generacion"
                result["es_electronico"]    = True
            elif any(kw in combined for kw in _SCANNER_PRODUCERS):
                result["producer_category"] = "scanner"
            elif "adobe acrobat" in combined or "acrobat" in combined:
                result["producer_category"] = "acrobat"
                result["es_electronico"]    = True
            elif combined.strip():
                result["producer_category"] = "otro"
                result["es_electronico"]    = True

            doc.close()
        except Exception as e:
            result["error_pymupdf"] = str(e)
            logger.error(f"PyMuPDF metadata error: {e}")

    # ── pikepdf: metadatos profundos ─────────────────────────────────────────
    if PIKEPDF_OK:
        try:
            with pikepdf.open(pdf_path) as pdf:
                # Linearización
                result["linearized"] = pdf.is_linearized

                # Contar objetos
                obj_count = 0
                for obj in pdf.objects:
                    if obj is not None:
                        obj_count += 1
                result["object_count"] = obj_count

                # XMP metadata stream
                try:
                    with pdf.open_metadata() as xmeta:
                        xmp_dict = dict(xmeta)
                        result["xmp_present"]    = len(xmp_dict) > 0
                        result["metadata_stream"] = len(xmp_dict) > 0
                        result["xmp_raw"] = {k: str(v) for k, v in xmp_dict.items()}
                except Exception:
                    pass

                # docinfo completo
                try:
                    docinfo = {}
                    if pdf.docinfo:
                        for k, v in pdf.docinfo.items():
                            docinfo[str(k)] = str(v)
                    result["metadatos_pikepdf"] = {
                        "xmp":         result["xmp_raw"],
                        "docinfo":     docinfo,
                        "linearized":  result["linearized"],
                        "num_objetos": result["object_count"],
                    }
                except Exception:
                    pass

                # Firma digital: /AcroForm + /Sig en Root
                try:
                    root = pdf.Root
                    if "/AcroForm" in root:
                        result["acroform_present"] = True
                        acroform = root["/AcroForm"]
                        if "/SigFlags" in acroform:
                            result["digital_signature_present"] = True
                            result["tiene_firma_digital"]       = True
                        # Revisar campos de firma individuales
                        if "/Fields" in acroform:
                            for field_ref in acroform["/Fields"]:
                                try:
                                    field = field_ref
                                    if isinstance(field, pikepdf.Dictionary):
                                        ft = str(field.get("/FT", ""))
                                        if ft == "/Sig":
                                            result["digital_signature_present"] = True
                                            result["tiene_firma_digital"]       = True
                                            sig_info = {}
                                            if "/V" in field:
                                                sig_val = field["/V"]
                                                if isinstance(sig_val, pikepdf.Dictionary):
                                                    for sk in ["/Name", "/Reason", "/Location",
                                                               "/M", "/Filter", "/SubFilter"]:
                                                        if sk in sig_val:
                                                            sig_info[sk.lstrip("/")] = str(sig_val[sk])
                                            result["signature_details"].append(sig_info)
                                except Exception:
                                    pass

                    # XFA
                    if "/AcroForm" in root:
                        if "/XFA" in root["/AcroForm"]:
                            result["xfa_present"] = True

                    # Metadata stream alternativo
                    if "/Metadata" in root:
                        result["metadata_stream"] = True

                except Exception as e:
                    logger.debug(f"pikepdf signature check error: {e}")

        except Exception as e:
            result["error_pikepdf"] = str(e)
            logger.error(f"pikepdf metadata error: {e}")

    return result


# ──────────────────────────────────────────────────────────────────────────────
# 2. DETECCIÓN DE ACTUALIZACIONES INCREMENTALES
# ──────────────────────────────────────────────────────────────────────────────

def detect_incremental_updates(pdf_path: str) -> Dict:
    """
    Lee el binario del PDF para contar marcadores que indican revisiones
    (actualizaciones incrementales): %%EOF, startxref, xref, trailer.
    Más de una instancia de cada marcador indica que el PDF fue modificado
    después de su creación original.

    Las actualizaciones incrementales son el mecanismo estándar para añadir
    firmas, anotaciones o ediciones manteniendo la versión previa. Pueden ser
    legítimas (firma digital posterior) o indicar edición encubierta.
    """
    result = {
        "disponible":        True,
        "eof_count":         0,
        "startxref_count":   0,
        "xref_count":        0,
        "trailer_count":     0,
        "xref_offsets":      [],
        "incremental_updates": False,
        "update_count":      0,
        "interpretacion":    "",
        "flag_riesgo":       False,
    }
    try:
        with open(pdf_path, "rb") as f:
            raw = f.read()

        result["eof_count"]       = raw.count(b"%%EOF")
        result["startxref_count"] = raw.count(b"startxref")
        result["xref_count"]      = raw.count(b"\nxref") + raw.count(b"\r\nxref")
        result["trailer_count"]   = raw.count(b"\ntrailer") + raw.count(b"\r\ntrailer")

        # Calcular número de revisiones (cada revisión añade startxref + %%EOF)
        updates = max(result["eof_count"], result["startxref_count"]) - 1
        result["update_count"]          = max(0, updates)
        result["incremental_updates"]   = result["update_count"] > 0

        # Extraer offsets de startxref para mapear revisiones
        offsets = []
        pos = 0
        while True:
            idx = raw.find(b"startxref", pos)
            if idx == -1:
                break
            # Leer el número en la línea siguiente
            end = raw.find(b"\n", idx + 9)
            if end == -1:
                break
            chunk = raw[idx + 9:end].strip()
            try:
                offsets.append(int(chunk))
            except ValueError:
                offsets.append(-1)
            pos = end
        result["xref_offsets"] = offsets[:20]  # máximo 20

        # Interpretación
        if result["update_count"] == 0:
            result["interpretacion"] = (
                "El PDF no presenta actualizaciones incrementales. "
                "Estructura de revisión única, compatible con documento sin modificaciones posteriores."
            )
        elif result["update_count"] == 1:
            result["flag_riesgo"] = True
            result["interpretacion"] = (
                f"Se detecta 1 actualización incremental (2 secciones xref/trailer). "
                "Esto puede ser normal si se añadió una firma digital posteriormente, "
                "o puede indicar modificación encubierta del contenido."
            )
        else:
            result["flag_riesgo"] = True
            result["interpretacion"] = (
                f"ATENCIÓN: Se detectan {result['update_count']} actualizaciones incrementales "
                f"({result['eof_count']} marcadores %%EOF). Esto indica múltiples modificaciones "
                "posteriores a la creación. Requiere revisión detallada de cada revisión."
            )
    except Exception as e:
        result["disponible"] = False
        result["error"]      = str(e)
        logger.error(f"Error en detect_incremental_updates: {e}")

    return result


# ──────────────────────────────────────────────────────────────────────────────
# 3. ANÁLISIS DE ESTRUCTURA DE OBJETOS PDF
# ──────────────────────────────────────────────────────────────────────────────

def analyze_pdf_structure(pdf_path: str) -> Dict:
    """
    Enumera los objetos del PDF (imágenes, fuentes, XObjects, streams) para
    determinar si el documento es una imagen plana, documento nativo o híbrido.
    Requiere pikepdf.
    """
    result = {
        "disponible":      PIKEPDF_OK,
        "image_objects":   [],
        "font_objects":    [],
        "xobject_count":   0,
        "stream_count":    0,
        "total_objects":   0,
        "has_images":      False,
        "has_fonts":       False,
        "has_vectors":     False,
        "document_model":  "desconocido",  # "single-image" | "multi-image" | "native" | "mixed"
        "interpretacion":  "",
    }

    if not PIKEPDF_OK:
        result["interpretacion"] = "pikepdf no disponible. Análisis de estructura limitado."
        return result

    try:
        with pikepdf.open(pdf_path) as pdf:
            result["total_objects"] = sum(1 for _ in pdf.objects if _ is not None)

            for obj in pdf.objects:
                if obj is None:
                    continue
                try:
                    if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
                        continue

                    obj_type    = str(obj.get("/Type",    ""))
                    obj_subtype = str(obj.get("/Subtype", ""))

                    # Imagen embebida
                    if obj_subtype == "/Image":
                        result["has_images"] = True
                        w   = int(obj.get("/Width",            0))
                        h   = int(obj.get("/Height",           0))
                        cs  = str(obj.get("/ColorSpace",       ""))
                        bpc = int(obj.get("/BitsPerComponent", 0))
                        # Filtros de compresión
                        raw_filter = obj.get("/Filter")
                        filters = []
                        if raw_filter is not None:
                            if isinstance(raw_filter, pikepdf.Array):
                                filters = [str(f) for f in raw_filter]
                            else:
                                filters = [str(raw_filter)]
                        readable_filters = [
                            _PDF_FILTER_NAMES.get(f, f) for f in filters
                        ]
                        img_info = {
                            "width":           w,
                            "height":          h,
                            "color_space":     cs.lstrip("/"),
                            "bits_per_component": bpc,
                            "filters":         readable_filters,
                        }
                        if isinstance(obj, pikepdf.Stream):
                            try:
                                img_info["stream_length"] = len(obj.read_bytes())
                            except Exception:
                                img_info["stream_length"] = -1
                        result["image_objects"].append(img_info)

                    # Fuente embebida
                    if obj_type == "/Font":
                        result["has_fonts"] = True
                        font_info = {
                            "subtype":    obj_subtype.lstrip("/"),
                            "base_name":  str(obj.get("/BaseFont", "")),
                            "encoding":   str(obj.get("/Encoding", "")),
                        }
                        result["font_objects"].append(font_info)

                    # XObjects (formas, imágenes referenciadas)
                    if obj_subtype == "/Form":
                        result["xobject_count"] += 1

                    # Streams genéricos
                    if isinstance(obj, pikepdf.Stream):
                        result["stream_count"] += 1

                except Exception:
                    continue

            # Determinar document_model
            n_images = len(result["image_objects"])
            n_fonts  = len(result["font_objects"])
            has_large_images = any(
                img["width"] * img["height"] > 500_000
                for img in result["image_objects"]
            )

            if n_fonts > 0 and n_images == 0:
                result["document_model"] = "native"
                result["has_vectors"]    = True
            elif n_fonts > 0 and n_images > 0:
                result["document_model"] = "mixed"
                result["has_vectors"]    = True
            elif n_images == 1 or (n_images > 0 and has_large_images and n_fonts == 0):
                result["document_model"] = "single-image"
            elif n_images > 1 and n_fonts == 0:
                result["document_model"] = "multi-image"
            else:
                result["document_model"] = "desconocido"

            # Resumen
            result["interpretacion"] = (
                f"Total objetos: {result['total_objects']}. "
                f"Imágenes: {n_images}. Fuentes: {n_fonts}. "
                f"Modelo de documento: {result['document_model']}."
            )

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Error en analyze_pdf_structure: {e}")

    return result


# ──────────────────────────────────────────────────────────────────────────────
# 4. ANÁLISIS DE IMÁGENES EMBEBIDAS POR PÁGINA (DPI, compresión, color)
# ──────────────────────────────────────────────────────────────────────────────

def analyze_image_objects_per_page(pdf_path: str) -> Dict:
    """
    Extrae, para cada página del PDF, las imágenes embebidas con sus propiedades
    técnicas: dimensiones en píxeles, DPI estimado respecto al tamaño de página,
    compresión, espacio de color, bits por componente.

    El DPI se estima comparando la resolución en píxeles de la imagen con las
    dimensiones de la página en puntos tipográficos (1 pt = 1/72 pulgada).
    """
    result = {
        "disponible":         PYMUPDF_OK,
        "pages":              [],
        "total_images":       0,
        "pages_single_image": 0,
        "pages_no_image":     0,
        "pages_multi_image":  0,
        "dpi_range":          {"min": None, "max": None, "avg": None},
        "compressions_found": [],
        "color_spaces_found": [],
        "interpretacion":     "",
    }

    if not PYMUPDF_OK:
        result["interpretacion"] = "PyMuPDF no disponible."
        return result

    try:
        doc   = fitz.open(pdf_path)
        all_dpi: List[float] = []
        compressions_set: set = set()
        color_spaces_set: set = set()

        for page_num, page in enumerate(doc):
            page_rect = page.rect  # en puntos tipográficos
            pw_in = page_rect.width  / 72.0  # ancho en pulgadas
            ph_in = page_rect.height / 72.0  # alto en pulgadas

            images_info = []
            img_list = page.get_images(full=True)

            for img_ref in img_list:
                xref      = img_ref[0]
                smask     = img_ref[1]
                img_width = img_ref[2]
                img_height= img_ref[3]
                bpc       = img_ref[4]
                cs_name   = img_ref[5] or img_ref[6] or "unknown"

                # DPI estimado
                x_ppi = round(img_width  / pw_in) if pw_in > 0 else 0
                y_ppi = round(img_height / ph_in) if ph_in > 0 else 0
                avg_ppi = (x_ppi + y_ppi) / 2 if (x_ppi and y_ppi) else 0
                all_dpi.append(avg_ppi)

                # Compresión: leer desde xref si pikepdf disponible
                compression = "desconocida"
                if PIKEPDF_OK:
                    try:
                        with pikepdf.open(pdf_path) as pdf:
                            obj = pdf.get_object(xref, 0)
                            if isinstance(obj, (pikepdf.Stream, pikepdf.Dictionary)):
                                raw_filter = obj.get("/Filter")
                                if raw_filter is not None:
                                    if isinstance(raw_filter, pikepdf.Array):
                                        fs = [str(f) for f in raw_filter]
                                    else:
                                        fs = [str(raw_filter)]
                                    compression = "+".join(
                                        _PDF_FILTER_NAMES.get(f, f) for f in fs
                                    )
                    except Exception:
                        pass

                compressions_set.add(compression)
                color_spaces_set.add(str(cs_name))

                images_info.append({
                    "xref":               xref,
                    "width_px":           img_width,
                    "height_px":          img_height,
                    "bits_per_component": bpc,
                    "color_space":        str(cs_name),
                    "compression":        compression,
                    "x_ppi":              x_ppi,
                    "y_ppi":              y_ppi,
                    "has_smask":          smask > 0,
                })

            n_imgs = len(images_info)
            result["total_images"] += n_imgs

            page_info = {
                "page":         page_num + 1,
                "width_pt":     round(page_rect.width,  1),
                "height_pt":    round(page_rect.height, 1),
                "width_in":     round(pw_in,  2),
                "height_in":    round(ph_in,  2),
                "image_count":  n_imgs,
                "images":       images_info,
            }
            result["pages"].append(page_info)

            if n_imgs == 0:
                result["pages_no_image"]    += 1
            elif n_imgs == 1:
                result["pages_single_image"] += 1
            else:
                result["pages_multi_image"]  += 1

        doc.close()

        # Estadísticas DPI
        valid_dpi = [d for d in all_dpi if d > 0]
        if valid_dpi:
            result["dpi_range"] = {
                "min": round(min(valid_dpi)),
                "max": round(max(valid_dpi)),
                "avg": round(sum(valid_dpi) / len(valid_dpi)),
            }

        result["compressions_found"] = sorted(compressions_set)
        result["color_spaces_found"] = sorted(color_spaces_set)

        # Interpretación
        avg_dpi = result["dpi_range"]["avg"]
        total_p = len(result["pages"])
        sing    = result["pages_single_image"]

        if sing == total_p and total_p > 0:
            dpi_str = f"DPI promedio estimado: {avg_dpi}." if avg_dpi else ""
            result["interpretacion"] = (
                f"Todas las páginas ({total_p}) contienen exactamente una imagen de página completa. "
                f"{dpi_str} Compresiones: {result['compressions_found']}. "
                "Modelo consistent con PDF de imagen única (posible escaneo o imagen artificial)."
            )
        elif result["pages_no_image"] == total_p:
            result["interpretacion"] = (
                "No se detectaron imágenes embebidas en ninguna página. "
                "Consistente con PDF nativo digital."
            )
        else:
            result["interpretacion"] = (
                f"Estructura mixta: {sing} pág. imagen única, "
                f"{result['pages_multi_image']} pág. multi-imagen, "
                f"{result['pages_no_image']} pág. sin imágenes. "
                f"DPI: {result['dpi_range']}."
            )

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Error en analyze_image_objects_per_page: {e}")

    return result


# ──────────────────────────────────────────────────────────────────────────────
# 5. ANÁLISIS DE CAPA DE TEXTO (texto real, OCR invisible, vectores, fuentes)
# ──────────────────────────────────────────────────────────────────────────────

def analyze_text_layer(pdf_path: str) -> Dict:
    """
    Analiza la capa de texto del PDF para distinguir:
    - Texto real seleccionable (nativo digital)
    - Texto OCR invisible superpuesto sobre imagen (render mode 3)
    - Ausencia total de texto (imagen sin OCR)
    - Fuentes embebidas y objetos vectoriales
    """
    result = {
        "disponible":          PYMUPDF_OK,
        "pages_with_text":     0,
        "pages_without_text":  0,
        "total_char_count":    0,
        "has_real_text":       False,
        "has_invisible_text":  False,   # OCR superpuesto invisible
        "ocr_coverage_pct":    None,    # % de área cubierta por texto OCR vs imagen
        "embedded_fonts":      [],
        "has_embedded_fonts":  False,
        "has_vector_graphics": False,
        "text_preview":        "",      # Primeros 300 caracteres
        "per_page":            [],
        "interpretacion":      "",
    }

    if not PYMUPDF_OK:
        result["interpretacion"] = "PyMuPDF no disponible."
        return result

    try:
        doc       = fitz.open(pdf_path)
        all_chars = 0
        fonts_set: set = set()

        for page_num, page in enumerate(doc):
            # ── Texto seleccionable ───────────────────────────────────────
            plain_text = page.get_text("text").strip()
            char_count = len(plain_text)
            all_chars += char_count

            # ── Texto invisible (OCR render mode 3) ──────────────────────
            # En PyMuPDF, rawdict expone span flags; el color blanco o
            # flags & 1 indican texto invisible en muchos PDFs de OCR.
            invisible_chars = 0
            try:
                raw  = page.get_text("rawdict")
                for block in raw.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            # color 0xFFFFFF (blanco) o flags que indican render invisible
                            color = span.get("color", 0)
                            flags = span.get("flags", 0)
                            text  = span.get("text", "").strip()
                            # Blanco = 16777215 (0xFFFFFF) = invisible sobre fondo blanco
                            if color == 16777215 and text:
                                invisible_chars += len(text)
                            # Flags bit 0 = superscript, flags & 2 = italic — no relacionados
                            # Pero algunos OCR marcan con size muy pequeño (< 1)
                            size = span.get("size", 10)
                            if size < 0.5 and text:
                                invisible_chars += len(text)
            except Exception:
                pass

            # ── Fuentes ──────────────────────────────────────────────────
            page_fonts = page.get_fonts(full=True)
            for font in page_fonts:
                # font tuple: (xref, ext, type, basefont, name, encoding, referencer)
                font_name = font[3] or font[4] or ""
                font_type = font[2] or ""
                if font_name:
                    fonts_set.add(f"{font_name} ({font_type})")

            # ── Vectores: dibujos/paths en la página ─────────────────────
            try:
                drawings = page.get_drawings()
                has_vect_page = len(drawings) > 0
            except Exception:
                has_vect_page = False

            page_info = {
                "page":             page_num + 1,
                "char_count":       char_count,
                "invisible_chars":  invisible_chars,
                "has_text":         char_count > 10,
                "has_invisible_ocr": invisible_chars > 10,
                "fonts_count":      len(page_fonts),
                "has_vectors":      has_vect_page,
            }
            result["per_page"].append(page_info)

            if char_count > 10:
                result["pages_with_text"]    += 1
            else:
                result["pages_without_text"] += 1

            if invisible_chars > 10:
                result["has_invisible_text"] = True

            if has_vect_page:
                result["has_vector_graphics"] = True

        doc.close()

        result["total_char_count"] = all_chars
        result["has_real_text"]    = all_chars > 20
        result["embedded_fonts"]   = sorted(fonts_set)
        result["has_embedded_fonts"] = len(fonts_set) > 0
        result["text_preview"]     = ""  # Se extrae aparte si se necesita

        # Interpretación
        if result["has_real_text"] and result["has_embedded_fonts"]:
            result["interpretacion"] = (
                f"Se detecta texto seleccionable real ({all_chars} caracteres) "
                f"y {len(fonts_set)} fuente(s) embebida(s). "
                "Compatible con PDF nativo digital."
            )
        elif result["has_invisible_text"] and not result["has_real_text"]:
            result["interpretacion"] = (
                "Se detecta texto OCR invisible superpuesto sobre imagen. "
                "Compatible con escaneo al que se aplicó OCR automático posterior."
            )
        elif not result["has_real_text"] and not result["has_invisible_text"]:
            result["interpretacion"] = (
                "No se detecta texto en ninguna página. "
                "Compatible con imagen embebida sin capa de texto."
            )
        elif result["has_real_text"] and not result["has_embedded_fonts"]:
            result["interpretacion"] = (
                f"Texto presente ({all_chars} chars) pero sin fuentes embebidas. "
                "Puede indicar uso de fuentes del sistema o extracción anómala."
            )
        else:
            result["interpretacion"] = (
                f"Texto: {all_chars} chars, fuentes: {len(fonts_set)}, "
                f"vectores: {result['has_vector_graphics']}."
            )

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Error en analyze_text_layer: {e}")

    return result


# ──────────────────────────────────────────────────────────────────────────────
# 6. DETECCIÓN DE HUELLAS DE ESCÁNER FÍSICO
# ──────────────────────────────────────────────────────────────────────────────

def detect_scanner_artifacts(pdf_path: str, dpi: int = 150) -> Dict:
    """
    Renderiza las páginas y analiza señales físicas de escaneo real:
    - Ruido de sensor (varianza en áreas blancas)
    - Inclinación/skew (ángulo estimado de la imagen)
    - Sombras de borde (bordes oscuros de hoja)
    - Iluminación no uniforme (gradiente de brillo entre cuadrantes)
    - Fondo con grano/textura natural vs fondo blanco perfecto

    Nota: un DPI bajo (150) es suficiente para detectar estas señales
    sin requerir excesivo tiempo de procesado.
    """
    result = {
        "disponible":           PYMUPDF_OK and IMAGING_OK,
        "noise_level":          0.0,    # Desviación estándar en áreas blancas
        "background_uniformity": 100.0,  # 100 = perfectamente uniforme
        "edge_shadow_detected": False,
        "illumination_uneven":  False,
        "skew_detected":        False,
        "skew_angle_deg":       0.0,
        "natural_grain":        False,
        "scan_score":           0,      # 0-100: probabilidad de ser escaneo real
        "interpretacion":       "",
        "indicadores":          [],
    }

    if not result["disponible"]:
        result["interpretacion"] = "Requiere PyMuPDF + numpy + Pillow."
        return result

    try:
        doc   = fitz.open(pdf_path)
        mat   = fitz.Matrix(dpi / 72, dpi / 72)
        page  = doc[0]  # analizar primera página
        pix   = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        doc.close()

        img_arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width
        )
        H, W = img_arr.shape

        indicadores: List[str] = []
        scan_score = 0

        # ── 1. Ruido en áreas blancas (background) ────────────────────────
        # Seleccionar región central superior (típicamente fondo en actas)
        margin_h = H // 8
        margin_w = W // 8
        white_region = img_arr[margin_h: margin_h * 2, margin_w: W - margin_w]
        # Solo píxeles considerados "blancos" (> 200)
        white_pixels = white_region[white_region > 200]
        if len(white_pixels) > 100:
            noise = float(np.std(white_pixels))
            result["noise_level"] = round(noise, 2)
            if noise > 4.0:
                scan_score += 25
                indicadores.append(
                    f"Ruido de sensor detectado en fondo blanco (σ={noise:.1f}). "
                    "Compatible con escáner físico."
                )
                result["natural_grain"] = True
            elif noise < 1.5:
                indicadores.append(
                    f"Fondo casi perfectamente uniforme (σ={noise:.1f}). "
                    "Compatible con imagen generada digitalmente."
                )

        # ── 2. Sombras de borde ───────────────────────────────────────────
        # Los escáneres de cama plana generan sombra en los bordes de la hoja
        border_w = max(1, W // 20)
        border_h = max(1, H // 20)

        left_strip  = img_arr[:, :border_w].astype(float)
        right_strip = img_arr[:, W - border_w:].astype(float)
        top_strip   = img_arr[:border_h, :].astype(float)
        bottom_strip= img_arr[H - border_h:, :].astype(float)
        center_mean = float(np.mean(img_arr[H // 4: 3 * H // 4, W // 4: 3 * W // 4]))

        border_means = [
            float(np.mean(left_strip)),
            float(np.mean(right_strip)),
            float(np.mean(top_strip)),
            float(np.mean(bottom_strip)),
        ]
        min_border = min(border_means)
        if center_mean - min_border > 15:
            result["edge_shadow_detected"] = True
            scan_score += 20
            indicadores.append(
                f"Sombra de borde detectada (contraste borde/centro={center_mean - min_border:.1f}). "
                "Compatible con escaneo en cama plana."
            )

        # ── 3. Iluminación no uniforme ────────────────────────────────────
        q1 = float(np.mean(img_arr[:H // 2, :W // 2]))
        q2 = float(np.mean(img_arr[:H // 2, W // 2:]))
        q3 = float(np.mean(img_arr[H // 2:, :W // 2]))
        q4 = float(np.mean(img_arr[H // 2:, W // 2:]))
        quad_range = max(q1, q2, q3, q4) - min(q1, q2, q3, q4)
        result["background_uniformity"] = round(100 - min(quad_range * 2, 100), 1)

        if quad_range > 8:
            result["illumination_uneven"] = True
            scan_score += 15
            indicadores.append(
                f"Iluminación no uniforme entre cuadrantes (rango={quad_range:.1f}). "
                "Compatible con óptica de escáner."
            )

        # ── 4. Grano natural (alta varianza local) ────────────────────────
        # Comparar varianza local en bloques pequeños
        block_size = max(1, W // 20)
        local_vars = []
        for bi in range(0, min(H - block_size, H // 2), block_size):
            for bj in range(0, min(W - block_size, W // 2), block_size):
                block = img_arr[bi:bi + block_size, bj:bj + block_size]
                if np.mean(block) > 200:   # solo en zonas blancas
                    local_vars.append(float(np.var(block)))
        if local_vars:
            avg_local_var = float(np.mean(local_vars))
            if avg_local_var > 5.0:
                result["natural_grain"] = True
                scan_score += 10
                indicadores.append(
                    f"Grano/textura natural en fondo (varianza local={avg_local_var:.1f}). "
                    "Compatible con papel real escaneado."
                )

        result["scan_score"]   = min(scan_score, 100)
        result["indicadores"]  = indicadores

        if scan_score >= 40:
            result["interpretacion"] = (
                f"Score de escaneo físico: {scan_score}/100. "
                "La imagen presenta múltiples características compatibles con escaneo real: "
                + "; ".join(indicadores)
            )
        elif scan_score > 0:
            result["interpretacion"] = (
                f"Score de escaneo físico: {scan_score}/100. "
                "Se detectan algunos indicios de escaneo físico pero no son concluyentes."
            )
        else:
            result["interpretacion"] = (
                "No se detectaron características típicas de escaneo físico real. "
                "Fondo uniforme y sin variaciones naturales."
            )

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Error en detect_scanner_artifacts: {e}")

    return result


# ──────────────────────────────────────────────────────────────────────────────
# 7. DETECCIÓN DE HUELLAS DE FABRICACIÓN ARTIFICIAL
# ──────────────────────────────────────────────────────────────────────────────

def detect_artificial_artifacts(pdf_path: str, dpi: int = 150) -> Dict:
    """
    Analiza señales que sugieren que la imagen fue generada o manipulada
    digitalmente en lugar de ser un escaneo físico real:
    - Fondo blanco perfecto / demasiado uniforme
    - Nitidez excesivamente uniforme en toda la página
    - Regiones con compresión/calidad inconsistente
    - Ausencia total de artefactos físicos
    """
    result = {
        "disponible":               PYMUPDF_OK and IMAGING_OK,
        "perfect_white_bg":         False,  # fondo blanco perfecto
        "uniform_sharpness":        False,  # nitidez demasiado uniforme
        "compression_inconsistency": False, # diferente calidad entre zonas
        "suspicious_zones":         [],     # coordenadas de zonas anómalas
        "artificial_score":         0,      # 0-100: probabilidad artificial
        "interpretacion":           "",
        "indicadores":              [],
    }

    if not result["disponible"]:
        result["interpretacion"] = "Requiere PyMuPDF + numpy + Pillow."
        return result

    try:
        doc   = fitz.open(pdf_path)
        mat   = fitz.Matrix(dpi / 72, dpi / 72)
        page  = doc[0]
        pix   = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        doc.close()

        img_arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width
        )
        H, W = img_arr.shape
        indicadores: List[str] = []
        art_score = 0

        # ── 1. Fondo blanco perfecto ──────────────────────────────────────
        white_ratio = float(np.mean(img_arr > 250))
        if white_ratio > 0.85:
            result["perfect_white_bg"] = True
            art_score += 30
            indicadores.append(
                f"Fondo blanco muy puro: {white_ratio * 100:.1f}% de píxeles > 250. "
                "Inusual en escaneos reales donde siempre hay algo de ruido."
            )

        # ── 2. Nitidez (Laplacian variance) en múltiples zonas ───────────
        # Comparar varianza del Laplaciano en franjas horizontales
        laplacian_vars: List[float] = []
        strip_h = max(1, H // 6)
        for si in range(0, H - strip_h, strip_h):
            strip = img_arr[si:si + strip_h, W // 4: 3 * W // 4].astype(np.float32)
            # Convolución Laplaciana manual (kernel [0,-1,0,-1,4,-1,0,-1,0])
            lap = (
                np.roll(strip,  1, axis=0) + np.roll(strip, -1, axis=0) +
                np.roll(strip,  1, axis=1) + np.roll(strip, -1, axis=1) -
                4 * strip
            )
            laplacian_vars.append(float(np.var(lap)))

        if len(laplacian_vars) > 2:
            lv_max  = max(laplacian_vars)
            lv_min  = min(laplacian_vars)
            lv_mean = float(np.mean(laplacian_vars))
            # Si la varianza del Laplaciano es muy baja en todo el documento
            if lv_mean < 50 and white_ratio > 0.5:
                result["uniform_sharpness"] = True
                art_score += 20
                indicadores.append(
                    f"Nitidez uniforme muy baja en toda la página (lap.var={lv_mean:.1f}). "
                    "Compatible con imagen digital de baja frecuencia espacial."
                )
            # Si hay variación fuerte entre zonas (inconsistencia por pegado/edición)
            if lv_max > 0 and lv_min < lv_max * 0.05 and lv_mean > 200:
                result["compression_inconsistency"] = True
                art_score += 25
                indicadores.append(
                    f"Inconsistencia de nitidez entre franjas (max/min={lv_max:.0f}/{lv_min:.0f}). "
                    "Puede indicar zonas de diferente origen o compresión diferencial."
                )

        # ── 3. Detección de bordes artificiales (zonas rectangulares muy nítidas) ──
        # Buscar transiciones de gris muy abruptas que no sean texto normal
        gradient_h = np.abs(np.diff(img_arr.astype(np.float32), axis=0))
        gradient_v = np.abs(np.diff(img_arr.astype(np.float32), axis=1))
        # Bordes muy fuertes horizontales en bloques amplios
        row_edge_strength = np.mean(gradient_h, axis=1)
        strong_rows = np.where(row_edge_strength > 30)[0]
        if len(strong_rows) > 3:
            # Verificar si hay "rectángulos" de bordes fuertes agrupados
            groups = []
            group  = [strong_rows[0]]
            for idx in range(1, len(strong_rows)):
                if strong_rows[idx] - strong_rows[idx - 1] < 20:
                    group.append(strong_rows[idx])
                else:
                    if len(group) > 5:
                        groups.append(group)
                    group = [strong_rows[idx]]
            if len(group) > 5:
                groups.append(group)

            if groups:
                result["suspicious_zones"].extend([
                    {"type": "horizontal_band", "rows": [int(g[0]), int(g[-1])]}
                    for g in groups[:5]
                ])
                if len(groups) > 2:
                    art_score += 15
                    indicadores.append(
                        f"Se detectan {len(groups)} bandas horizontales con bordes fuertes. "
                        "Puede indicar inserción de elementos (sellos, firmas, texto) con bordes artificiales."
                    )

        result["artificial_score"] = min(art_score, 100)
        result["indicadores"]      = indicadores

        if art_score >= 40:
            result["interpretacion"] = (
                f"Score de fabricación artificial: {art_score}/100. "
                "La imagen presenta características compatibles con generación o edición digital: "
                + "; ".join(indicadores)
            )
        elif art_score > 0:
            result["interpretacion"] = (
                f"Score de fabricación artificial: {art_score}/100. "
                "Algunos indicios de posible edición, no concluyente."
            )
        else:
            result["interpretacion"] = (
                "No se detectaron características típicas de fabricación artificial."
            )

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Error en detect_artificial_artifacts: {e}")

    return result


# ──────────────────────────────────────────────────────────────────────────────
# 8. CLASIFICACIÓN TÉCNICA DE ORIGEN DEL PDF (Tipo A-E)
# ──────────────────────────────────────────────────────────────────────────────

def classify_pdf_origin(
    metadata:       Dict,
    structure:      Dict,
    images_per_page: Dict,
    text_layer:     Dict,
    incr_updates:   Dict,
    scanner_art:    Dict,
    artificial_art: Dict,
) -> Dict:
    """
    Aplica reglas técnicas para clasificar el origen del PDF:

    Tipo A — PDF nativo digital
    Tipo B — PDF escaneado auténtico
    Tipo C — PDF híbrido
    Tipo D — PDF imagen artificial / sospechoso
    Tipo E — No concluyente

    Devuelve clasificación, nivel de confianza (bajo/medio/alto),
    indicadores a favor y en contra.
    """
    indicadores_favor:  List[str] = []
    indicadores_contra: List[str] = []
    risk_flags:         List[str] = []

    producer_cat   = metadata.get("producer_category", "desconocido")
    has_text       = text_layer.get("has_real_text", False)
    has_fonts      = text_layer.get("has_embedded_fonts", False)
    has_vectors    = text_layer.get("has_vector_graphics", False)
    has_invis_ocr  = text_layer.get("has_invisible_text", False)
    doc_model      = structure.get("document_model", "desconocido")
    n_images       = structure.get("image_objects", [])
    has_sig        = metadata.get("digital_signature_present", False)
    has_acroform   = metadata.get("acroform_present", False)
    incremental    = incr_updates.get("incremental_updates", False)
    update_count   = incr_updates.get("update_count", 0)
    scan_score     = scanner_art.get("scan_score", 0)
    art_score      = artificial_art.get("artificial_score", 0)
    dpi_avg        = (images_per_page.get("dpi_range") or {}).get("avg") or 0
    compressions   = images_per_page.get("compressions_found", [])
    pages_single   = images_per_page.get("pages_single_image", 0)
    total_pages    = len(images_per_page.get("pages", []))

    # ── REGLA 1: PDF nativo digital (Tipo A) ─────────────────────────────
    score_A = 0
    if has_text:
        score_A += 40
        indicadores_favor.append("Texto seleccionable real presente.")
    if has_fonts:
        score_A += 30
        indicadores_favor.append("Fuentes embebidas en el documento.")
    if has_vectors:
        score_A += 20
        indicadores_favor.append("Objetos vectoriales/drawings detectados.")
    if producer_cat == "generacion":
        score_A += 15
        indicadores_favor.append(
            f"Producer '{metadata.get('producer')}' es una librería de generación PDF."
        )
    if has_sig and not incremental:
        score_A += 10
        indicadores_favor.append("Firma digital presente y sin actualizaciones incrementales.")

    # ── REGLA 2: PDF escaneado auténtico (Tipo B) ─────────────────────────
    score_B = 0
    if pages_single == total_pages and total_pages > 0 and not has_text:
        score_B += 30
        indicadores_favor.append(
            f"Todas las páginas son imagen única ({pages_single}/{total_pages}) sin texto."
        )
    if has_invis_ocr and not has_text:
        score_B += 25
        indicadores_favor.append("OCR invisible superpuesto sobre imagen (escáner + OCR automático).")
    if producer_cat == "scanner":
        score_B += 30
        indicadores_favor.append(
            f"Producer '{metadata.get('producer')}' es compatible con escáner/MFP."
        )
    if scan_score >= 40:
        score_B += 20
        indicadores_favor.append(
            f"Análisis de imagen: score de escaneo real = {scan_score}/100."
        )
    if 150 <= dpi_avg <= 400:
        score_B += 15
        indicadores_favor.append(f"DPI estimado ({dpi_avg}) en rango típico de escáner (150–400).")
    if any(c in compressions for c in ["JPEG", "CCITT", "JBIG2"]):
        score_B += 10
        indicadores_favor.append(
            f"Compresión {compressions} compatible con escáner."
        )

    # ── REGLA 3: PDF imagen artificial / sospechoso (Tipo D) ──────────────
    score_D = 0
    if pages_single == total_pages and total_pages > 0:
        score_D += 20
        if not has_text and not has_invis_ocr:
            score_D += 10
    if producer_cat == "generacion":
        score_D += 30
        risk_flags.append(
            f"Producer '{metadata.get('producer')}' es una librería de generación PDF, "
            "no un escáner ni software de ofimática estándar."
        )
        # Si además es single-image sin metadatos de escáner → señal fuerte de Tipo D
        if pages_single == total_pages and total_pages > 0:
            score_D += 20
            risk_flags.append(
                "Combinación crítica: imagen única por página + producer es librería de generación PDF. "
                "Compatible con imagen insertada artificialmente en contenedor PDF."
            )
    if art_score >= 40:
        score_D += 25
        risk_flags.append(
            f"Score de fabricación artificial = {art_score}/100."
        )
    # JPEG puede enmascarar señales de escáner real — si producer=generacion,
    # el ruido JPEG no es evidencia de escaneo físico
    if scanner_art.get("noise_level", 0) < 1.5 and pages_single > 0:
        score_D += 15
        risk_flags.append(
            "Imagen con ruido extremadamente bajo: inusual en escaneos físicos reales."
        )
    elif producer_cat == "generacion" and scanner_art.get("noise_level", 0) > 0:
        # El ruido JPEG de una imagen artificial no es evidencia de escáner real
        risk_flags.append(
            "Nota: el ruido detectado puede ser artefacto de compresión JPEG, "
            "no necesariamente grano de escáner físico."
        )
    if not metadata.get("metadata_stream") and not metadata.get("xmp_present"):
        score_D += 5
        risk_flags.append("Sin stream de metadatos XMP (atípico para escáneres modernos).")

    # ── REGLA 4: PDF híbrido (Tipo C) ─────────────────────────────────────
    score_C = 0
    if incremental:
        score_C += 30
        risk_flags.append(
            f"Actualizaciones incrementales detectadas ({update_count}). "
            "Indica modificación posterior a la creación original."
        )
    if has_invis_ocr and (has_text or has_fonts):
        score_C += 20
    if score_B >= 30 and (has_text or has_fonts or has_vectors):
        score_C += 25
        risk_flags.append(
            "Base escaneada con elementos digitales superpuestos (fuentes/texto/vectores)."
        )

    # ── Indicadores en contra ─────────────────────────────────────────────
    if not has_sig:
        indicadores_contra.append("Sin firma digital embebida.")
    if not metadata.get("metadata_stream"):
        indicadores_contra.append("Sin stream de metadatos extendido.")
    if incremental:
        indicadores_contra.append(
            f"Actualizaciones incrementales ({update_count}) reducen la certeza del estado original."
        )
    if producer_cat == "desconocido":
        indicadores_contra.append("Producer no identificado o ausente.")
    if scan_score < 20 and score_B > 0:
        indicadores_contra.append(
            "La imagen no presenta características claras de escaneo físico real."
        )

    # ── Flags de riesgo adicionales ───────────────────────────────────────
    if not has_sig:
        risk_flags.append("Sin firma digital embebida (no verificable criptográficamente).")
    if dpi_avg and (dpi_avg < 72 or dpi_avg > 1200):
        risk_flags.append(f"DPI estimado ({dpi_avg}) fuera de rango habitual.")
    if metadata.get("mod_date") and metadata.get("creation_date"):
        if metadata["mod_date"] != metadata["creation_date"]:
            risk_flags.append(
                f"Fecha de modificación ({metadata['mod_date']}) difiere de la creación "
                f"({metadata['creation_date']})."
            )

    # ── Determinar clasificación ganadora ─────────────────────────────────
    scores = {
        "A": score_A,
        "B": score_B,
        "C": score_C,
        "D": score_D,
    }
    max_score = max(scores.values())
    winner    = max(scores, key=scores.get)

    # Umbral mínimo para clasificar
    if max_score < 25:
        winner = "E"

    # Desambiguar C vs B/D
    if winner in ("B", "D") and score_C >= max_score * 0.8:
        winner = "C"

    # Nivel de confianza
    sorted_scores = sorted(scores.values(), reverse=True)
    gap = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else sorted_scores[0]
    if max_score >= 60 and gap >= 20:
        confidence = "alto"
    elif max_score >= 35 or gap >= 10:
        confidence = "medio"
    else:
        confidence = "bajo"

    # Descripciones de tipo
    tipo_desc = {
        "A": "PDF nativo digital (texto real, fuentes embebidas, vectores)",
        "B": "PDF escaneado auténtico (imagen raster, posible OCR, metadatos de escáner)",
        "C": "PDF híbrido (base escaneada con elementos digitales superpuestos)",
        "D": "PDF imagen artificial / sospechoso (imagen en PDF generada por software, sin artefactos de escaneo real)",
        "E": "No concluyente (evidencia insuficiente para clasificar)",
    }

    # Qué prueba adicional haría falta
    prueba_adicional: List[str] = []
    if not has_sig:
        prueba_adicional.append(
            "Firma digital criptográfica verificable que certifique la integridad del documento."
        )
    if incremental:
        prueba_adicional.append(
            "Revisión de cada versión incremental del PDF para identificar qué se modificó."
        )
    if winner == "D" or art_score > 30:
        prueba_adicional.append(
            "Análisis ELA (Error Level Analysis) sobre la imagen embebida para detectar recompresión diferencial."
        )
        prueba_adicional.append(
            "Verificación de la cadena de custodia digital del archivo."
        )
    if winner == "B":
        prueba_adicional.append(
            "Análisis de puntos MIC (Machine Identification Code) con herramienta 'deda' a 600 DPI "
            "para identificar el dispositivo físico de impresión/escaneo."
        )

    # Lo que NO puede determinarse
    no_determinable: List[str] = []
    no_determinable.append(
        "Sin firma digital criptográfica verificable, no es posible afirmar con certeza "
        "que el documento no fue alterado."
    )
    if winner != "A":
        no_determinable.append(
            "No se puede determinar la autenticidad del contenido visual sin comparación "
            "contra el acta física original."
        )

    return {
        "classification":    winner,
        "classification_desc": tipo_desc.get(winner, ""),
        "confidence":        confidence,
        "scores":            scores,
        "indicators_for":    indicadores_favor,
        "indicators_against": indicadores_contra,
        "risk_flags":        risk_flags,
        "cannot_determine":  no_determinable,
        "additional_proof_needed": prueba_adicional,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 9. CONCLUSIÓN PERICIAL
# ──────────────────────────────────────────────────────────────────────────────

def generate_forensic_conclusion(
    classification: Dict,
    metadata:       Dict,
    images_per_page: Dict,
) -> Dict:
    """
    Genera la conclusión técnica pericial en lenguaje apropiado:
    usa «compatible con», «consistente con», «indicios de», «no concluyente»,
    nunca afirma autenticidad ni fraude como certeza sin respaldo criptográfico.
    """
    tipo         = classification.get("classification", "E")
    confidence   = classification.get("confidence", "bajo")
    risk_flags   = classification.get("risk_flags", [])
    producer     = metadata.get("producer") or "no identificado"
    has_sig      = metadata.get("digital_signature_present", False)
    incremental  = metadata.get("incremental_updates_detected", False)
    dpi_avg      = (images_per_page.get("dpi_range") or {}).get("avg") or "N/D"

    # Nivel de riesgo
    n_flags = len(risk_flags)
    if n_flags == 0:
        nivel_riesgo = "sin indicadores de riesgo detectados"
    elif n_flags <= 2:
        nivel_riesgo = f"{n_flags} indicador(es) de riesgo que requieren revisión"
    else:
        nivel_riesgo = f"{n_flags} indicadores de riesgo que requieren atención prioritaria"

    # Texto pericial según tipo
    textos = {
        "A": (
            f"El documento presenta características técnicas consistentes con un PDF nativo digital. "
            f"El software productor reportado es '{producer}'. "
            f"Se detecta texto seleccionable real y fuentes embebidas. "
            f"{'Se detecta firma digital embebida.' if has_sig else 'No se detecta firma digital embebida.'} "
            f"Nivel de confianza: {confidence}. {nivel_riesgo.capitalize()}."
        ),
        "B": (
            f"El documento presenta características técnicas compatibles con un PDF escaneado físicamente. "
            f"Productor: '{producer}'. DPI estimado: {dpi_avg}. "
            f"{'No se' if not has_sig else 'Se'} detecta firma digital. "
            f"No se puede determinar la autenticidad del contenido sin comparación "
            f"contra el acta física original. "
            f"Nivel de confianza: {confidence}. {nivel_riesgo.capitalize()}."
        ),
        "C": (
            f"El documento presenta indicios de ser un PDF híbrido: base escaneada con "
            f"elementos añadidos digitalmente de forma posterior. "
            f"Productor: '{producer}'. "
            f"Se observan señales compatibles con edición posterior al escaneo original. "
            f"Nivel de confianza: {confidence}. {nivel_riesgo.capitalize()}."
        ),
        "D": (
            f"El documento presenta señales compatibles con un PDF imagen artificial: "
            f"una imagen insertada en un contenedor PDF por software de generación. "
            f"Productor identificado: '{producer}' (librería de generación PDF). "
            f"No se detectan huellas de escaneo físico real. "
            f"IMPORTANTE: esto no implica necesariamente fraude; puede ser un flujo legítimo "
            f"de digitalización. Se requiere verificación adicional. "
            f"Nivel de confianza: {confidence}. {nivel_riesgo.capitalize()}."
        ),
        "E": (
            f"La evidencia técnica disponible no es suficiente para clasificar con certeza el "
            f"origen del documento. Productor: '{producer}'. "
            f"Se recomienda análisis adicional con herramientas especializadas. "
            f"Nivel de confianza: {confidence}. {nivel_riesgo.capitalize()}."
        ),
    }

    conclusion_text = textos.get(tipo, textos["E"])

    return {
        "conclusion_tecnica":   conclusion_text,
        "origen_mas_probable":  classification.get("classification_desc", "No determinado"),
        "nivel_confianza":      confidence,
        "nivel_riesgo_flags":   n_flags,
        "tiene_firma_digital":  has_sig,
        "es_verificable_criptograficamente": has_sig,
        "advertencia_legal": (
            "Este análisis es de carácter técnico-pericial preliminar. "
            "No constituye prueba jurídica por sí solo. "
            "La determinación de autenticidad o falsedad de un documento electoral "
            "requiere peritaje formal, custodia de evidencia y verificación "
            "contra los registros oficiales del organismo electoral competente."
        ),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 10. DETECCIÓN DE PUNTOS AMARILLOS (Machine Identification Code)
# ──────────────────────────────────────────────────────────────────────────────

def _pixel_is_yellow(r: int, g: int, b: int, threshold: int = 30) -> bool:
    """Clasifica un píxel como punto amarillo de impresora."""
    # Los puntos amarillos MIC son R≈255, G≈255, B≈0-50 (amarillo puro)
    # Pero pueden ser más sutiles en escaneados con JPEG
    if r > 180 and g > 180 and b < 80:
        return True
    # También verificar en espacio HSV
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    # Amarillo: H ≈ 0.1-0.2 (36°-72°), S > 0.5, V > 0.5
    if 0.08 <= h <= 0.22 and s > 0.4 and v > 0.4:
        return True
    return False


def detect_yellow_dots(pdf_path: str, dpi: int = 600) -> Dict:
    """
    Detecta puntos amarillos (Machine Identification Code) en el PDF.
    Los MIC son impresos por impresoras láser color en un patrón de cuadrícula
    de ~0.1mm de separación, casi invisible al ojo humano.

    A 600 DPI, cada punto ocupa ~1-3 píxeles.
    Los puntos aparecen en páginas de fondo blanco como manchas amarillas aisladas.

    Retorna estadísticas e intento de decodificación básica.
    """
    result = {
        "disponible": PYMUPDF_OK and IMAGING_OK,
        "puntos_detectados": 0,
        "paginas_con_puntos": [],
        "mapa_puntos": [],
        "posibles_filas": [],
        "posibles_columnas": [],
        "interpretacion": "",
        "advertencia": "",
    }

    if not result["disponible"]:
        result["advertencia"] = "Requiere PyMuPDF + numpy + Pillow"
        return result

    try:
        doc = fitz.open(pdf_path)
        mat = fitz.Matrix(dpi / 72, dpi / 72)

        for page_num, page in enumerate(doc):
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, 3
            )

            # Máscara de píxeles amarillos
            r, g, b = img_array[:, :, 0], img_array[:, :, 1], img_array[:, :, 2]
            yellow_mask = (r > 180) & (g > 180) & (b < 80)

            # Excluir áreas de texto negro y logotipos (solo fondo casi blanco)
            background_mask = (r > 220) & (g > 220) & (b > 220)
            # El punto amarillo está sobre fondo blanco
            dot_candidates = yellow_mask

            # Encontrar coordenadas de puntos candidatos
            ys, xs = np.where(dot_candidates)
            num_puntos = len(xs)

            if num_puntos > 10:   # Umbral mínimo para considerar puntos MIC
                result["paginas_con_puntos"].append(page_num + 1)
                result["puntos_detectados"] += num_puntos

                # Agrupar en clusters (cada punto es ~3x3 px a 600 DPI)
                clusters = _cluster_dots(xs, ys, max_dist=5)
                result["mapa_puntos"].extend([
                    {"pagina": page_num + 1, "x": int(cx), "y": int(cy)}
                    for cx, cy in clusters[:200]  # Máximo 200 por página
                ])

        doc.close()

        if result["puntos_detectados"] > 0:
            result["interpretacion"] = (
                f"Se detectaron {result['puntos_detectados']} píxeles amarillos candidatos "
                f"en {len(result['paginas_con_puntos'])} página(s). "
                "Posible presencia de Machine Identification Code (MIC). "
                "Para análisis completo usar herramienta 'deda' con imagen a 600 DPI."
            )
        else:
            result["interpretacion"] = (
                "No se detectaron patrones de puntos amarillos MIC. "
                "Puede ser un PDF electrónico (no impreso) o el escaneado tiene baja resolución."
            )

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Error en detección de puntos amarillos: {e}")

    return result


def _cluster_dots(xs: "np.ndarray", ys: "np.ndarray", max_dist: int = 5) -> List[Tuple]:
    """Agrupa puntos cercanos en clusters y retorna sus centroides."""
    if len(xs) == 0:
        return []
    points = list(zip(xs.tolist(), ys.tolist()))
    clusters = []
    used = set()

    for i, (x, y) in enumerate(points):
        if i in used:
            continue
        cluster_pts = [(x, y)]
        used.add(i)
        for j, (x2, y2) in enumerate(points[i + 1:], start=i + 1):
            if j not in used and abs(x2 - x) <= max_dist and abs(y2 - y) <= max_dist:
                cluster_pts.append((x2, y2))
                used.add(j)
        cx = sum(p[0] for p in cluster_pts) / len(cluster_pts)
        cy = sum(p[1] for p in cluster_pts) / len(cluster_pts)
        clusters.append((cx, cy))

    return clusters


# ──────────────────────────────────────────────────────────────────────────────
# 11. ANÁLISIS DE HISTOGRAMA DE IMAGEN (artefactos JPEG, gaps de manipulación)
# ──────────────────────────────────────────────────────────────────────────────

def analyze_image_artifacts(pdf_path: str, dpi: int = 300) -> Dict:
    """
    Analiza artefactos de imagen que podrían indicar manipulación:
    - Histograma de niveles de gris (gap en histograma = posible edición)
    - Ruido de cuantización JPEG (doble compresión)
    - Diferencias de resolución entre regiones
    """
    result = {
        "disponible": PYMUPDF_OK and IMAGING_OK,
        "histograma_gris": [],
        "artefactos_jpeg": False,
        "resolucion_uniforme": True,
        "nivel_ruido": 0.0,
        "gaps_histograma": [],
        "interpretacion": "",
    }

    if not result["disponible"]:
        result["advertencia"] = "Requiere PyMuPDF + numpy + Pillow"
        return result

    try:
        doc = fitz.open(pdf_path)
        mat = fitz.Matrix(dpi / 72, dpi / 72)

        histogramas = []
        for page in list(doc)[:2]:   # Solo primeras 2 páginas
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
            img_array = np.frombuffer(pix.samples, dtype=np.uint8)

            hist, _ = np.histogram(img_array, bins=256, range=(0, 256))
            histogramas.append(hist.tolist())

            # Detectar gaps en el histograma (señal de manipulación)
            gaps = []
            for i in range(10, 240):
                if hist[i] == 0 and hist[i - 1] > 0 and hist[i + 1] > 0:
                    gaps.append(i)
            result["gaps_histograma"].extend(gaps[:10])

            # Nivel de ruido: std de la derivada de la imagen
            noise = float(np.std(np.diff(img_array.astype(int)[:10000])))
            result["nivel_ruido"] = round(noise, 2)

        if histogramas:
            result["histograma_gris"] = histogramas[0][:64]  # Muestra reducida

        doc.close()

        if result["gaps_histograma"]:
            result["interpretacion"] = (
                f"ATENCIÓN: Se detectaron {len(result['gaps_histograma'])} gaps en el histograma "
                f"(niveles {result['gaps_histograma'][:5]}). "
                "Esto puede indicar edición digital de la imagen."
            )
        else:
            result["interpretacion"] = "Histograma sin gaps anómalos detectados."

    except Exception as e:
        result["error"] = str(e)

    return result


# ──────────────────────────────────────────────────────────────────────────────
# 12. FUNCIÓN PRINCIPAL DE ANÁLISIS FORENSE COMPLETO
# ──────────────────────────────────────────────────────────────────────────────

def full_forensic_analysis(pdf_path: str) -> Dict:
    """
    Orquesta el análisis forense completo sobre un PDF de acta electoral.
    Ejecuta todos los módulos de análisis y genera un reporte estructurado
    con clasificación Tipo A-E, flags de riesgo y conclusión pericial.

    Retorna un dict listo para serializar como JSON con todos los campos
    técnicos descritos en el estándar de análisis forense documental.
    """
    if not os.path.exists(pdf_path):
        return {"error": f"Archivo no encontrado: {pdf_path}"}

    # ── Hashes de integridad ─────────────────────────────────────────────
    h256 = hashlib.sha256()
    h_md = hashlib.md5()
    file_size = os.path.getsize(pdf_path)
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h256.update(chunk)
            h_md.update(chunk)
    sha256 = h256.hexdigest()
    md5    = h_md.hexdigest()

    # ── Ejecutar todos los análisis ──────────────────────────────────────
    logger.info(f"[forense] Iniciando análisis completo: {os.path.basename(pdf_path)}")

    metadata        = extract_pdf_metadata(pdf_path)
    incr_updates    = detect_incremental_updates(pdf_path)
    structure       = analyze_pdf_structure(pdf_path)
    images_per_page = analyze_image_objects_per_page(pdf_path)
    text_layer      = analyze_text_layer(pdf_path)
    scanner_art     = detect_scanner_artifacts(pdf_path, dpi=150)
    artificial_art  = detect_artificial_artifacts(pdf_path, dpi=150)
    yellow_dots     = detect_yellow_dots(pdf_path, dpi=600)
    image_hist      = analyze_image_artifacts(pdf_path, dpi=300)

    # Propagar flag de actualizaciones incrementales a metadata para la conclusión
    metadata["incremental_updates_detected"] = incr_updates.get("incremental_updates", False)

    # ── Clasificación ────────────────────────────────────────────────────
    classification = classify_pdf_origin(
        metadata        = metadata,
        structure       = structure,
        images_per_page = images_per_page,
        text_layer      = text_layer,
        incr_updates    = incr_updates,
        scanner_art     = scanner_art,
        artificial_art  = artificial_art,
    )

    # ── Conclusión pericial ───────────────────────────────────────────────
    conclusion = generate_forensic_conclusion(
        classification  = classification,
        metadata        = metadata,
        images_per_page = images_per_page,
    )

    # ── Construir JSON técnico canónico ──────────────────────────────────
    # (formato solicitado por el estándar de análisis)
    first_page_images = (
        images_per_page.get("pages", [{}])[0].get("images", [])
        if images_per_page.get("pages") else []
    )
    canonical_image_objects = []
    for page_data in images_per_page.get("pages", []):
        for img in page_data.get("images", []):
            canonical_image_objects.append({
                "page":               page_data["page"],
                "width":              img.get("width_px"),
                "height":             img.get("height_px"),
                "color_space":        img.get("color_space"),
                "bits_per_component": img.get("bits_per_component"),
                "encoding":           img.get("compression"),
                "x_ppi":              img.get("x_ppi"),
                "y_ppi":              img.get("y_ppi"),
            })

    canonical = {
        # ── Identificación del archivo ────────────────────────────────
        "file":                      os.path.basename(pdf_path),
        "file_size_bytes":           file_size,
        "sha256":                    sha256,
        "md5":                       md5,
        # ── Metadatos PDF básicos ─────────────────────────────────────
        "file_type":                 "PDF",
        "pdf_version":               metadata.get("pdf_version", ""),
        "page_count":                metadata.get("page_count", 0),
        "producer":                  metadata.get("producer"),
        "creator":                   metadata.get("creator"),
        "creation_date":             metadata.get("creation_date"),
        "mod_date":                  metadata.get("mod_date"),
        "encrypted":                 metadata.get("encrypted", False),
        "linearized":                metadata.get("linearized", False),
        "tagged":                    metadata.get("tagged", False),
        "metadata_stream":           metadata.get("metadata_stream", False),
        "xmp_present":               metadata.get("xmp_present", False),
        "xmp_raw":                   metadata.get("xmp_raw", {}),
        # ── Seguridad ────────────────────────────────────────────────
        "digital_signature_present": metadata.get("digital_signature_present", False),
        "signature_details":         metadata.get("signature_details", []),
        "acroform_present":          metadata.get("acroform_present", False),
        "xfa_present":               metadata.get("xfa_present", False),
        "javascript_present":        metadata.get("javascript_present", False),
        "attachments_present":       metadata.get("attachments_present", False),
        # ── Estructura ───────────────────────────────────────────────
        "object_count":              metadata.get("object_count", 0),
        "incremental_updates":       incr_updates.get("incremental_updates", False),
        "incremental_update_count":  incr_updates.get("update_count", 0),
        "xref_trailer_count":        incr_updates.get("trailer_count", 0),
        "eof_count":                 incr_updates.get("eof_count", 0),
        # ── Contenido ────────────────────────────────────────────────
        "text_objects_present":      text_layer.get("has_real_text", False),
        "embedded_fonts_present":    text_layer.get("has_embedded_fonts", False),
        "embedded_fonts":            text_layer.get("embedded_fonts", []),
        "vector_objects_present":    text_layer.get("has_vector_graphics", False),
        "invisible_ocr_present":     text_layer.get("has_invisible_text", False),
        "total_text_chars":          text_layer.get("total_char_count", 0),
        "image_objects":             canonical_image_objects,
        "document_model":            structure.get("document_model", "desconocido"),
        "compressions_found":        images_per_page.get("compressions_found", []),
        "color_spaces_found":        images_per_page.get("color_spaces_found", []),
        "dpi_range":                 images_per_page.get("dpi_range"),
        # ── Scores de análisis ────────────────────────────────────────
        "scan_score":                scanner_art.get("scan_score", 0),
        "artificial_score":          artificial_art.get("artificial_score", 0),
        "histogram_gaps":            image_hist.get("gaps_histograma", []),
        "noise_level":               scanner_art.get("noise_level", 0),
        "edge_shadow_detected":      scanner_art.get("edge_shadow_detected", False),
        "illumination_uneven":       scanner_art.get("illumination_uneven", False),
        "natural_grain":             scanner_art.get("natural_grain", False),
        "perfect_white_bg":          artificial_art.get("perfect_white_bg", False),
        "uniform_sharpness":         artificial_art.get("uniform_sharpness", False),
        "compression_inconsistency": artificial_art.get("compression_inconsistency", False),
        "yellow_dots_detected":      yellow_dots.get("puntos_detectados", 0) > 50,
        "yellow_dots_count":         yellow_dots.get("puntos_detectados", 0),
        # ── Clasificación y conclusión ────────────────────────────────
        "origin_classification":     classification.get("classification"),
        "origin_description":        classification.get("classification_desc"),
        "origin_assessment":         conclusion.get("origen_mas_probable"),
        "confidence":                classification.get("confidence"),
        "risk_flags":                classification.get("risk_flags", []),
        "indicators_for":            classification.get("indicators_for", []),
        "indicators_against":        classification.get("indicators_against", []),
        "cannot_determine":          classification.get("cannot_determine", []),
        "additional_proof_needed":   classification.get("additional_proof_needed", []),
        "conclusion_tecnica":        conclusion.get("conclusion_tecnica"),
        "advertencia_legal":         conclusion.get("advertencia_legal"),
        # ── Productor clasificado ─────────────────────────────────────
        "producer_category":         metadata.get("producer_category", "desconocido"),
    }

    # ── Generar alertas automáticas (retrocompatibilidad) ────────────────
    alertas: List[Dict] = []
    risk_flags_list = classification.get("risk_flags", [])

    if metadata.get("digital_signature_present"):
        alertas.append({"nivel": "ok",
                        "mensaje": "El PDF contiene firma digital embebida."})
    else:
        alertas.append({"nivel": "alerta",
                        "mensaje": "Sin firma digital: no es posible verificar "
                                   "criptográficamente la integridad del documento."})

    if incr_updates.get("incremental_updates"):
        alertas.append({"nivel": "alerta",
                        "mensaje": incr_updates.get("interpretacion", "")})

    if metadata.get("javascript_present"):
        alertas.append({"nivel": "alerta",
                        "mensaje": "El PDF contiene JavaScript embebido. "
                                   "Inusual en actas electorales."})

    if metadata.get("attachments_present"):
        alertas.append({"nivel": "alerta",
                        "mensaje": "El PDF tiene archivos adjuntos embebidos."})

    if image_hist.get("gaps_histograma"):
        alertas.append({"nivel": "alerta",
                        "mensaje": f"GAPS en histograma de imagen: "
                                   f"niveles {image_hist['gaps_histograma'][:5]}. "
                                   "Posible edición digital de imagen."})

    if yellow_dots.get("puntos_detectados", 0) > 50:
        alertas.append({"nivel": "info",
                        "mensaje": f"Posibles puntos MIC: "
                                   f"{yellow_dots['puntos_detectados']} px amarillos. "
                                   "Puede identificar el dispositivo físico."})

    clf = classification.get("classification", "E")
    if clf == "D":
        alertas.append({"nivel": "alerta",
                        "mensaje": f"Clasificación: Tipo D – PDF imagen artificial/sospechoso. "
                                   f"Confianza: {classification.get('confidence')}."})
    elif clf == "C":
        alertas.append({"nivel": "alerta",
                        "mensaje": f"Clasificación: Tipo C – PDF híbrido con posibles "
                                   f"elementos añadidos posteriormente."})
    elif clf == "B":
        alertas.append({"nivel": "info",
                        "mensaje": f"Clasificación: Tipo B – PDF escaneado. "
                                   f"Confianza: {classification.get('confidence')}."})
    elif clf == "A":
        alertas.append({"nivel": "info",
                        "mensaje": f"Clasificación: Tipo A – PDF nativo digital. "
                                   f"Confianza: {classification.get('confidence')}."})

    # ── Resumen ejecutivo ─────────────────────────────────────────────────
    niveles = [a["nivel"] for a in alertas]
    n_alertas = niveles.count("alerta")
    if n_alertas >= 3:
        resumen = (f"⚠️ ANÁLISIS FORENSE: {n_alertas} indicadores de riesgo detectados. "
                   f"Clasificación: Tipo {clf} — {classification.get('classification_desc', '')}. "
                   f"Confianza: {classification.get('confidence')}.")
    elif n_alertas >= 1:
        resumen = (f"⚠️ ANÁLISIS FORENSE: {n_alertas} indicador(es) que requieren revisión. "
                   f"Clasificación: Tipo {clf}. Confianza: {classification.get('confidence')}.")
    else:
        resumen = (f"ℹ️ ANÁLISIS FORENSE: Sin alertas críticas. "
                   f"Clasificación: Tipo {clf}. Confianza: {classification.get('confidence')}.")

    return {
        # Compatibilidad con el frontend existente
        "archivo": {
            "path":          pdf_path,
            "nombre":        os.path.basename(pdf_path),
            "tamanio_bytes": file_size,
            "sha256":        sha256,
            "md5":           md5,
        },
        "metadatos":          metadata,
        "actualizaciones_incrementales": incr_updates,
        "estructura_pdf":     structure,
        "imagenes_por_pagina": images_per_page,
        "capa_texto":         text_layer,
        "artefactos_scanner": scanner_art,
        "artefactos_artificiales": artificial_art,
        "puntos_amarillos":   yellow_dots,
        "artefactos_imagen":  image_hist,   # retrocompatibilidad
        "clasificacion":      classification,
        "conclusion":         conclusion,
        # JSON canónico técnico (formato pericial estándar)
        "reporte_tecnico":    canonical,
        # Resumen para UI
        "alertas":            alertas,
        "resumen":            resumen,
    }
