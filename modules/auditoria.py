"""
Módulo de Auditoría Electoral ONPE 2026
Análisis de anomalías, timeline, inconsistencias y comparación con API general.
"""

import json
import logging
import sqlite3
import statistics
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

from config import DB_PATH, ONPE_BASE

logger = logging.getLogger(__name__)

# ── Constantes de tiempo (Unix epoch en SEGUNDOS) ────────────────────────────
# Peru = UTC-5
# Día de votación: Abril 12, 2026 (primera vuelta) y Abril 13, 2026 (segunda vuelta/continuación)
# April 12, 2026 00:00:00 UTC  → 1775952000
# April 13, 2026 00:00:00 UTC  → 1776038400
# April 14, 2026 00:00:00 UTC  → 1776124800
DIA_12_UTC_START = 1775952000
DIA_13_UTC_START = 1776038400
DIA_14_UTC_START = 1776124800
PERU_TZ_OFFSET   = -5 * 3600   # UTC-5 en segundos

# Hora de cierre de votación (7 PM hora Perú = 00:00 UTC del día siguiente)
# Cualquier acta digitalizada antes de las 7 PM del día de votación es normal
# Actas procesadas en horas muy tempranas (ej. 1-5 AM) merecen revisión

HORAS_SOSPECHOSAS_INICIO = 1    # 1 AM Peru
HORAS_SOSPECHOSAS_FIN    = 5    # 5 AM Peru
TIEMPO_MIN_PROCESO_MIN   = 1.5  # < 1.5 minutos = sospechosamente rápido
TIEMPO_MAX_PROCESO_HORAS = 72   # > 72 horas = proceso inusualmente lento


# ── Helpers de tiempo ─────────────────────────────────────────────────────────

def _ts_ms_to_sec(ts_ms) -> Optional[int]:
    if ts_ms is None:
        return None
    return int(ts_ms) // 1000


def _ts_to_dia(ts_ms) -> int:
    """Retorna 12 o 13 según el día de votación, 0 si no es ninguno."""
    if ts_ms is None:
        return 0
    ts = _ts_ms_to_sec(ts_ms)
    if DIA_12_UTC_START <= ts < DIA_13_UTC_START:
        return 12
    if DIA_13_UTC_START <= ts < DIA_14_UTC_START:
        return 13
    return 0


