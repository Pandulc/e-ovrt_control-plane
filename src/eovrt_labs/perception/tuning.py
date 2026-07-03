"""Parametros finos de deteccion y tracking del generador (via YAML `--tuning`).

Aislado de `generator.py` para poder cargarse/testearse sin dependencias pesadas
(torch/opencv). Los defaults reproducen el comportamiento historico del generador.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class TuningConfig:
    person_confidence: float = 0.35
    helmet_confidence: float = 0.25
    vest_confidence: float = 0.25
    min_box_area_px: float = 100.0
    nms_iou_person: float = 0.65
    nms_iou_epp: float = 0.50
    model_iou: float = 0.50
    image_size: int = 640
    image_folder_frame_interval_ms: float = 500.0
    track_iou_threshold: float = 0.20
    track_max_lost_ms: float = 1500.0
    track_max_lost_frames: int = 30
    track_center_gate_ratio: float = 0.75
    track_area_ratio_min: float = 0.35
    track_min_score: float = 0.25
    track_appearance: bool = True
    track_appearance_weight: float = 0.45
    track_appearance_min_similarity: float = 0.30


_TUNING_FIELDS = {tuning_field.name for tuning_field in fields(TuningConfig)}


def load_tuning_config(path: Path | str | None) -> TuningConfig:
    """Carga parametros de tuning desde un YAML; defaults sensatos si no hay archivo."""
    if path is None:
        return TuningConfig()

    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"El YAML de tuning debe ser un objeto: {path}")
    unknown = set(data) - _TUNING_FIELDS
    if unknown:
        raise ValueError(
            f"Claves de tuning no reconocidas en {path}: {sorted(unknown)}. "
            f"Validas: {sorted(_TUNING_FIELDS)}"
        )
    return TuningConfig(**data)
