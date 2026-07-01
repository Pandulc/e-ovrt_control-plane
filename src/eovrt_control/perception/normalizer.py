"""Normalizacion de detecciones crudas al contrato media.detection.v1."""

from __future__ import annotations

from dataclasses import dataclass

from eovrt_control.contracts.media import Detection
from eovrt_control.perception.events import RawModelDetection
from eovrt_control.perception.labels import CANONICAL_LABELS


@dataclass(frozen=True)
class LetterboxTransform:
    """Parametros de letterbox para reproyectar cajas al espacio original."""

    scale_x: float
    scale_y: float
    pad_x: float
    pad_y: float

    def project_to_original(self, box_xyxy: list[float]) -> list[float]:
        x1, y1, x2, y2 = box_xyxy
        return [
            (x1 - self.pad_x) / self.scale_x,
            (y1 - self.pad_y) / self.scale_y,
            (x2 - self.pad_x) / self.scale_x,
            (y2 - self.pad_y) / self.scale_y,
        ]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def normalize_detections(
    raw: list[RawModelDetection],
    *,
    width: int,
    height: int,
    model_name: str,
    min_confidence: float,
    min_box_area_px: float = 100.0,
    transform: LetterboxTransform | None = None,
) -> list[Detection]:
    """Filtra y normaliza detecciones al contrato de salida."""
    normalized: list[Detection] = []
    det_index = 0

    for item in raw:
        if item.confidence < min_confidence:
            continue
        if item.label not in CANONICAL_LABELS:
            continue

        if transform is not None:
            box_xyxy = transform.project_to_original(item.bbox_xyxy)
        else:
            box_xyxy = list(item.bbox_xyxy)

        x1, y1, x2, y2 = box_xyxy
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        if area < min_box_area_px:
            continue

        if width > 0 and height > 0:
            bbox_norm = [
                round(_clamp01(x1 / width), 4),
                round(_clamp01(y1 / height), 4),
                round(_clamp01(x2 / width), 4),
                round(_clamp01(y2 / height), 4),
            ]
        else:
            bbox_norm = []

        det_index += 1
        detection_id = item.detection_id or f"det_{det_index:06d}"
        normalized.append(
            Detection(
                detection_id=detection_id,
                label=item.label,
                prompt_id=item.label,
                confidence=round(item.confidence, 4),
                bbox_xyxy=[round(v, 1) for v in box_xyxy],
                bbox_norm_xyxy=bbox_norm,
                area_px=round(area, 1),
                model_name=model_name,
            )
        )

    return normalized
