"""
Detección de anomalías electorales con One-Class SVM (scikit-learn).

El modelo se entrena con los vectores de características de TODAS las actas
procesadas. Una acta se considera "anómala" si su score cae fuera de la
frontera de soporte del conjunto de entrenamiento.

Features (vectoriales) por acta:
  - votos_totales_partido:   suma de todos los votos a partidos
  - votos_blanco
  - votos_nulos
  - votos_impugnados
  - total_emitidos
  - electores_habiles
  - participacion:           total_votantes / electores_habiles
  - ratio_nulos:             nulos / total_emitidos
  - ratio_blanco:            blanco / total_emitidos
  - discrepancia_ocr_api:    0/1/nan
  - es_tipo_d:               0/1 (PDF generado artificialmente según forense)
  - scan_score:              0-100 del módulo de forensics
  - artificial_score:        0-100 del módulo de forensics
  - num_alertas_forense:     entero
"""

import json
import logging
import pickle
import os
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    from sklearn.svm import OneClassSVM
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline
    SKLEARN_OK = True
except ImportError:
    SKLEARN_OK = False
    logger.warning("scikit-learn no disponible. Detección de anomalías desactivada.")

# Ruta para persistir el modelo entrenado
from config import DATA_DIR
MODEL_PATH = os.path.join(DATA_DIR, "ocsvm_model.pkl")

# ── Features ─────────────────────────────────────────────────────────────────

FEATURE_NAMES = [
    "votos_partidos",       # suma de votos a todos los partidos
    "votos_blanco",
    "votos_nulos",
    "votos_impugnados",
    "total_emitidos",
    "electores_habiles",
    "participacion",        # total_votantes / electores_habiles (0-1)
    "ratio_nulos",          # nulos / total_emitidos
    "ratio_blanco",         # blanco / total_emitidos
    "discrepancia_ocr",     # 0=ok, 1=discrepancia, 0.5=sin OCR
    "es_tipo_d",            # 1=PDF generado artificialmente
    "scan_score",           # 0-100
    "artificial_score",     # 0-100
    "num_alertas_forense",  # 0..N
]

N_FEATURES = len(FEATURE_NAMES)


def build_feature_vector(
    api_data: dict,
    ocr_data: dict = None,
    comparacion: dict = None,
    forense: dict = None,
) -> np.ndarray:
    """
    Construye el vector de características (1D, longitud N_FEATURES).
    Los valores faltantes se imputan con la media del dominio esperado.
    """
    def _safe(d: dict, *keys, default=0.0):
        """Navega claves anidadas con fallback."""
        val = d
        for k in keys:
            if not isinstance(val, dict):
                return default
            val = val.get(k, default)
        if val is None:
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    # ── Datos API ──────────────────────────────────────────────────────────
    votos_por_partido = api_data.get("votos_por_partido") or {}
    votos_partidos = sum(
        float(v) for v in votos_por_partido.values() if v is not None
    ) if votos_por_partido else _safe(api_data, "sumaVotosPartidos", default=0.0)

    votos_blanco     = _safe(api_data, "votosBlanco",     default=0.0)
    votos_nulos      = _safe(api_data, "votosNulos",      default=0.0)
    votos_impugnados = _safe(api_data, "votosImpugnados", default=0.0)
    total_emitidos   = _safe(api_data, "totalVotosEmitidos", default=0.0)
    electores        = _safe(api_data, "electoresHabiles",   default=300.0)
    total_votantes   = _safe(api_data, "totalVotantes",      default=0.0)

    # Si total_emitidos es 0, intentar reconstruirlo
    if total_emitidos == 0:
        total_emitidos = votos_partidos + votos_blanco + votos_nulos + votos_impugnados

    participacion = total_votantes / electores if electores > 0 else 0.5
    ratio_nulos   = votos_nulos   / total_emitidos if total_emitidos > 0 else 0.0
    ratio_blanco  = votos_blanco  / total_emitidos if total_emitidos > 0 else 0.0

    # ── Discrepancia OCR ───────────────────────────────────────────────────
    if comparacion is not None:
        estado = comparacion.get("estado", "")
        discrepancia_ocr = 1.0 if estado == "alerta" else 0.0
    else:
        discrepancia_ocr = 0.5  # Sin OCR procesado

    # ── Forense ────────────────────────────────────────────────────────────
    es_tipo_d        = 0.0
    scan_score       = 50.0
    artificial_score = 0.0
    num_alertas      = 0.0

    if forense:
        # Compatibilidad con la estructura de full_forensic_analysis
        clasificacion = forense.get("clasificacion") or forense.get("reporte_json", {}).get("clasificacion", {})
        if isinstance(clasificacion, dict):
            tipo = clasificacion.get("tipo_origen", "")
            es_tipo_d = 1.0 if tipo == "D" else 0.0

        art = forense.get("artefactos_artificiales") or forense.get("reporte_json", {}).get("artefactos_artificiales", {})
        if isinstance(art, dict):
            artificial_score = float(art.get("artificial_score", 0))

        sc = forense.get("artefactos_scanner") or forense.get("reporte_json", {}).get("artefactos_scanner", {})
        if isinstance(sc, dict):
            scan_score = float(sc.get("scan_score", 50))

        alertas = forense.get("alertas") or forense.get("alertas_json", [])
        if isinstance(alertas, list):
            num_alertas = float(len([a for a in alertas if isinstance(a, dict) and a.get("nivel") == "alerta"]))

    vec = np.array([
        votos_partidos,
        votos_blanco,
        votos_nulos,
        votos_impugnados,
        total_emitidos,
        electores,
        participacion,
        ratio_nulos,
        ratio_blanco,
        discrepancia_ocr,
        es_tipo_d,
        scan_score,
        artificial_score,
        num_alertas,
    ], dtype=np.float64)

    # Reemplazar NaN/Inf por 0
    vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)
    return vec


