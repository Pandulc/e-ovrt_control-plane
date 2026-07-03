"""Descarga y cache de pesos YOLO/YOLOE para backends de percepcion."""

from __future__ import annotations

import logging
import shutil
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# Pesos publicos construction-PPE (Person / Hardhat / Safety Vest / NO-*).
DEFAULT_YOLO_PPE_URL = (
    "https://github.com/ftnabil97/Construction-Site-Safety-Gears-Detection-Model-Yolov8/"
    "raw/main/models/best.pt"
)
DEFAULT_WEIGHTS_FILENAME = "construction-site-safety.pt"
LEGACY_WEIGHTS_ALIASES = frozenset({"construction-ppe.pt", DEFAULT_WEIGHTS_FILENAME})

# YOLOE 26s (Ultralytics).
DEFAULT_YOLOE_URL = (
    "https://github.com/ultralytics/assets/releases/download/v8.4.0/yoloe-26s-seg.pt"
)
DEFAULT_YOLOE_FILENAME = "yoloe-26s-seg.pt"
DEFAULT_YOLOE_MODEL_ID = "yoloe/yoloe-26s"
LEGACY_YOLOE_ALIASES = frozenset({DEFAULT_YOLOE_FILENAME, "yoloe-26s.pt"})


def perception_project_root() -> Path:
    """Raiz del repo e-ovrt_control-plane."""
    return Path(__file__).resolve().parents[3]


def sibling_media_plane_yoloe_weights() -> Path | None:
    candidate = perception_project_root().parent / "e-ovrt_media-plane" / "models" / "yoloe" / "original" / DEFAULT_YOLOE_FILENAME
    if candidate.is_file() and candidate.stat().st_size > 1_000_000:
        return candidate
    return None


def default_yolo_weights_path() -> Path:
    return perception_project_root() / "models" / "yolo" / DEFAULT_WEIGHTS_FILENAME


def default_yoloe_weights_path() -> Path:
    return perception_project_root() / "models" / "yoloe" / DEFAULT_YOLOE_FILENAME


def ensure_yolo_ppe_weights(model_id: str | None = None) -> Path:
    """Resuelve pesos locales o los descarga al cache del proyecto."""
    if model_id:
        candidate = Path(model_id).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        if model_id not in LEGACY_WEIGHTS_ALIASES:
            raise FileNotFoundError(
                f"No se encontro el archivo de pesos: {model_id}. "
                f"Omiti --model-id para usar la descarga automatica."
            )

    cache_path = default_yolo_weights_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.is_file() and cache_path.stat().st_size > 1_000_000:
        return cache_path

    logger.info("Descargando pesos YOLO construction-site-safety...")
    logger.info("URL: %s", DEFAULT_YOLO_PPE_URL)
    try:
        urllib.request.urlretrieve(DEFAULT_YOLO_PPE_URL, cache_path)
    except Exception as exc:
        if cache_path.exists():
            cache_path.unlink(missing_ok=True)
        raise RuntimeError(
            "No se pudieron descargar los pesos YOLO. "
            "Proba --backend gdino o pasa --model-id con un .pt local."
        ) from exc

    if not cache_path.is_file() or cache_path.stat().st_size < 1_000_000:
        cache_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"La descarga de pesos parece invalida (<1MB): {cache_path}"
        )

    logger.info("Pesos listos en %s", cache_path)
    return cache_path


def ensure_yoloe_weights(model_id: str | None = None) -> Path:
    """Resuelve pesos YOLOE locales, copia del media-plane o descarga."""
    if model_id:
        candidate = Path(model_id).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        if model_id not in LEGACY_YOLOE_ALIASES and model_id != DEFAULT_YOLOE_MODEL_ID:
            raise FileNotFoundError(
                f"No se encontro el archivo de pesos YOLOE: {model_id}. "
                f"Omiti --model-id para usar la descarga automatica."
            )

    cache_path = default_yoloe_weights_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.is_file() and cache_path.stat().st_size > 1_000_000:
        return cache_path

    sibling = sibling_media_plane_yoloe_weights()
    if sibling is not None:
        logger.info("Copiando pesos YOLOE desde media-plane: %s", sibling)
        shutil.copy2(sibling, cache_path)
        return cache_path

    logger.info("Descargando pesos YOLOE 26s...")
    logger.info("URL: %s", DEFAULT_YOLOE_URL)
    try:
        urllib.request.urlretrieve(DEFAULT_YOLOE_URL, cache_path)
    except Exception as exc:
        if cache_path.exists():
            cache_path.unlink(missing_ok=True)
        raise RuntimeError(
            "No se pudieron descargar los pesos YOLOE. "
            "Copia yoloe-26s-seg.pt al cache o usa --model-id con un .pt local."
        ) from exc

    if not cache_path.is_file() or cache_path.stat().st_size < 1_000_000:
        cache_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"La descarga de pesos YOLOE parece invalida (<1MB): {cache_path}"
        )

    logger.info("Pesos YOLOE listos en %s", cache_path)
    return cache_path