def _ts_to_hora_peru(ts_ms) -> int:
    """Convierte timestamp ms a hora local Perú (UTC-5), 0-23."""
    if ts_ms is None:
        return -1
    ts_sec = _ts_ms_to_sec(ts_ms) + PERU_TZ_OFFSET
    return int((ts_sec % 86400) // 3600)


def _ts_to_datetime_peru(ts_ms) -> Optional[str]:
    """Convierte timestamp ms a string legible en hora Perú."""
    if ts_ms is None:
        return None
    dt = datetime.fromtimestamp(_ts_ms_to_sec(ts_ms), tz=timezone.utc)
    dt_peru = dt + timedelta(hours=-5)
    return dt_peru.strftime("%d/%m/%Y %H:%M:%S")


# ── Extracción de datos ───────────────────────────────────────────────────────

def _extract_timeline(api_json: dict) -> dict:
    """Extrae eventos de la lineaTiempo del json del acta."""
    timeline_raw = api_json.get("lineaTiempo", [])
    result = {}
    code_map = {
        "T": "digitalizacion",
        "D": "digitacion",
        "C": "contabilizada",
        "O": "observada",
        "J": "enviada_jee",
    }
    for item in (timeline_raw or []):
        code = item.get("codigoEstadoActa", "")
        ts   = item.get("fechaRegistro")
        key  = code_map.get(code)
        if key and ts:
            result[key] = ts
    return result


def _get_all_actas_raw() -> List[sqlite3.Row]:
    """Devuelve todas las actas con api_json no vacío."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT codigo_mesa, id_eleccion, tipo_eleccion, api_json "
        "FROM actas WHERE api_json IS NOT NULL AND api_json != '{}' AND api_json != ''"
    ).fetchall()
    conn.close()
    return rows


# ── Análisis de timeline ──────────────────────────────────────────────────────

def get_timeline_stats() -> Dict:
    """
    Analiza el timeline de todas las actas en DB.
    Retorna distribución por hora, día de votación, velocidad de procesamiento
    y lista de anomalías de timeline.
    """
    rows = _get_all_actas_raw()

    stats = {
        "total": len(rows),
        "con_timeline": 0,
        "por_dia": {"12": 0, "13": 0, "otro": 0},
        "por_hora_digitalizacion": {str(h): 0 for h in range(24)},
        "por_hora_contabilizada":  {str(h): 0 for h in range(24)},
        "velocidad_minutos": [],   # list of {mesa, tipo, minutos}
        "actas_dia12": [],
        "actas_dia13": [],
        "anomalias": [],
        "por_tipo_dia": {},        # {tipo_eleccion: {dia12, dia13, otro}}
        "acumulado_por_hora": {},  # Para gráfica de ritmo de contabilización
    }

    acum_12 = {str(h): 0 for h in range(24)}
    acum_13 = {str(h): 0 for h in range(24)}

    for row in rows:
        try:
            api_json = json.loads(row["api_json"] or "{}")
        except Exception:
            continue

        tl = _extract_timeline(api_json)
        if not tl:
            continue

        stats["con_timeline"] += 1
        tipo = row["tipo_eleccion"] or f"Elección {row['id_eleccion']}"
        mesa = row["codigo_mesa"]

        ts_dig  = tl.get("digitalizacion")
        ts_cont = tl.get("contabilizada")
        ts_digt = tl.get("digitacion")

        # Día de votación (basado en digitalización o contabilización)
        ts_ref = ts_dig or ts_digt or ts_cont
        dia    = _ts_to_dia(ts_ref)
        dia_key = str(dia) if dia in (12, 13) else "otro"
        stats["por_dia"][dia_key] = stats["por_dia"].get(dia_key, 0) + 1

        # Registro por tipo/día
        if tipo not in stats["por_tipo_dia"]:
            stats["por_tipo_dia"][tipo] = {"dia12": 0, "dia13": 0, "otro": 0}
        stats["por_tipo_dia"][tipo][f"dia{dia}" if dia in (12, 13) else "otro"] += 1

        # Distribución por hora
        if ts_dig:
            h = _ts_to_hora_peru(ts_dig)
            if 0 <= h < 24:
                stats["por_hora_digitalizacion"][str(h)] += 1
                if dia == 12: acum_12[str(h)] += 1
                elif dia == 13: acum_13[str(h)] += 1
        if ts_cont:
            h = _ts_to_hora_peru(ts_cont)
            if 0 <= h < 24:
                stats["por_hora_contabilizada"][str(h)] += 1

        # Velocidad de procesamiento
        if ts_dig and ts_cont:
            minutos = (ts_cont - ts_dig) / 60000.0
            stats["velocidad_minutos"].append({
                "mesa":    mesa,
                "tipo":    tipo,
                "minutos": round(minutos, 1),
                "dia":     dia,
            })

            # Anomalía: demasiado rápido o demasiado lento
            if minutos < TIEMPO_MIN_PROCESO_MIN:
                stats["anomalias"].append({
                    "mesa":       mesa,
                    "id_eleccion": row["id_eleccion"],
                    "tipo":       tipo,
                    "categoria":  "timeline",
                    "motivo":     f"Procesamiento extremadamente rápido ({minutos:.1f} min)",
                    "severidad":  "alta",
                    "detalle":    {
                        "digitalizacion": _ts_to_datetime_peru(ts_dig),
                        "contabilizada":  _ts_to_datetime_peru(ts_cont),
                        "minutos":        round(minutos, 1),
                    },
                })
            elif minutos > TIEMPO_MAX_PROCESO_HORAS * 60:
                stats["anomalias"].append({
                    "mesa":       mesa,
                    "id_eleccion": row["id_eleccion"],
                    "tipo":       tipo,
                    "categoria":  "timeline",
                    "motivo":     f"Proceso inusualmente largo ({minutos/60:.1f} horas)",
                    "severidad":  "media",
                    "detalle":    {
                        "digitalizacion": _ts_to_datetime_peru(ts_dig),
                        "contabilizada":  _ts_to_datetime_peru(ts_cont),
                        "horas":          round(minutos / 60, 1),
                    },
                })

        # Anomalía: digitalizacion en hora sospechosa (1-5 AM Perú)
        if ts_dig:
            h = _ts_to_hora_peru(ts_dig)
            if HORAS_SOSPECHOSAS_INICIO <= h <= HORAS_SOSPECHOSAS_FIN:
                stats["anomalias"].append({
                    "mesa":       mesa,
                    "id_eleccion": row["id_eleccion"],
                    "tipo":       tipo,
                    "categoria":  "horario",
                    "motivo":     f"Digitalización en horario inusual ({h}:00 AM hora Perú)",
                    "severidad":  "baja",
                    "detalle":    {"hora_peru": h, "timestamp": _ts_to_datetime_peru(ts_dig)},
                })

        # Listas por día
        acta_entry = {
            "mesa": mesa,
            "tipo": tipo,
            "digitalizacion": _ts_to_datetime_peru(ts_dig),
            "digitacion":     _ts_to_datetime_peru(ts_digt),
            "contabilizada":  _ts_to_datetime_peru(ts_cont),
        }
        if dia == 12:
            stats["actas_dia12"].append(acta_entry)
        elif dia == 13:
            stats["actas_dia13"].append(acta_entry)

    stats["acumulado_dia12"] = acum_12
    stats["acumulado_dia13"] = acum_13

    # Estadísticas de velocidad
    tiempos = [v["minutos"] for v in stats["velocidad_minutos"]]
    if tiempos:
        stats["velocidad_media"]   = round(statistics.mean(tiempos), 1)
        stats["velocidad_mediana"] = round(statistics.median(tiempos), 1)
        stats["velocidad_min"]     = round(min(tiempos), 1)
        stats["velocidad_max"]     = round(max(tiempos), 1)
        # Histograma de velocidad (buckets de 10 min)
        buckets = {}
        for t in tiempos:
            b = int(t // 10) * 10
            buckets[b] = buckets.get(b, 0) + 1
        stats["velocidad_histograma"] = sorted(
            [{"desde": k, "hasta": k + 10, "count": v} for k, v in buckets.items()],
            key=lambda x: x["desde"]
        )

    return stats


# ── Inconsistencias de suma interna ──────────────────────────────────────────

def get_inconsistencias_suma() -> List[Dict]:
    """
    Detecta actas donde la suma interna de votos no coincide con los totales declarados.
    totalVotosEmitidos debe = sum(partidos) + nulos + blancos + impugnados
    totalVotosValidos  debe = sum(partidos)
    """
    rows = _get_all_actas_raw()
    inconsistencias = []

    for row in rows:
        try:
            api_json = json.loads(row["api_json"] or "{}")
        except Exception:
            continue

        total_emitidos = api_json.get("totalVotosEmitidos")
        total_validos  = api_json.get("totalVotosValidos")
        detalle        = api_json.get("detalle", [])
        electores      = api_json.get("totalElectoresHabiles")

        if not detalle or total_emitidos is None:
            continue

        suma_todos    = 0
        suma_validos  = 0
        nulos = blancos = impugnados = 0

        for item in detalle:
            v = item.get("adVotos")
            if v is None:
                continue
            v    = int(v)
            desc = (item.get("adDescripcion") or "").upper()
            cod  = str(item.get("adCodigo") or "")

            if cod in ("81", "82", "83") or "NULO" in desc:
                nulos += v
            elif cod == "80" or "BLANCO" in desc:
                blancos += v
            elif "IMPUGN" in desc:
                impugnados += v
            else:
                suma_validos += v
            suma_todos += v

        total_emitidos_int = int(total_emitidos)
        total_validos_int  = int(total_validos) if total_validos is not None else None

        dif_emitidos = suma_todos - total_emitidos_int
        dif_validos  = (suma_validos - total_validos_int) if total_validos_int is not None else None

        has_issue = abs(dif_emitidos) > 0 or (dif_validos is not None and abs(dif_validos) > 0)
        if has_issue:
            # Participación declarada vs calculable
            part_declarada  = api_json.get("porcentajeParticipacionCiudadana")
            part_calculada  = (total_emitidos_int / electores * 100) if electores else None

            inconsistencias.append({
                "mesa":                   row["codigo_mesa"],
                "id_eleccion":            row["id_eleccion"],
                "tipo":                   row["tipo_eleccion"] or f"Elección {row['id_eleccion']}",
                "total_emitidos_api":     total_emitidos_int,
                "suma_votos_calculada":   suma_todos,
                "dif_emitidos":           dif_emitidos,
                "total_validos_api":      total_validos_int,
                "suma_validos_calculada": suma_validos,
                "dif_validos":            dif_validos,
                "nulos":                  nulos,
                "blancos":                blancos,
                "impugnados":             impugnados,
                "electores":              electores,
                "participacion_declarada": part_declarada,
                "participacion_calculada": round(part_calculada, 3) if part_calculada else None,
                "severidad":              "alta" if abs(dif_emitidos) > 1 else "baja",
            })

    return sorted(inconsistencias, key=lambda x: abs(x["dif_emitidos"]), reverse=True)


# ── Votos por partido (suma local) ────────────────────────────────────────────

def get_votos_por_partido_local() -> Dict[int, List[Dict]]:
    """
    Suma los votos por partido de todas las actas locales.
    Retorna {id_eleccion: [{codigo, nombre, votos, mesas}]}
    """
    rows = _get_all_actas_raw()
    # {id_eleccion: {codigo: {nombre, votos, mesas}}}
    acumulado: Dict[int, Dict[str, Dict]] = {}

    for row in rows:
        try:
            api_json = json.loads(row["api_json"] or "{}")
        except Exception:
            continue

        id_elec = row["id_eleccion"]
        detalle = api_json.get("detalle", [])

        for item in detalle:
            codigo = str(item.get("adCodigo") or item.get("adAgrupacionPolitica") or "")
            nombre = (item.get("adDescripcion") or "").strip()
            v      = item.get("adVotos")
            if v is None or not codigo or not nombre:
                continue

            desc_up = nombre.upper()
            if any(x in desc_up for x in ("NULO", "BLANCO", "IMPUGN")):
                continue

            if id_elec not in acumulado:
                acumulado[id_elec] = {}
            if codigo not in acumulado[id_elec]:
                acumulado[id_elec][codigo] = {"codigo": codigo, "nombre": nombre, "votos": 0, "mesas": 0}
            acumulado[id_elec][codigo]["votos"] += int(v)
            acumulado[id_elec][codigo]["mesas"] += 1

    result = {}
    for id_elec, partidos in acumulado.items():
        result[id_elec] = sorted(partidos.values(), key=lambda x: x["votos"], reverse=True)
    return result


# ── Comparación local vs API general ─────────────────────────────────────────

def compare_local_vs_general(resumen_rows: list) -> Dict:
    """
    Compara nuestros totales locales vs los totales de la API general.
    resumen_rows: output de db.get_all_resumen_general()
    """
    local_votos = get_votos_por_partido_local()
    result      = {}

    for row in resumen_rows:
        id_elec        = row["id_eleccion"]
        participantes  = row.get("participantes_json", [])
        totales        = row.get("totales_json", {})
        tipo           = row.get("tipo_eleccion", f"Elección {id_elec}")

        # Construir dict por código de partido
        api_por_codigo = {}
        for p in participantes:
            codigo = str(p.get("codigoAgrupacionPolitica", ""))
            if codigo:
                api_por_codigo[codigo] = {
                    "nombre":            p.get("nombreAgrupacionPolitica", ""),
                    "votos_api":         p.get("totalVotosValidos", 0) or 0,
                    "pct_validos_api":   p.get("porcentajeVotosValidos", 0) or 0,
                }

        local_list = {p["codigo"]: p for p in local_votos.get(id_elec, [])}

        comparacion = []
        for codigo, api_data in api_por_codigo.items():
            local   = local_list.get(codigo, {})
            v_api   = api_data["votos_api"]
            v_local = local.get("votos", 0)
            diff    = v_api - v_local
            diff_pct = round(diff / v_api * 100, 2) if v_api > 0 else 0
            comparacion.append({
                "codigo":       codigo,
                "nombre":       api_data["nombre"],
                "votos_api":    v_api,
                "votos_local":  v_local,
                "mesas_local":  local.get("mesas", 0),
                "diff":         diff,
                "diff_pct":     diff_pct,
                "pct_api":      api_data["pct_validos_api"],
                "anomalia":     abs(diff_pct) > 15,
            })

        result[str(id_elec)] = {
            "tipo":         tipo,
            "id_eleccion":  id_elec,
            "totales_api":  totales,
            "partidos":     sorted(comparacion, key=lambda x: x["votos_api"], reverse=True),
            "fecha_captura": row.get("fecha_captura", ""),
        }

    return result


# ── Anomalías estadísticas por partido (outliers por mesa) ───────────────────

def get_anomalias_por_partido() -> List[Dict]:
    """
    Detecta mesas donde un partido obtuvo un porcentaje estadísticamente anómalo.
    Usa z-score > 3 σ respecto a la distribución de mesas con datos.
    """
    rows = _get_all_actas_raw()
    if not rows:
        return []

    # {id_eleccion: {codigo_partido: [pct_votos_validos, ...]}}
    party_pcts: Dict[int, Dict[str, List[float]]] = {}

    # Primera pasada: recolectar porcentajes
    mesa_records = []
    for row in rows:
        try:
            api_json = json.loads(row["api_json"] or "{}")
        except Exception:
            continue

        id_elec        = row["id_eleccion"]
        total_validos  = api_json.get("totalVotosValidos") or 0
        if not total_validos:
            continue

        detalle = api_json.get("detalle", [])
        partidos_mesa = {}
        for item in detalle:
            codigo = str(item.get("adCodigo") or "")
            v      = item.get("adVotos")
            desc   = (item.get("adDescripcion") or "").upper()
            nombre = (item.get("adDescripcion") or "").strip()
            if v is None or not codigo:
                continue
            if any(x in desc for x in ("NULO", "BLANCO", "IMPUGN")) or int(codigo) >= 80:
                continue
            pct = int(v) / total_validos * 100
            partidos_mesa[codigo] = {"nombre": nombre, "votos": int(v), "pct": pct}
            if id_elec not in party_pcts:
                party_pcts[id_elec] = {}
            if codigo not in party_pcts[id_elec]:
                party_pcts[id_elec][codigo] = []
            party_pcts[id_elec][codigo].append(pct)

        mesa_records.append({
            "mesa":       row["codigo_mesa"],
            "id_eleccion": id_elec,
            "tipo":       row["tipo_eleccion"] or f"Elección {id_elec}",
            "partidos":   partidos_mesa,
        })

    # Calcular stats por partido
    party_stats: Dict[int, Dict[str, Dict]] = {}
    for id_elec, partidos in party_pcts.items():
        party_stats[id_elec] = {}
        for codigo, pcts in partidos.items():
            if len(pcts) < 3:
                continue
            mean = statistics.mean(pcts)
            std  = statistics.stdev(pcts) if len(pcts) > 1 else 0
            party_stats[id_elec][codigo] = {"mean": mean, "std": std, "n": len(pcts)}

    # Segunda pasada: detectar outliers
    anomalias = []
    for mr in mesa_records:
        id_elec = mr["id_eleccion"]
        if id_elec not in party_stats:
            continue
        for codigo, pdata in mr["partidos"].items():
            pst = party_stats[id_elec].get(codigo)
            if not pst or pst["std"] < 0.01:
                continue
            z = abs(pdata["pct"] - pst["mean"]) / pst["std"]
            if z < 2.5:
                continue
            anomalias.append({
                "mesa":           mr["mesa"],
                "id_eleccion":    id_elec,
                "tipo":           mr["tipo"],
                "partido":        pdata["nombre"],
                "codigo_partido": codigo,
                "votos":          pdata["votos"],
                "pct":            round(pdata["pct"], 2),
                "media_pct":      round(pst["mean"], 2),
                "std_pct":        round(pst["std"], 2),
                "z_score":        round(z, 2),
                "motivo":         (
                    "Porcentaje muy por encima de la media" if pdata["pct"] > pst["mean"]
                    else "Porcentaje muy por debajo de la media"
                ),
                "severidad":      "alta" if z > 4 else "media",
            })

    return sorted(anomalias, key=lambda x: x["z_score"], reverse=True)[:200]


# ── Ranking de mesas más sospechosas ─────────────────────────────────────────

def get_mesas_sospechosas(limit: int = 50) -> List[Dict]:
    """
    Combina todas las señales de alerta para dar un ranking de mesas sospechosas.
    Cada señal suma puntos al score de sospecha.
    """
    rows = _get_all_actas_raw()
    scores: Dict[str, Dict] = {}

    def _add(mesa, tipo, motivo, puntos, detalle=None):
        key = f"{mesa}|{tipo}"
        if key not in scores:
            scores[key] = {"mesa": mesa, "tipo": tipo, "score": 0, "señales": []}
        scores[key]["score"] += puntos
        scores[key]["señales"].append({"motivo": motivo, "puntos": puntos, "detalle": detalle or {}})

    for row in rows:
        try:
            api_json = json.loads(row["api_json"] or "{}")
        except Exception:
            continue

        mesa  = row["codigo_mesa"]
        tipo  = row["tipo_eleccion"] or f"Elección {row['id_eleccion']}"
        tl    = _extract_timeline(api_json)

        ts_dig  = tl.get("digitalizacion")
        ts_cont = tl.get("contabilizada")

        # Señal 1: procesamiento ultrarrápido
        if ts_dig and ts_cont:
            mins = (ts_cont - ts_dig) / 60000.0
            if mins < TIEMPO_MIN_PROCESO_MIN:
                _add(mesa, tipo, f"Proceso ultrarrápido ({mins:.1f} min)", 50, {"minutos": mins})
            elif mins < 5:
                _add(mesa, tipo, f"Proceso muy rápido ({mins:.1f} min)", 20, {"minutos": mins})

        # Señal 2: hora sospechosa
        if ts_dig:
            h = _ts_to_hora_peru(ts_dig)
            if HORAS_SOSPECHOSAS_INICIO <= h <= HORAS_SOSPECHOSAS_FIN:
                _add(mesa, tipo, f"Digitalización a las {h:02d}:xx AM (hora Perú)", 15, {"hora": h})

        # Señal 3: inconsistencia interna de votos
        total_emitidos = api_json.get("totalVotosEmitidos")
        total_validos  = api_json.get("totalVotosValidos")
        detalle        = api_json.get("detalle", [])
        if total_emitidos and detalle:
            suma = sum(int(i.get("adVotos") or 0) for i in detalle if i.get("adVotos") is not None)
            dif  = abs(suma - int(total_emitidos))
            if dif > 0:
                _add(mesa, tipo, f"Suma de votos no coincide (Δ={dif})", 30 + dif, {"diferencia": dif})

        # Señal 4: participación inusualmente alta
        electores = api_json.get("totalElectoresHabiles") or 0
        emitidos  = int(total_emitidos) if total_emitidos else 0
        if electores and emitidos:
            pct = emitidos / electores * 100
            if pct > 98:
                _add(mesa, tipo, f"Participación sospechosamente alta ({pct:.1f}%)", 25, {"pct": pct})
            elif pct > 95:
                _add(mesa, tipo, f"Participación muy alta ({pct:.1f}%)", 10, {"pct": pct})

    ranked = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
    return ranked[:limit]


# ── Resumen ejecutivo de auditoría ────────────────────────────────────────────

def get_resumen_auditoria() -> Dict:
    """Resumen rápido de todas las métricas de auditoría."""
    rows  = _get_all_actas_raw()
    total = len(rows)
    con_timeline = 0
    dia12 = dia13 = otro_dia = 0
    total_inconsistencias = 0
    actas_rapidas = 0

    for row in rows:
        try:
            api_json = json.loads(row["api_json"] or "{}")
        except Exception:
            continue

        tl = _extract_timeline(api_json)
        if tl:
            con_timeline += 1
            ts_ref = tl.get("digitalizacion") or tl.get("digitacion") or tl.get("contabilizada")
            dia    = _ts_to_dia(ts_ref)
            if dia == 12:   dia12 += 1
            elif dia == 13: dia13 += 1
            else:           otro_dia += 1

            ts_dig  = tl.get("digitalizacion")
            ts_cont = tl.get("contabilizada")
            if ts_dig and ts_cont:
                mins = (ts_cont - ts_dig) / 60000.0
                if mins < TIEMPO_MIN_PROCESO_MIN:
                    actas_rapidas += 1

        # Inconsistencias
        total_emitidos = api_json.get("totalVotosEmitidos")
        detalle        = api_json.get("detalle", [])
        if total_emitidos and detalle:
            suma = sum(int(i.get("adVotos") or 0) for i in detalle if i.get("adVotos") is not None)
            if abs(suma - int(total_emitidos)) > 0:
                total_inconsistencias += 1

    return {
        "total_actas_analizadas": total,
        "con_timeline":           con_timeline,
        "dia_votacion_12":        dia12,
        "dia_votacion_13":        dia13,
        "dia_desconocido":        otro_dia,
        "inconsistencias_suma":   total_inconsistencias,
        "actas_proceso_rapido":   actas_rapidas,
    }


# ── Participación ciudadana vs votos locales ──────────────────────────────────

# Límite real: cada mesa peruana tiene máx. 300 electores hábiles
MAX_ELECTORES_MESA    = 300
# Umbral de participación sospechosamente alta a nivel mesa
PCT_PART_ALTO         = 97.0
# Diferencia % entre participación oficial y suma local (agregado)
UMBRAL_DIFERENCIA_PCT = 5.0


def get_participacion_analysis(participacion_cache: Optional[dict] = None) -> Dict:
    """
    Analiza la coherencia entre la participación ciudadana oficial (ONPE)
    y los datos de actas locales.

    Detecta:
    - Votos emitidos por mesa > máximo posible de electores (>300)
    - Participación declarada en acta > 100%
    - Desfase entre suma local de votos y total oficial de asistentes
    - Mesas con participación estadísticamente anómala (z-score > 2.5σ)
    - Votos emitidos > electores hábiles declarados en la propia acta

    Args:
        participacion_cache: resultado de db.get_participacion() (puede ser None)
    """
    rows  = _get_all_actas_raw()

    # Totales oficiales ONPE (si tenemos caché)
    oficial = {}
    if participacion_cache:
        oficial = participacion_cache.get("totales_json", {})

    total_electores_onpe   = oficial.get("totalElectoresHabiles", 0) or 0
    total_asistentes_onpe  = oficial.get("totalAsistentes", 0) or 0
    total_ausentes_onpe    = oficial.get("totalAusentes", 0) or 0
    pct_asistentes_onpe    = oficial.get("porcentajeAsistentes", 0) or 0
    pct_ausentes_onpe      = oficial.get("porcentajeAusentes", 0) or 0
    pendientes_onpe        = total_electores_onpe - total_asistentes_onpe - total_ausentes_onpe

    # Acumular datos locales
    suma_emitidos_local    = 0
    suma_electores_local   = 0
    mesas_analizadas       = 0
    pcts_participacion     = []   # lista de % por mesa (para estadísticas)
    anomalias              = []
    mesas_detalle          = []   # para histograma

    for row in rows:
        try:
            api_json = json.loads(row["api_json"] or "{}")
        except Exception:
            continue

        mesa           = row["codigo_mesa"]
        tipo           = row["tipo_eleccion"] or f"Elección {row['id_eleccion']}"
        emitidos       = api_json.get("totalVotosEmitidos")
        electores      = api_json.get("totalElectoresHabiles")
        pct_declarado  = api_json.get("porcentajeParticipacionCiudadana")

        if emitidos is None:
            continue

        emitidos_int  = int(emitidos)
        electores_int = int(electores) if electores else 0
        mesas_analizadas += 1
        suma_emitidos_local += emitidos_int
        if electores_int:
            suma_electores_local += electores_int

        # Calcular participación real
        pct_real = (emitidos_int / electores_int * 100) if electores_int > 0 else None

        if pct_real is not None:
            pcts_participacion.append(pct_real)
            mesas_detalle.append({
                "mesa":           mesa,
                "tipo":           tipo,
                "emitidos":       emitidos_int,
                "electores":      electores_int,
                "pct":            round(pct_real, 3),
                "pct_declarado":  round(float(pct_declarado), 3) if pct_declarado else None,
            })

        # Anomalía 1: emitidos > electores de la propia acta
        if electores_int and emitidos_int > electores_int:
            anomalias.append({
                "mesa":      mesa,
                "tipo":      tipo,
                "categoria": "votos_excedentes",
                "motivo":    f"Votos emitidos ({emitidos_int}) > electores hábiles ({electores_int})",
                "severidad": "alta",
                "detalle":   {
                    "emitidos":  emitidos_int,
                    "electores": electores_int,
                    "exceso":    emitidos_int - electores_int,
                },
            })

        # Anomalía 2: emitidos > máximo absoluto de mesa (300)
        if emitidos_int > MAX_ELECTORES_MESA:
            anomalias.append({
                "mesa":      mesa,
                "tipo":      tipo,
                "categoria": "sobre_capacidad",
                "motivo":    f"Votos ({emitidos_int}) superan máximo de {MAX_ELECTORES_MESA} por mesa",
                "severidad": "alta" if emitidos_int > MAX_ELECTORES_MESA * 1.1 else "media",
                "detalle":   {"emitidos": emitidos_int, "max_mesa": MAX_ELECTORES_MESA},
            })

        # Anomalía 3: participación > 100%
        if pct_real is not None and pct_real > 100:
            anomalias.append({
                "mesa":      mesa,
                "tipo":      tipo,
                "categoria": "participacion_imposible",
                "motivo":    f"Participación {pct_real:.1f}% > 100%",
                "severidad": "alta",
                "detalle":   {"pct_real": pct_real, "emitidos": emitidos_int, "electores": electores_int},
            })

        # Anomalía 4: discrepancia entre % declarado y % calculado
        if pct_real is not None and pct_declarado is not None:
            diff_pct = abs(pct_real - float(pct_declarado))
            if diff_pct > 1.0:
                anomalias.append({
                    "mesa":      mesa,
                    "tipo":      tipo,
                    "categoria": "pct_inconsistente",
                    "motivo":    f"% participación declarado ({pct_declarado:.1f}%) ≠ calculado ({pct_real:.1f}%)",
                    "severidad": "alta" if diff_pct > 5 else "media",
                    "detalle":   {
                        "declarado":  float(pct_declarado),
                        "calculado":  round(pct_real, 3),
                        "diferencia": round(diff_pct, 3),
                    },
                })

    # Anomalía 5 (z-score): mesas con participación estadísticamente outlier
    if len(pcts_participacion) >= 10:
        media_pct = statistics.mean(pcts_participacion)
        std_pct   = statistics.stdev(pcts_participacion) if len(pcts_participacion) > 1 else 0
        if std_pct > 0.01:
            for md in mesas_detalle:
                z = abs(md["pct"] - media_pct) / std_pct
                if z > 2.5:
                    anomalias.append({
                        "mesa":      md["mesa"],
                        "tipo":      md["tipo"],
                        "categoria": "outlier_estadistico",
                        "motivo":    (
                            f"Participación {md['pct']:.1f}% muy {'alta' if md['pct'] > media_pct else 'baja'} "
                            f"(media={media_pct:.1f}%, z={z:.2f}σ)"
                        ),
                        "severidad": "alta" if z > 4 else "media",
                        "detalle":   {
                            "pct":       md["pct"],
                            "media_pct": round(media_pct, 2),
                            "std_pct":   round(std_pct, 2),
                            "z_score":   round(z, 2),
                        },
                    })
    else:
        media_pct = None
        std_pct   = None

    # Comparación global: suma local vs oficial ONPE
    desfase_votos     = None
    desfase_pct       = None
    cobertura_pct     = None
    if total_asistentes_onpe > 0:
        desfase_votos = suma_emitidos_local - total_asistentes_onpe
        desfase_pct   = round(desfase_votos / total_asistentes_onpe * 100, 3)
    if total_electores_onpe > 0 and suma_electores_local > 0:
        cobertura_pct = round(suma_electores_local / total_electores_onpe * 100, 2)

    # Histograma de participación por rango
    hist = {}
    for p in pcts_participacion:
        b = int(p // 5) * 5
        hist[b] = hist.get(b, 0) + 1
    hist_lista = sorted(
        [{"desde": k, "hasta": k + 5, "count": v} for k, v in hist.items()],
        key=lambda x: x["desde"]
    )

    return {
        "oficial": {
            "total_electores":    total_electores_onpe,
            "total_asistentes":   total_asistentes_onpe,
            "total_ausentes":     total_ausentes_onpe,
            "pendientes":         max(0, pendientes_onpe),
            "pct_asistentes":     pct_asistentes_onpe,
            "pct_ausentes":       pct_ausentes_onpe,
            "pct_pendientes":     round(pendientes_onpe / total_electores_onpe * 100, 3) if total_electores_onpe else 0,
            "ubigeos":            participacion_cache.get("ubigeos_json", []) if participacion_cache else [],
            "fecha_captura":      participacion_cache.get("fecha_captura") if participacion_cache else None,
        },
        "local": {
            "mesas_analizadas":   mesas_analizadas,
            "suma_emitidos":      suma_emitidos_local,
            "suma_electores":     suma_electores_local,
            "media_pct":          round(media_pct, 2) if media_pct else None,
            "std_pct":            round(std_pct, 2) if std_pct else None,
            "cobertura_pct":      cobertura_pct,
        },
        "comparacion": {
            "desfase_votos":       desfase_votos,
            "desfase_pct":         desfase_pct,
            "tiene_datos_oficiales": total_electores_onpe > 0,
            "alerta_desfase":      abs(desfase_pct or 0) > UMBRAL_DIFERENCIA_PCT,
        },
        "histograma_participacion": hist_lista,
        "anomalias": sorted(anomalias, key=lambda x: (x["severidad"] == "alta"), reverse=True),
        "total_anomalias": len(anomalias),
        "mesas_detalle":   sorted(
            mesas_detalle, key=lambda x: x.get("pct", 0), reverse=True
        )[:100],
    }
