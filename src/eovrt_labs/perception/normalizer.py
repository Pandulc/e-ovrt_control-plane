"""Normalizacion de detecciones crudas al contrato media.detection.v1."""

from __future__ import annotations

from dataclasses import dataclass

from eovrt_control.contracts.media import Detection
from eovrt_labs.perception.events import RawModelDetection
from eovrt_labs.perception.labels import CANONICAL_LABELS

DEFAULT_CLASS_CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "person": 0.35,
    "helmet": 0.25,
    "vest": 0.25,
}
DEFAULT_NMS_IOU_THRESHOLDS: dict[str, float] = {
    "person": 0.65,
    "helmet": 0.50,
    "vest": 0.50,
}


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


@dataclass(frozen=True)
class _Candidate:
    index: int
    detection: RawModelDetection
    box_xyxy: list[float]
    area_px: float


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _clip_box(box_xyxy: list[float], *, width: int, height: int) -> list[float]:
    if width <= 0 or height <= 0:
        return list(box_xyxy)
    x1, y1, x2, y2 = box_xyxy
    return [
        _clamp(x1, 0.0, float(width)),
        _clamp(y1, 0.0, float(height)),
        _clamp(x2, 0.0, float(width)),
        _clamp(y2, 0.0, float(height)),
    ]


def _area(box_xyxy: list[float]) -> float:
    x1, y1, x2, y2 = box_xyxy
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _bbox_iou(box_a: list[float], box_b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_area = max(0.0, inter_x2 - inter_x1) * max(0.0, inter_y2 - inter_y1)
    if inter_area <= 0.0:
        return 0.0
    area_a = _area(box_a)
    area_b = _area(box_b)
    union = area_a + area_b - inter_area
    if union <= 0.0:
        return 0.0
    return inter_area / union


def _confidence_threshold(
    label: str,
    min_confidence: float,
    class_confidence_thresholds: dict[str, float] | None,
) -> float:
    if class_confidence_thresholds is None:
        return min_confidence
    return class_confidence_thresholds.get(label, min_confidence)


def _nms(candidates: list[_Candidate], thresholds: dict[str, float]) -> list[_Candidate]:
    kept: list[_Candidate] = []
    for label in sorted({candidate.detection.label for candidate in candidates}):
        label_candidates = [
            candidate for candidate in candidates if candidate.detection.label == label
        ]
        threshold = thresholds.get(label, 1.0)
        selected: list[_Candidate] = []
        for candidate in sorted(
            label_candidates,
            key=lambda item: (-item.detection.confidence, item.index),
        ):
            if all(_bbox_iou(candidate.box_xyxy, item.box_xyxy) <= threshold for item in selected):
                selected.append(candidate)
        kept.extend(selected)
    return sorted(kept, key=lambda item: item.index)


def postprocess_raw_detections(
    raw: list[RawModelDetection],
    *,
    width: int,
    height: int,
    min_confidence: float,
    min_box_area_px: float = 100.0,
    transform: LetterboxTransform | None = None,
    class_confidence_thresholds: dict[str, float] | None = None,
    nms_iou_thresholds: dict[str, float] | None = None,
) -> list[RawModelDetection]:
    """Filtra, recorta y deduplica detecciones crudas manteniendo el contrato."""

    candidates: list[_Candidate] = []
    for index, item in enumerate(raw):
        if item.label not in CANONICAL_LABELS:
            continue

        threshold = _confidence_threshold(
            item.label,
            min_confidence,
            class_confidence_thresholds,
        )
        if item.confidence < threshold:
            continue

        if transform is not None:
            box_xyxy = transform.project_to_original(item.bbox_xyxy)
        else:
            box_xyxy = list(item.bbox_xyxy)

        box_xyxy = _clip_box(box_xyxy, width=width, height=height)
        area_px = _area(box_xyxy)
        if area_px <= 0.0 or area_px < min_box_area_px:
            continue

        candidates.append(
            _Candidate(
                index=index,
                detection=item,
                box_xyxy=box_xyxy,
                area_px=area_px,
            )
        )

    thresholds = nms_iou_thresholds or DEFAULT_NMS_IOU_THRESHOLDS
    return [
        RawModelDetection(
            label=candidate.detection.label,
            confidence=candidate.detection.confidence,
            bbox_xyxy=candidate.box_xyxy,
            detection_id=candidate.detection.detection_id,
        )
        for candidate in _nms(candidates, thresholds)
    ]


def normalize_detections(
    raw: list[RawModelDetection],
    *,
    width: int,
    height: int,
    model_name: str,
    min_confidence: float,
    min_box_area_px: float = 100.0,
    transform: LetterboxTransform | None = None,
    class_confidence_thresholds: dict[str, float] | None = None,
    nms_iou_thresholds: dict[str, float] | None = None,
    apply_postprocessing: bool = True,
) -> list[Detection]:
    """Filtra y normaliza detecciones al contrato de salida."""

    if apply_postprocessing:
        raw = postprocess_raw_detections(
            raw,
            width=width,
            height=height,
            min_confidence=min_confidence,
            min_box_area_px=min_box_area_px,
            transform=transform,
            class_confidence_thresholds=class_confidence_thresholds,
            nms_iou_thresholds=nms_iou_thresholds,
        )

    normalized: list[Detection] = []
    det_index = 0

    for item in raw:
        if item.label not in CANONICAL_LABELS:
            continue
        box_xyxy = _clip_box(list(item.bbox_xyxy), width=width, height=height)
        area_px = _area(box_xyxy)
        if area_px <= 0.0:
            continue

        if width > 0 and height > 0:
            x1, y1, x2, y2 = box_xyxy
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
                area_px=round(area_px, 1),
                model_name=model_name,
            )
        )

    return normalized
