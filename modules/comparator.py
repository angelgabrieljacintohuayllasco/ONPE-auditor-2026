"""
Comparador: contrasta datos de la API ONPE vs datos extraídos por OCR.
Detecta discrepancias entre lo que dice el acta escaneada y
lo que digitalizó/digitó ONPE.
"""

import logging
import unicodedata
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _norm(nombre: str) -> str:
    """Normaliza nombre de partido para comparación (quita tildes y espacios)."""
    s = nombre.upper().strip()
    # Eliminar diacríticos (tildes, diéresis) para comparación robusta
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def _nombre_match(a: str, b: str, threshold: float = 0.5) -> bool:
    """
    Compara dos nombres de partidos con tolerancia.
    Usa coincidencia de substrings significativos.
    """
    a, b = _norm(a), _norm(b)
    if a == b:
        return True
    # Palabras significativas (≥4 chars) en común
    words_a = set(w for w in a.split() if len(w) >= 4)
    words_b = set(w for w in b.split() if len(w) >= 4)
    if not words_a or not words_b:
        return False
    intersection = words_a & words_b
    union = words_a | words_b
    jaccard = len(intersection) / len(union)
    return jaccard >= threshold


def compare_api_vs_ocr(
    api_data: Dict,
    ocr_data: Dict,
    codigo_mesa: str = "",
) -> Dict:
    """
    Compara los datos de la API ONPE con los extraídos por OCR del acta.

    api_data: datos del endpoint /actas/{id} (campo 'detalle')
    ocr_data: resultado de ocr_processor.parse_acta_text()

    Retorna un reporte de coincidencias y discrepancias.
    """
    reporte = {
        "codigo_mesa": codigo_mesa,
        "coincidencias": [],
        "discrepancias": [],
        "partidos_solo_api": [],
        "partidos_solo_ocr": [],
        "suma_votos_api": 0,
        "suma_votos_ocr": 0,
        "diferencia_totales": 0,
        "estado": "ok",
        "alertas": [],
    }

    # ── Totales generales ────────────────────────────────────────────────────

    api_electores   = api_data.get("totalElectoresHabiles")
    api_emitidos    = api_data.get("totalVotosEmitidos")
    api_validos     = api_data.get("totalVotosValidos")
    ocr_electores   = ocr_data.get("electores_habiles")
    ocr_emitidos    = ocr_data.get("total_votantes")
    ocr_validos     = ocr_data.get("votos_validos")

    for campo, api_val, ocr_val in [
        ("Electores Hábiles", api_electores, ocr_electores),
        ("Total Votantes",    api_emitidos,  ocr_emitidos),
        ("Votos Válidos",     api_validos,   ocr_validos),
    ]:
        if api_val is not None and ocr_val is not None:
            match = api_val == ocr_val
            entrada = {
                "campo": campo,
                "valor_api": api_val,
                "valor_ocr": ocr_val,
                "coincide": match,
            }
            if match:
                reporte["coincidencias"].append(entrada)
            else:
                reporte["discrepancias"].append(entrada)
                reporte["alertas"].append({
                    "nivel": "alerta",
                    "campo": campo,
                    "mensaje": f"{campo}: API={api_val} vs OCR={ocr_val} (diferencia={abs(api_val - ocr_val)})",
                })

    # ── Votos por partido ────────────────────────────────────────────────────

    # Construir dict API: {nombre_partido_norm: votos}
    api_votos = {}
    detalle_api = api_data.get("detalle", [])
    for item in detalle_api:
        nombre = item.get("descripcion", item.get("adDescripcion", ""))
        votos_item = item.get("nvotos", item.get("adVotos"))
        if votos_item is None:
            continue
        # Excluir nulos/blancos/impugnados del cruce de partidos
        if nombre.upper() in ("VOTOS NULOS", "VOTOS EN BLANCO", "VOTOS IMPUGNADOS",
                               "NULOS", "EN BLANCO", "IMPUGNADOS"):
            continue
        api_votos[_norm(nombre)] = int(votos_item)

    reporte["suma_votos_api"] = sum(api_votos.values())

    # OCR votos
    ocr_votos = {_norm(k): v for k, v in ocr_data.get("votos_por_partido", {}).items()}
    reporte["suma_votos_ocr"] = sum(ocr_votos.values())
    reporte["diferencia_totales"] = abs(reporte["suma_votos_api"] - reporte["suma_votos_ocr"])

    matched_ocr = set()
    for api_nombre, api_v in api_votos.items():
        # Buscar en OCR por nombre similar
        ocr_match = None
        for ocr_nombre in ocr_votos:
            if _nombre_match(api_nombre, ocr_nombre):
                ocr_match = ocr_nombre
                matched_ocr.add(ocr_nombre)
                break

        if ocr_match:
            ocr_v = ocr_votos[ocr_match]
            entrada = {
                "partido_api": api_nombre,
                "partido_ocr": ocr_match,
                "votos_api": api_v,
                "votos_ocr": ocr_v,
                "diferencia": api_v - ocr_v,
                "coincide": api_v == ocr_v,
            }
            if api_v == ocr_v:
                reporte["coincidencias"].append(entrada)
            else:
                reporte["discrepancias"].append(entrada)
                if abs(api_v - ocr_v) > 0:
                    reporte["alertas"].append({
                        "nivel": "alerta" if abs(api_v - ocr_v) > 5 else "info",
                        "campo": f"Votos {api_nombre}",
                        "mensaje": (
                            f"DISCREPANCIA: {api_nombre}: "
                            f"API={api_v} vs OCR={ocr_v} "
                            f"(diferencia={api_v - ocr_v})"
                        ),
                    })
        else:
            reporte["partidos_solo_api"].append({"partido": api_nombre, "votos": api_v})

    # Partidos en OCR que no se cruzaron con API
    for ocr_nombre in ocr_votos:
        if ocr_nombre not in matched_ocr:
            reporte["partidos_solo_ocr"].append({
                "partido": ocr_nombre,
                "votos": ocr_votos[ocr_nombre],
            })

    # ── Estado final ─────────────────────────────────────────────────────────
    if reporte["discrepancias"] or reporte["alertas"]:
        niveles = [a["nivel"] for a in reporte["alertas"]]
        reporte["estado"] = "alerta" if "alerta" in niveles else "info"
    else:
        reporte["estado"] = "ok"

    return reporte


