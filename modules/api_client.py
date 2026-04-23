"""
Cliente para la API pública de ONPE - resultadoelectoral.onpe.gob.pe
Descubre y consume todos los endpoints del portal de presentación de resultados.
"""

import time
import logging
import requests
from typing import Optional, Dict, List, Any

from config import ENDPOINTS, REQUEST_HEADERS, REQUEST_TIMEOUT, ONPE_BASE

logger = logging.getLogger(__name__)

# Tipo de archivo: 1 = Acta de Escrutinio (el PDF del acta que queremos)


class ONPEApiClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(REQUEST_HEADERS)
        self._proceso_activo: Optional[Dict] = None

    # ── Utilidades ──────────────────────────────────────────────────────────

    def _get(self, url: str, params: dict = None, timeout: int = REQUEST_TIMEOUT) -> Any:
        """GET con reintentos y manejo de errores."""
        for intento in range(3):
            try:
                r = self.session.get(url, params=params, timeout=timeout)
                r.raise_for_status()
                data = r.json()
                if data.get("success") is False:
                    raise ValueError(f"API error: {data.get('message')}")
                return data.get("data", data)
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout en {url}, intento {intento + 1}/3")
                if intento == 2:
                    raise
                time.sleep(2 ** intento)
            except requests.exceptions.HTTPError as e:
                logger.error(f"HTTP {e.response.status_code} en {url}")
                raise
        return None

    def _get_archivo_id(self, detalle: Dict, tipo: int = 1) -> Optional[str]:
        """
        Extrae el MongoDB ObjectId del campo `archivos` del detalle de un acta.
        tipo=1 → Acta de Escrutinio (PDF de votos)
        tipo=2 → Acta de Instalación y Sufragio
        """
        archivos = detalle.get("archivos") or []
        for archivo in archivos:
            if archivo.get("tipo") == tipo:
                return archivo.get("id")
        # Fallback: cualquier archivo disponible
        if archivos:
            return archivos[0].get("id")
        return None

    # ── Proceso electoral ────────────────────────────────────────────────────

    def get_proceso_activo(self) -> Dict:
        """Retorna el proceso electoral activo."""
        if self._proceso_activo is None:
            self._proceso_activo = self._get(ENDPOINTS["proceso_activo"])
        return self._proceso_activo

    def get_elecciones(self, id_proceso: int = 2) -> List[Dict]:
        """Lista las elecciones de un proceso."""
        url = ENDPOINTS["elecciones"].format(idProceso=id_proceso)
        return self._get(url)

    # ── Búsqueda de actas ────────────────────────────────────────────────────

    def buscar_por_mesa(self, codigo_mesa: str) -> List[Dict]:
        """
        Busca todas las actas asociadas a un número de mesa.
        Retorna lista con: id, codigoMesa, idEleccion, estadoActa,
        descripcionEstadoActa, detalle de votos, etc.
        """
        codigo_mesa = codigo_mesa.strip().zfill(6)
        data = self._get(ENDPOINTS["buscar_mesa"], params={"codigoMesa": codigo_mesa})
        return data if isinstance(data, list) else []

    def buscar_por_dni(self, dni: str) -> List[Dict]:
        """Busca actas asociadas a un DNI de personero/miembro de mesa."""
        data = self._get(ENDPOINTS["buscar_dni"], params={"numeroDni": dni.strip()})
        return data if isinstance(data, list) else []

    # ── Detalle del acta ─────────────────────────────────────────────────────

    def get_acta_detalle(self, acta_id: int) -> Dict:
        """
        Obtiene el detalle completo de un acta por su ID numérico compuesto.
        El ID sigue el patrón: int(codigoMesa + idUbigeo.zfill(6) + idEleccion.zfill(2))
        """
        url = ENDPOINTS["acta_detalle"].format(id=acta_id)
        return self._get(url)

    def get_acta_file_url(self, acta_id: int, detalle: Dict = None) -> Optional[str]:
        """
        Obtiene la presigned URL de S3 para descargar el PDF del acta.

        Extrae el MongoDB ObjectId del campo `archivos` del detalle,
        luego llama a /actas/file?id={mongoId}.
        Si no se pasa detalle, lo descarga automáticamente.
        Retorna None si no hay archivos o si el endpoint falla.
        """
        if detalle is None:
            detalle = self.get_acta_detalle(acta_id)

        mongo_id = self._get_archivo_id(detalle, tipo=1)
        if not mongo_id:
            logger.warning(f"Sin archivos para acta {acta_id} — sin PDF disponible")
            return None

        try:
            raw = self._get(ENDPOINTS["acta_file"], params={"id": mongo_id})
            # La respuesta puede ser: string URL directa, o dict/data con la URL
            if isinstance(raw, str) and raw.startswith("http"):
                logger.info(f"Presigned URL obtenida para acta {acta_id} (mongo={mongo_id})")
                return raw
            if isinstance(raw, dict):
                for key in ("url", "presignedUrl", "fileUrl", "data"):
                    v = raw.get(key)
                    if isinstance(v, str) and v.startswith("http"):
                        logger.info(f"Presigned URL obtenida para acta {acta_id} (mongo={mongo_id})")
                        return v
            logger.warning(f"Respuesta inesperada de /actas/file para {acta_id}: {type(raw)} = {str(raw)[:200]}")
        except Exception as e:
            logger.error(f"Error obteniendo file URL para {acta_id}: {e}")

        return None

    def get_acta_detalle_completo(self, acta_id: int) -> Dict:
        """
        Obtiene el detalle completo del acta incluyendo campo `archivos`.
        Combina la respuesta de /actas/{id} que incluye: votos por partido,
        candidatos, totales, ubicación y archivos PDF (MongoDB IDs).
        """
        detalle = self.get_acta_detalle(acta_id)
        return detalle if isinstance(detalle, dict) else {}

    def get_session(self) -> requests.Session:
        """Expone la sesión HTTP para poder usarla en descargas."""
        return self.session

    # ── Datos resumen ────────────────────────────────────────────────────────

    def get_mesa_totales(self) -> Dict:
        """Retorna los totales de mesas por tipo de elección."""
        return self._get(ENDPOINTS["mesa_totales"], params={"tipoFiltro": "eleccion"})

    # ── Agregador de mesa ─────────────────────────────────────────────────────

    def get_mesa_completa(self, codigo_mesa: str) -> Dict:
        """
        Descarga todos los datos de una mesa:
        - Lista de actas (buscar_por_mesa)
        - Detalle de cada acta
        - File URL (presigned S3) de cada acta
        Retorna un dict estructurado listo para guardar en DB.
        """
        codigo_mesa = codigo_mesa.strip().zfill(6)
        logger.info(f"Obteniendo mesa completa: {codigo_mesa}")

        actas_lista = self.buscar_por_mesa(codigo_mesa)
        if not actas_lista:
            logger.warning(f"Mesa {codigo_mesa} no encontrada o sin actas")
            return {"codigo_mesa": codigo_mesa, "actas": [], "error": "Mesa no encontrada"}

        resultado = {
            "codigo_mesa": codigo_mesa,
            "actas": [],
        }

        for acta_resumen in actas_lista:
            acta_id = acta_resumen.get("id")
            id_eleccion = acta_resumen.get("idEleccion")
            estado = acta_resumen.get("descripcionEstadoActa", "")

            try:
                detalle = self.get_acta_detalle(acta_id)
                file_url = self.get_acta_file_url(acta_id, detalle=detalle)
            except Exception as e:
                logger.error(f"Error en detalle/file de acta {acta_id}: {e}")
                detalle = acta_resumen
                file_url = None

            resultado["actas"].append({
                "id":            acta_id,
                "idEleccion":    id_eleccion,
                "estado":        estado,
                "codigoEstado":  acta_resumen.get("codigoEstadoActa", ""),
                "resumen":       acta_resumen,
                "detalle":       detalle,
                "file_url":      file_url,
            })

        return resultado

    def close(self):
        self.session.close()