# ── Modelo ─────────────────────────────────────────────────────────────────

class AnomalyDetector:
    """
    Wrapper de OneClassSVM con StandardScaler.
    nu: fracción esperada de outliers (0.05 = 5%).
    """

    def __init__(self, nu: float = 0.05, kernel: str = "rbf", gamma: str = "scale"):
        self.nu = nu
        self.kernel = kernel
        self.gamma = gamma
        self._pipeline: Optional[Pipeline] = None
        self._trained_at: Optional[str] = None
        self._n_samples: int = 0

    # ── Entrenamiento ──────────────────────────────────────────────────────

    def fit(self, feature_matrix: np.ndarray) -> "AnomalyDetector":
        """
        Entrena con una matriz (n_samples × N_FEATURES).
        Requiere al menos 10 muestras para ser significativo.
        """
        if not SKLEARN_OK:
            raise RuntimeError("scikit-learn no disponible")

        n = feature_matrix.shape[0]
        if n < 5:
            raise ValueError(f"Se necesitan al menos 5 actas para entrenar. Hay {n}.")

        self._pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("ocsvm",  OneClassSVM(
                kernel      = self.kernel,
                gamma       = self.gamma,
                nu          = self.nu,
                cache_size  = 500,
            )),
        ])
        self._pipeline.fit(feature_matrix)
        self._trained_at = datetime.now().isoformat()
        self._n_samples  = n
        logger.info(f"OCSVM entrenado con {n} muestras (nu={self.nu})")
        return self

    def save(self, path: str = MODEL_PATH):
        with open(path, "wb") as f:
            pickle.dump(self, f)
        logger.info(f"Modelo OCSVM guardado en {path}")

    @classmethod
    def load(cls, path: str = MODEL_PATH) -> Optional["AnomalyDetector"]:
        if not os.path.exists(path):
            return None
        try:
            with open(path, "rb") as f:
                obj = pickle.load(f)
            logger.info(f"Modelo OCSVM cargado ({obj._n_samples} muestras, {obj._trained_at})")
            return obj
        except Exception as e:
            logger.warning(f"Error cargando modelo OCSVM: {e}")
            return None

    @property
    def is_fitted(self) -> bool:
        return self._pipeline is not None

    # ── Predicción ─────────────────────────────────────────────────────────

    def predict_one(self, vec: np.ndarray) -> Dict:
        """
        Evalúa un único vector.
        Retorna:
          - is_anomaly: bool
          - score: float  (positivo = normal, negativo = anómalo)
          - score_normalized: float en [-1, 1] aprox.
          - confidence: "alta" | "media" | "baja"
          - explanation: lista de textos que explican los factores de riesgo
        """
        if not self.is_fitted:
            return {"is_anomaly": False, "score": 0.0, "explanation": ["Modelo no entrenado"]}

        X = vec.reshape(1, -1)
        raw_score   = float(self._pipeline.decision_function(X)[0])
        prediction  = int(self._pipeline.predict(X)[0])   # 1=normal, -1=anómalo
        is_anomaly  = prediction == -1

        # Normalizar score a [-1, 1] para comparación entre modelos
        # La escala real depende del kernel/nu; usamos tanh como proxy suave
        score_norm = float(np.tanh(raw_score))

        # Nivel de confianza basado en la magnitud del score
        if abs(raw_score) > 0.5:
            confidence = "alta"
        elif abs(raw_score) > 0.2:
            confidence = "media"
        else:
            confidence = "baja"

        explanation = _build_explanation(vec, is_anomaly, raw_score)

        return {
            "is_anomaly":       is_anomaly,
            "score":            raw_score,
            "score_normalized": score_norm,
            "prediction":       prediction,
            "confidence":       confidence,
            "explanation":      explanation,
        }

    def predict_batch(self, matrix: np.ndarray) -> List[Dict]:
        """Evalúa múltiples vectores. Retorna lista de predict_one()."""
        return [self.predict_one(matrix[i]) for i in range(len(matrix))]


# ── Singleton / entrenamiento automático ────────────────────────────────────

_detector_cache: Optional[AnomalyDetector] = None


def get_detector(nu: float = 0.05) -> Optional[AnomalyDetector]:
    """Retorna el detector cargado o None si no existe."""
    global _detector_cache
    if _detector_cache is None:
        _detector_cache = AnomalyDetector.load()
    return _detector_cache


