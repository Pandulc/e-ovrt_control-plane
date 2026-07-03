"""Lectura de alertas del plano de control y export a CSV normalizado.

Este modulo produce el artefacto `alerts.csv` de cada corrida (usado por el replay)
a partir de `alerts.jsonl`/`pattern_events.jsonl`. No depende de OpenCV: el dibujado
de frames sobre video vive en `eovrt_labs.visualization.frame_drawing`.
"""

from __future__ import annotations

import ast
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STAGE_CHOICES = ("confirm", "candidate", "both", "all")

DETAIL_FIELDNAMES = [
    "variant",
    "stage",
    "alert_order",
    "alert_id",
    "pattern_id",
    "condition_id",
    "risk_condition",
    "severity",
    "subject_id",
    "subject_label",
    "frame_index",
    "timestamp_ms",
    "second",
    "bbox_xyxy",
    "confidence",
    "missing_class",
    "supporting_labels",
    "supporting_bboxes",
    "orientation",
    "rationale",
]


@dataclass(frozen=True)
class Annotation:
    variant: str
    stage: str
    alert_order: str
    alert_id: str
    pattern_id: str
    condition_id: str
    risk_condition: str
    severity: str
    subject_id: str
    subject_label: str
    frame_index: int
    timestamp_ms: float | None
    second: float | None
    bbox_xyxy: tuple[float, float, float, float]
    confidence: float | None
    missing_class: str
    supporting_labels: tuple[str, ...]
    supporting_bboxes: tuple[tuple[float, float, float, float], ...]
    orientation: str
    rationale: str


def parse_bbox(value: Any) -> tuple[float, float, float, float] | None:
    if value in (None, ""):
        return None
    parsed: Any
    if isinstance(value, (list, tuple)):
        parsed = value
    else:
        try:
            parsed = ast.literal_eval(str(value))
        except (SyntaxError, ValueError):
            numbers = re.findall(r"-?\d+(?:\.\d+)?", str(value))
            parsed = [float(number) for number in numbers]
    if not isinstance(parsed, (list, tuple)) or len(parsed) != 4:
        return None
    return tuple(float(part) for part in parsed)  # type: ignore[return-value]


def parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(float(value))


def wanted_variants(raw_filter: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw_filter.split(",") if item.strip())


def _first_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _stage_values(stage: str) -> tuple[str, ...]:
    if stage == "both":
        return ("candidate", "confirm")
    if stage == "all":
        return ()
    return (stage,)


def _stage_is_wanted(stage: str, requested: str) -> bool:
    wanted = _stage_values(requested)
    return not wanted or stage in wanted


def _state_to_stage(value: Any, *, event_type: str = "") -> str:
    state = str(value or "").strip().lower()
    if state in {"candidate"}:
        return "candidate"
    if state in {"confirmed", "confirm", "open"}:
        return "confirm"
    if event_type == "alert_event":
        return "confirm"
    return state or "confirm"


def _frame_from_unit_id(unit_id: Any) -> int | None:
    if unit_id in (None, ""):
        return None
    match = re.search(r"(\d+)$", str(unit_id))
    if not match:
        return None
    return int(match.group(1))


def _second_from_time(timestamp_ms: float | None, second: float | None) -> float | None:
    if second is not None:
        return second
    if timestamp_ms is None:
        return None
    return timestamp_ms / 1000.0


def read_alert_annotations(
    path: str | Path,
    stage: str = "confirm",
    variants: tuple[str, ...] | set[str] = (),
) -> list[Annotation]:
    path = Path(path)
    variant_set = set(variants)
    if path.suffix.lower() == ".jsonl":
        return _read_jsonl_annotations(path, stage, variant_set)
    return _read_csv_annotations(path, stage, variant_set)


def _read_csv_annotations(path: Path, stage: str, variants: set[str]) -> list[Annotation]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {path}")
        rows = list(reader)

    annotations: list[Annotation] = []
    for row_index, row in enumerate(rows, start=1):
        variant = str(_first_value(row, "variant", "control_run_id", "run_id") or "unknown")
        if variants and variant not in variants:
            continue

        if "confirm_frame" in row or "candidate_frame" in row:
            stages = _stage_values(stage) or ("candidate", "confirm")
        elif "stage" in row or "state" in row:
            row_stage = _state_to_stage(_first_value(row, "stage", "state"))
            stages = (row_stage,) if _stage_is_wanted(row_stage, stage) else ()
        elif stage == "candidate":
            stages = ()
        else:
            stages = ("confirm",) if stage in {"both", "all"} else (stage,)

        for current_stage in stages:
            annotation = _csv_row_to_annotation(row, current_stage, row_index)
            if annotation is not None:
                annotations.append(annotation)

    return annotations