def compare_votos_api_vs_grafico(api_data: Dict) -> Dict:
    """
    Verifica la consistencia interna de los datos de la API:
    - La suma de votos por partido debe igualar totalVotosValidos
    - Nulos + Blancos + Válidos = totalVotosEmitidos

    Retorna alertas si hay inconsistencias aritméticas en los propios datos de ONPE.
    """
    result = {
        "consistente": True,
        "alertas": [],
        "suma_partidos": 0,
        "total_validos_api": 0,
        "total_emitidos_api": 0,
        "nulos_api": 0,
        "blancos_api": 0,
        "diferencia_validos": 0,
        "diferencia_emitidos": 0,
    }

    total_validos  = api_data.get("totalVotosValidos", 0) or 0
    total_emitidos = api_data.get("totalVotosEmitidos", 0) or 0
    result["total_validos_api"]  = total_validos
    result["total_emitidos_api"] = total_emitidos

    suma_partidos = 0
    nulos = 0
    blancos = 0
    impugnados = 0

    for item in api_data.get("detalle", []):
        nombre = item.get("descripcion", item.get("adDescripcion", "")).upper()
        votos_item = item.get("nvotos", item.get("adVotos"))
        if votos_item is None:
            continue
        votos_int = int(votos_item)
        if "NULOS" in nombre:
            nulos += votos_int
        elif "BLANCO" in nombre:
            blancos += votos_int
        elif "IMPUGNADOS" in nombre:
            impugnados += votos_int
        else:
            suma_partidos += votos_int

    result["suma_partidos"] = suma_partidos
    result["nulos_api"] = nulos
    result["blancos_api"] = blancos

    # Verificación 1: suma de partidos ≈ votos válidos
    dif_val = suma_partidos - total_validos
    result["diferencia_validos"] = dif_val
    if abs(dif_val) > 1:    # tolerancia de 1 voto por redondeo
        result["consistente"] = False
        result["alertas"].append({
            "nivel": "alerta",
            "mensaje": (
                f"INCONSISTENCIA ARITMÉTICA: Suma de votos de partidos={suma_partidos} "
                f"≠ totalVotosValidos={total_validos} (diferencia={dif_val})"
            ),
        })

    # Verificación 2: válidos + nulos + blancos ≈ total emitidos
    total_contado = suma_partidos + nulos + blancos + impugnados
    dif_emit = total_contado - total_emitidos
    result["diferencia_emitidos"] = dif_emit
    if abs(dif_emit) > 1:
        result["consistente"] = False
        result["alertas"].append({
            "nivel": "alerta",
            "mensaje": (
                f"INCONSISTENCIA ARITMÉTICA: "
                f"partidos({suma_partidos})+nulos({nulos})+blancos({blancos})+impugnados({impugnados})"
                f"={total_contado} ≠ totalVotosEmitidos={total_emitidos} (diferencia={dif_emit})"
            ),
        })

    return result


# ── Comparación Híbrida ───────────────────────────────────────────────────────

