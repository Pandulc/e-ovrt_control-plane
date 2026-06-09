"""Evaluador de ausencia espacial de EPP asociado a una persona."""

from __future__ import annotations

from dataclasses import dataclass

from eovrt_control.config import PatternDefinition
from eovrt_control.contracts.media import Detection, DetectionEvent
from eovrt_control.contracts.pattern import EvidenceRef, PatternEvidence


LABEL_ALIASES: dict[str, set[str]] = {
    "person": {"person", "worker", "human", "people"},
    "helmet": {"helmet", "hardhat", "hard_hat", "safety helmet", "protective helmet"},
    "vest": {
        "vest",
        "reflective vest",
        "safety vest",
        "high visibility vest",
        "high-visibility vest",
    },
}


@dataclass(frozen=True)
class PatternEvaluationResult:
    evidences: list[PatternEvidence]
    observed_subject_keys: set[str]


def _normalize_label(value: str | None) -> str:
    return (value or "").strip().lower().replace("_", " ")


def _matches_detection(detection: Detection, target_class: str) -> bool:
    target = _normalize_label(target_class)
    aliases = LABEL_ALIASES.get(target, {target})
    label = _normalize_label(detection.label)
    prompt_id = _normalize_label(detection.prompt_id)
    return label in aliases or prompt_id in aliases


def _bbox_center(bbox_xyxy: list[float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox_xyxy
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _region_bbox(subject_bbox: list[float], pattern: PatternDefinition) -> list[float]:
    x1, y1, x2, y2 = subject_bbox
    width = max(0.0, x2 - x1)
    height = max(0.0, y2 - y1)
    margin_x = width * pattern.region.x_margin_ratio
    return [
        x1 + margin_x,
        y1 + height * pattern.region.y_min_ratio,
        x2 - margin_x,
        y1 + height * pattern.region.y_max_ratio,
    ]


def _center_inside_region(detection: Detection, region: list[float]) -> bool:
    cx, cy = _bbox_center(detection.bbox_xyxy)
    x1, y1, x2, y2 = region
    return x1 <= cx <= x2 and y1 <= cy <= y2


def _evidence_ref(detection: Detection) -> EvidenceRef:
    return EvidenceRef(
        detection_id=detection.detection_id,
        label=detection.label,
        confidence=detection.confidence,
        bbox_xyxy=detection.bbox_xyxy,
    )


def _subject_key(event: DetectionEvent, pattern: PatternDefinition, index: int, subject: Detection) -> str:
    stable_id = subject.detection_id or f"{event.unit_id}:person:{index}"
    return f"{pattern.id}:{event.source.source_id}:{stable_id}"


def evaluate_spatial_absence(
    event: DetectionEvent,
    pattern: PatternDefinition,
) -> PatternEvaluationResult:
    """Evalua si cada persona carece de una clase EPP asociada espacialmente."""

    subjects = [
        detection
        for detection in event.detections
        if _matches_detection(detection, pattern.subject_class)
        and detection.confidence >= pattern.evidence.min_subject_confidence
        and (detection.area_px or 0.0) >= pattern.evidence.min_subject_area_px
    ]
    required_items = [
        detection
        for detection in event.detections
        if _matches_detection(detection, pattern.required_absent_class)
        and detection.confidence >= pattern.evidence.min_absent_class_confidence
    ]

    evidences: list[PatternEvidence] = []
    observed_subject_keys: set[str] = set()

    for index, subject in enumerate(subjects):
        subject_key = _subject_key(event, pattern, index, subject)
        observed_subject_keys.add(subject_key)
        region = _region_bbox(subject.bbox_xyxy, pattern)
        associated_items = [
            item for item in required_items if _center_inside_region(item, region)
        ]
        if associated_items:
            continue

        evidences.append(
            PatternEvidence(
                pattern_id=pattern.id,
                condition_id=pattern.condition_id,
                subject_key=subject_key,
                subject=_evidence_ref(subject),
                missing_class=pattern.required_absent_class,
                supporting=[],
                score=subject.confidence,
                rationale=(
                    f"No se encontro evidencia '{pattern.required_absent_class}' "
                    f"en region '{pattern.region.type}' del sujeto."
                ),
            )
        )

    return PatternEvaluationResult(
        evidences=evidences,
        observed_subject_keys=observed_subject_keys,
    )