def _csv_row_to_annotation(
    row: dict[str, Any],
    stage: str,
    row_index: int,
) -> Annotation | None:
    if "confirm_frame" in row or "candidate_frame" in row:
        frame_value = _first_value(row, f"{stage}_frame", f"{stage}_frame_index")
        second_value = _first_value(row, f"{stage}_second", f"{stage}_timestamp_s")
        timestamp_ms_value = _first_value(row, f"{stage}_timestamp_ms")
        bbox_value = _first_value(row, f"{stage}_bbox", f"{stage}_bbox_xyxy")
        orientation = str(_first_value(row, f"{stage}_orientation", "orientation") or "")
        confidence_value = _first_value(row, f"{stage}_confidence", "confirm_confidence", "confidence")
    else:
        frame_value = _first_value(row, "frame_index", "frame", "frame_id")
        second_value = _first_value(row, "second", "timestamp_s")
        timestamp_ms_value = _first_value(row, "timestamp_ms")
        bbox_value = _first_value(
            row,
            "bbox_xyxy",
            "bbox",
            "subject_bbox",
            "subject_bbox_xyxy",
            "risk_bbox",
        )
        orientation = str(_first_value(row, "orientation") or "")
        confidence_value = _first_value(
            row,
            "confidence",
            "subject_confidence",
            "confirm_confidence",
        )

    frame_index = parse_int(frame_value)
    bbox = parse_bbox(bbox_value)
    if frame_index is None or bbox is None:
        return None

    timestamp_ms = parse_float(timestamp_ms_value)
    second = _second_from_time(timestamp_ms, parse_float(second_value))
    supporting_bboxes = _parse_supporting_bboxes(row)
    condition_id = str(_first_value(row, "condition_id") or "")
    missing_class = str(_first_value(row, "missing_class", "required_absent_class") or "")
    risk_condition = str(
        _first_value(row, "risk_condition", "condition", "condition_name")
        or (f"missing_{missing_class}" if missing_class else condition_id)
    )
    return Annotation(
        variant=str(_first_value(row, "variant", "control_run_id", "run_id") or "unknown"),
        stage=stage,
        alert_order=str(_first_value(row, "alert_order", "order") or row_index),
        alert_id=str(_first_value(row, "alert_id") or ""),
        pattern_id=str(_first_value(row, "pattern_id") or ""),
        condition_id=condition_id,
        risk_condition=risk_condition,
        severity=str(_first_value(row, "severity") or ""),
        subject_id=str(_first_value(row, "subject_id", "subject_key", "detection_id") or ""),
        subject_label=str(_first_value(row, "subject_label", "label") or "person"),
        frame_index=frame_index,
        timestamp_ms=timestamp_ms,
        second=second,
        bbox_xyxy=bbox,
        confidence=parse_float(confidence_value),
        missing_class=missing_class,
        supporting_labels=_split_tuple(_first_value(row, "supporting_labels", "supporting")),
        supporting_bboxes=supporting_bboxes,
        orientation=orientation,
        rationale=str(_first_value(row, "rationale", "evidence_rationale") or ""),
    )


def _parse_supporting_bboxes(row: dict[str, Any]) -> tuple[tuple[float, float, float, float], ...]:
    value = _first_value(row, "supporting_bboxes", "supporting_bbox_xyxy")
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        parts = [part.strip() for part in value.split("|") if part.strip()]
        parsed = [parse_bbox(part) for part in parts]
        return tuple(item for item in parsed if item is not None)
    if isinstance(value, list):
        parsed = [parse_bbox(item) for item in value]
        return tuple(item for item in parsed if item is not None)
    return ()


def _split_tuple(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, list):
        return tuple(str(item) for item in value if str(item))
    return tuple(part.strip() for part in str(value).split(";") if part.strip())


def _read_jsonl_annotations(path: Path, stage: str, variants: set[str]) -> list[Annotation]:
    annotations: list[Annotation] = []
    with path.open("r", encoding="utf-8") as fh:
        for row_index, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            annotation = _json_event_to_annotation(payload, row_index)
            if annotation is None:
                continue
            if variants and annotation.variant not in variants:
                continue
            if not _stage_is_wanted(annotation.stage, stage):
                continue
            annotations.append(annotation)
    return annotations