def train_from_db(nu: float = 0.05) -> Dict:
    """
    Entrena el detector usando todos los datos disponibles en la DB.
    Retorna estadísticas del entrenamiento.
    """
    global _detector_cache
    from modules import database as db

    # Recopilar todos los datos disponibles
    mesas = db.get_all_mesas()
    vectors = []
    used_mesas = []

    for mesa in mesas:
        codigo = mesa["codigo_mesa"]
        actas = db.get_actas_by_mesa(codigo)
        for acta in actas:
            id_elec = acta["id_eleccion"]
            api_data = json.loads(acta.get("api_json") or "{}")
            ocr_row  = db.get_latest_ocr(codigo, id_elec)
            ocr_data = ocr_row.get("datos_json", {}) if ocr_row else {}

            # Comparación (si existe)
            comp_data = None
            try:
                from modules import database as db2
                with db2._get_conn() as conn:
                    row = conn.execute(
                        "SELECT * FROM comparisons WHERE codigo_mesa=? AND id_eleccion=? ORDER BY fecha_comparacion DESC LIMIT 1",
                        (codigo, id_elec)
                    ).fetchone()
                    if row:
                        comp_data = dict(row)
                        comp_data["estado"] = comp_data.get("estado", "")
            except Exception:
                pass

            # Forense
            forense_row = db.get_latest_forensic(codigo, id_elec)

            vec = build_feature_vector(api_data, ocr_data, comp_data, forense_row)
            vectors.append(vec)
            used_mesas.append(f"{codigo}:{id_elec}")

    if not vectors:
        return {"ok": False, "error": "Sin datos en DB para entrenar", "n_samples": 0}

    matrix = np.array(vectors, dtype=np.float64)

    detector = AnomalyDetector(nu=nu)
    try:
        detector.fit(matrix)
        detector.save()
        _detector_cache = detector
    except ValueError as e:
        return {"ok": False, "error": str(e), "n_samples": len(vectors)}

    # Estadísticas post-entrenamiento
    predictions = np.array([d["prediction"] for d in detector.predict_batch(matrix)])
    n_anomalias = int((predictions == -1).sum())

    return {
        "ok":            True,
        "n_samples":     len(vectors),
        "n_anomalias":   n_anomalias,
        "pct_anomalias": round(100 * n_anomalias / len(vectors), 1),
        "trained_at":    detector._trained_at,
        "nu":            nu,
        "mesas_usadas":  used_mesas,
    }


def analyze_acta(
    api_data: dict,
    ocr_data: dict = None,
    comparacion: dict = None,
    forense: dict = None,
) -> Dict:
    """
    Analiza UNA acta y retorna su evaluación de anomalía.
    Si el modelo no está entrenado, retorna solo las features sin predicción.
    """
    vec = build_feature_vector(api_data, ocr_data, comparacion, forense)
    detector = get_detector()

    feature_dict = {FEATURE_NAMES[i]: float(vec[i]) for i in range(N_FEATURES)}

    if detector is None or not detector.is_fitted:
        return {
            "modelo_disponible": False,
            "features":          feature_dict,
            "is_anomaly":        None,
            "score":             None,
            "explanation":       ["Modelo OCSVM no entrenado. Usa /api/anomalias/entrenar primero."],
        }

    result = detector.predict_one(vec)
    result["modelo_disponible"] = True
    result["features"] = feature_dict
    return result


# ── Explicabilidad ────────────────────────────────────────────────────────────

def _build_explanation(vec: np.ndarray, is_anomaly: bool, score: float) -> List[str]:
    """Genera explicaciones en lenguaje natural basadas en los valores del vector."""
    msgs = []
    f = {FEATURE_NAMES[i]: vec[i] for i in range(N_FEATURES)}

    participacion = f["participacion"]
    if participacion > 0.97:
        msgs.append(f"Participación muy alta ({participacion*100:.1f}%): inusual en elecciones peruanas.")
    elif participacion < 0.30:
        msgs.append(f"Participación muy baja ({participacion*100:.1f}%): inusual.")

    ratio_nulos = f["ratio_nulos"]
    if ratio_nulos > 0.20:
        msgs.append(f"Tasa de nulos alta ({ratio_nulos*100:.1f}%): supera el 20%.")

    if f["es_tipo_d"] == 1.0:
        msgs.append("Acta clasificada como PDF generado artificialmente (Tipo D). Riesgo de adulteración.")

    if f["artificial_score"] > 60:
        msgs.append(f"Score artificial alto ({f['artificial_score']:.0f}/100): posible imagen fabricada.")

    if f["discrepancia_ocr"] == 1.0:
        msgs.append("Discrepancia detectada entre datos de la API y el OCR del acta física.")

    if f["num_alertas_forense"] >= 3:
        msgs.append(f"Múltiples alertas forenses ({f['num_alertas_forense']:.0f}): revisión recomendada.")

    if not msgs:
        if is_anomaly:
            msgs.append(f"Combinación estadística inusual (score={score:.3f}). Sin factor individual dominante.")
        else:
            msgs.append("Acta dentro de los parámetros estadísticos normales.")

    return msgs