def compare_hibrido(
    api_data: Dict,
    ocr_local: Dict,
    ocr_google: Dict,
    codigo_mesa: str = "",
) -> Dict:
    """
    Comparación híbrida: API vs OCR local + OCR Google.

    Lógica de consenso por campo:
      - COINCIDE   → API == al menos uno de los OCR
      - DISCREPANCIA → API ≠ ambos OCR (o falta dato en uno de ellos)
      - SOLO_UN_OCR → si falta un OCR, se evalúa sólo con el disponible

    Retorna el mismo esquema que compare_api_vs_ocr pero con campos extra
    `valor_ocr_local`, `valor_ocr_google`, `coincide_local`, `coincide_google`.
    """
    reporte = {
        "codigo_mesa":       codigo_mesa,
        "modo":              "hibrido",
        "coincidencias":     [],
        "discrepancias":     [],
        "partidos_solo_api": [],
        "partidos_solo_ocr": [],
        "suma_votos_api":    0,
        "diferencia_totales": 0,
        "estado":            "ok",
        "alertas":           [],
    }

    # ── Totales generales ────────────────────────────────────────────────────
    campos_totales = [
        ("Electores Hábiles", api_data.get("totalElectoresHabiles"),
         ocr_local.get("electores_habiles"), ocr_google.get("electores_habiles")),
        ("Total Votantes",    api_data.get("totalVotosEmitidos"),
         ocr_local.get("total_votantes"),    ocr_google.get("total_votantes")),
        ("Votos Válidos",     api_data.get("totalVotosValidos"),
         ocr_local.get("votos_validos"),     ocr_google.get("votos_validos")),
    ]
    for campo, api_v, loc_v, goo_v in campos_totales:
        if api_v is None:
            continue
        coin_local  = loc_v is not None and int(api_v) == int(loc_v)
        coin_google = goo_v is not None and int(api_v) == int(goo_v)
        coincide    = coin_local or coin_google
        entrada = {
            "campo":           campo,
            "valor_api":       int(api_v),
            "valor_ocr_local": int(loc_v)  if loc_v  is not None else None,
            "valor_ocr_google":int(goo_v)  if goo_v  is not None else None,
            "coincide_local":  coin_local,
            "coincide_google": coin_google,
            "coincide":        coincide,
        }
        if coincide:
            reporte["coincidencias"].append(entrada)
        else:
            reporte["discrepancias"].append(entrada)
            reporte["alertas"].append({
                "nivel":   "alerta",
                "campo":   campo,
                "mensaje": (
                    f"{campo}: API={api_v} | "
                    f"Local={loc_v if loc_v is not None else '—'} | "
                    f"Google={goo_v if goo_v is not None else '—'}"
                ),
            })

    # ── Votos por partido ────────────────────────────────────────────────────
    _EXCLUIR = {"VOTOS NULOS", "VOTOS EN BLANCO", "VOTOS IMPUGNADOS",
                "NULOS", "EN BLANCO", "IMPUGNADOS"}

    api_votos = {}
    for item in api_data.get("detalle", []):
        nom = item.get("descripcion", item.get("adDescripcion", ""))
        v   = item.get("nvotos") if item.get("nvotos") is not None else item.get("adVotos")
        if v is None or _norm(nom) in _EXCLUIR:
            continue
        api_votos[_norm(nom)] = int(v)

    reporte["suma_votos_api"] = sum(api_votos.values())

    loc_votos = {_norm(k): int(v) for k, v in (ocr_local.get("votos_por_partido") or {}).items()}
    goo_votos = {_norm(k): int(v) for k, v in (ocr_google.get("votos_por_partido") or {}).items()}

    matched_loc = set()
    matched_goo = set()

    for api_nom, api_v in api_votos.items():
        loc_nom = next((n for n in loc_votos if _nombre_match(api_nom, n)), None)
        goo_nom = next((n for n in goo_votos if _nombre_match(api_nom, n)), None)
        if loc_nom:
            matched_loc.add(loc_nom)
        if goo_nom:
            matched_goo.add(goo_nom)

        loc_v = loc_votos.get(loc_nom) if loc_nom else None
        goo_v = goo_votos.get(goo_nom) if goo_nom else None

        coin_local  = loc_v is not None and api_v == loc_v
        coin_google = goo_v is not None and api_v == goo_v
        coincide    = coin_local or coin_google

        entrada = {
            "campo":           api_nom,
            "valor_api":       api_v,
            "valor_ocr_local": loc_v,
            "valor_ocr_google":goo_v,
            "coincide_local":  coin_local,
            "coincide_google": coin_google,
            "coincide":        coincide,
        }
        if coincide:
            reporte["coincidencias"].append(entrada)
        else:
            reporte["discrepancias"].append(entrada)
            if loc_v is not None or goo_v is not None:
                reporte["alertas"].append({
                    "nivel":   "alerta",
                    "campo":   f"Votos {api_nom}",
                    "mensaje": (
                        f"DISCREPANCIA: {api_nom}: "
                        f"API={api_v} | Local={loc_v if loc_v is not None else '—'} | "
                        f"Google={goo_v if goo_v is not None else '—'}"
                    ),
                })
            else:
                reporte["partidos_solo_api"].append({"partido": api_nom, "votos": api_v})

    # Partidos OCR no cruzados
    for n in list(loc_votos.keys()) + list(goo_votos.keys()):
        if n not in matched_loc and n not in matched_goo and n not in api_votos:
            # Solo añadir una vez por nombre
            if not any(p["partido"] == n for p in reporte["partidos_solo_ocr"]):
                reporte["partidos_solo_ocr"].append({"partido": n})

    if reporte["discrepancias"] or reporte["alertas"]:
        reporte["estado"] = "alerta"

    return reporte