def _json_event_to_annotation(payload: dict[str, Any], row_index: int) -> Annotation | None:
    event_type = str(payload.get("event_type") or "")
    evidence = payload.get("evidence") or {}
    if not isinstance(evidence, dict):
        return None
    subject = evidence.get("subject") or {}
    if not isinstance(subject, dict):
        return None

    bbox = parse_bbox(subject.get("bbox_xyxy"))
    frame_index = parse_int(payload.get("frame_index"))
    if frame_index is None:
        frame_index = _frame_from_unit_id(payload.get("unit_id"))
    if bbox is None or frame_index is None:
        return None

    timestamp_ms = parse_float(payload.get("timestamp_ms"))
    stage = _state_to_stage(payload.get("state"), event_type=event_type)
    if event_type == "pattern_state_changed":
        stage = _state_to_stage(payload.get("state"), event_type=event_type)

    supporting = evidence.get("supporting") or []
    supporting_labels: list[str] = []
    supporting_bboxes: list[tuple[float, float, float, float]] = []
    if isinstance(supporting, list):
        for item in supporting:
            if not isinstance(item, dict):
                continue
            label = item.get("label")
            if label:
                supporting_labels.append(str(label))
            support_bbox = parse_bbox(item.get("bbox_xyxy"))
            if support_bbox is not None:
                supporting_bboxes.append(support_bbox)

    condition_id = str(payload.get("condition_id") or evidence.get("condition_id") or "")
    missing_class = str(evidence.get("missing_class") or "")
    risk_condition = str(
        payload.get("risk_condition")
        or evidence.get("risk_condition")
        or (f"missing_{missing_class}" if missing_class else condition_id)
    )
    rationale = str(evidence.get("rationale") or "")
    return Annotation(
        variant=str(
            payload.get("variant")
            or payload.get("control_run_id")
            or payload.get("media_run_id")
            or "unknown"
        ),
        stage=stage,
        alert_order=str(payload.get("alert_order") or row_index),
        alert_id=str(payload.get("alert_id") or ""),
        pattern_id=str(payload.get("pattern_id") or evidence.get("pattern_id") or ""),
        condition_id=condition_id,
        risk_condition=risk_condition,
        severity=str(payload.get("severity") or ""),
        subject_id=str(payload.get("subject_key") or evidence.get("subject_key") or ""),
        subject_label=str(subject.get("label") or "person"),
        frame_index=frame_index,
        timestamp_ms=timestamp_ms,
        second=_second_from_time(timestamp_ms, None),
        bbox_xyxy=bbox,
        confidence=parse_float(subject.get("confidence")),
        missing_class=missing_class,
        supporting_labels=tuple(supporting_labels),
        supporting_bboxes=tuple(supporting_bboxes),
        orientation=str(payload.get("orientation") or evidence.get("orientation") or ""),
        rationale=rationale,
    )


def _format_bbox(bbox: tuple[float, float, float, float]) -> str:
    return "[" + ", ".join(f"{part:.3f}".rstrip("0").rstrip(".") for part in bbox) + "]"


def _annotation_to_row(annotation: Annotation) -> dict[str, str]:
    return {
        "variant": annotation.variant,
        "stage": annotation.stage,
        "alert_order": annotation.alert_order,
        "alert_id": annotation.alert_id,
        "pattern_id": annotation.pattern_id,
        "condition_id": annotation.condition_id,
        "risk_condition": annotation.risk_condition,
        "severity": annotation.severity,
        "subject_id": annotation.subject_id,
        "subject_label": annotation.subject_label,
        "frame_index": str(annotation.frame_index),
        "timestamp_ms": "" if annotation.timestamp_ms is None else f"{annotation.timestamp_ms:.6f}",
        "second": "" if annotation.second is None else f"{annotation.second:.6f}",
        "bbox_xyxy": _format_bbox(annotation.bbox_xyxy),
        "confidence": "" if annotation.confidence is None else f"{annotation.confidence:.6f}",
        "missing_class": annotation.missing_class,
        "supporting_labels": ";".join(annotation.supporting_labels),
        "supporting_bboxes": "|".join(_format_bbox(bbox) for bbox in annotation.supporting_bboxes),
        "orientation": annotation.orientation,
        "rationale": annotation.rationale,
    }


def write_alert_details_csv(annotations: list[Annotation], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=DETAIL_FIELDNAMES)
        writer.writeheader()
        writer.writerows(_annotation_to_row(annotation) for annotation in annotations)
    return output_path


def export_alert_details_csv(
    alerts_path: str | Path,
    output_path: str | Path,
    *,
    stage: str = "all",
    variants: tuple[str, ...] | set[str] = (),
) -> Path:
    annotations = read_alert_annotations(alerts_path, stage=stage, variants=variants)
    return write_alert_details_csv(annotations, Path(output_path))
