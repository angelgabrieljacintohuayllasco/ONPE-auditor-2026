"""
Descargador de PDFs de actas con reintentos, verificación de hash y caché local.
"""

import os
import hashlib
import logging
import requests
import time
from pathlib import Path
from typing import Optional

from config import ACTAS_DIR, DOWNLOAD_TIMEOUT, REQUEST_HEADERS

logger = logging.getLogger(__name__)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def get_local_path(codigo_mesa: str, id_eleccion: int, extension: str = "pdf", base_dir: str = None) -> str:
    """Genera la ruta local para guardar el archivo de un acta.
    
    Si se pasa base_dir, usa ese directorio raíz en lugar de ACTAS_DIR.
    """
    root = base_dir if base_dir else ACTAS_DIR
    mesa_dir = os.path.join(root, codigo_mesa)
    os.makedirs(mesa_dir, exist_ok=True)
    return os.path.join(mesa_dir, f"acta_{codigo_mesa}_e{id_eleccion}.{extension}")


def download_pdf(
    url: str,
    dest_path: str,
    force: bool = False,
    timeout: int = DOWNLOAD_TIMEOUT,
    session: requests.Session = None,
) -> Optional[str]:
    """
    Descarga un PDF desde una URL (presigned S3 u otra).
    - Si el archivo ya existe y no se fuerza la descarga, lo devuelve directamente.
    - Acepta un requests.Session opcional para reutilizar cookies/headers de ONPE.
    - Retorna la ruta local del archivo, o None si falla.
    """
    if not force and os.path.exists(dest_path) and os.path.getsize(dest_path) > 1024:
        logger.info(f"Cache: {dest_path}")
        return dest_path

    if not url:
        logger.warning(f"URL vacía para {dest_path}")
        return None

    logger.info(f"URL completa: {url[:250]}")

    # Las presigned URLs de S3 de ONPE solo funcionan sin headers adicionales.
    # requests añade User-Agent, Accept-Encoding, Accept, Connection, etc. que
    # no estaban en la firma → S3 responde 403 SignatureDoesNotMatch.
    # urllib con headers mínimos es la estrategia que SIEMPRE funciona.
    # Intentamos primero con urllib; si falla, probamos requests sin headers como fallback.
    import urllib.request

    try:
        logger.info(f"Descargando PDF [urllib]: {os.path.basename(dest_path)}")
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if data[:5] == b"%PDF-":
            with open(dest_path, "wb") as f:
                f.write(data)
            size_kb = len(data) / 1024
            logger.info(f"PDF OK [urllib] {size_kb:.0f}KB → {dest_path}")
            return dest_path
        else:
            logger.warning(f"urllib: respuesta no es PDF (magic: {data[:10]}), probando requests…")
    except Exception as e:
        logger.warning(f"urllib falló ({e}), probando requests sin headers…")

    # Fallback: requests sin headers extra (solo en caso de que urllib falle)
    strategies = [
        ("limpia", None, {}),
        ("origin_onpe", None, {
            "Origin": "https://resultadoelectoral.onpe.gob.pe",
            "Referer": "https://resultadoelectoral.onpe.gob.pe/",
        }),
    ]
    if session is not None:
        strategies.insert(0, ("sesion_onpe", session, None))

    last_403_body = ""

    for name, sess, hdrs in strategies:
        try:
            logger.info(f"Descargando PDF [estrategia: {name}]: {os.path.basename(dest_path)}")
            if sess is not None:
                # Para S3, no queremos los headers de la API ONPE (content-type: json etc.)
                # Hacer petición separada con la sesión pero sin headers conflictivos
                r = requests.get(url, cookies=sess.cookies, timeout=timeout, stream=True,
                                 headers={
                                     "User-Agent": REQUEST_HEADERS["User-Agent"],
                                     "Origin": "https://resultadoelectoral.onpe.gob.pe",
                                     "Referer": "https://resultadoelectoral.onpe.gob.pe/",
                                 })
            else:
                r = requests.get(url, headers=hdrs or {}, timeout=timeout, stream=True)

            if r.status_code == 403:
                last_403_body = r.text[:500] if r.text else "(vacío)"
                logger.warning(f"403 en [{name}]. Respuesta S3: {last_403_body[:200]}")
                continue

            r.raise_for_status()

            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)

            # Verificar que es un PDF válido
            with open(dest_path, "rb") as f:
                magic = f.read(5)
            if magic != b"%PDF-":
                os.remove(dest_path)
                logger.error(f"No es PDF válido (magic: {magic}): {dest_path}")
                return None

            sha = _sha256_file(dest_path)
            size_kb = os.path.getsize(dest_path) / 1024
            logger.info(f"PDF OK [{name}] {size_kb:.0f}KB SHA256:{sha[:16]}… → {dest_path}")
            return dest_path

        except requests.exceptions.HTTPError as e:
            logger.warning(f"HTTP {e.response.status_code} en [{name}]")
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout en [{name}]")
        except Exception as e:
            logger.error(f"Error en [{name}]: {e}")

    # Si el fallback de requests también falló
    logger.error(f"TODAS las estrategias fallaron. Último 403 body:\n{last_403_body}")
    return None


def pdf_info(path: str) -> dict:
    """Retorna información básica de un PDF local."""
    if not os.path.exists(path):
        return {"existe": False}
    stat = os.stat(path)
    sha = _sha256_file(path)
    return {
        "existe": True,
        "path": path,
        "tamanio_bytes": stat.st_size,
        "tamanio_kb": round(stat.st_size / 1024, 1),
        "sha256": sha,
        "nombre": os.path.basename(path),
    }
