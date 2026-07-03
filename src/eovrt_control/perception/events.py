"""Construccion de eventos media.detection.v1."""

from __future__ import annotations

from dataclasses import dataclass

from eovrt_control.contracts.media import (
    Detection,
    DetectionEvent,
    DetectionEventModel,
    DetectionEventPrompts,
    DetectionEventSource,
    DetectionEventTiming,
)


@dataclass(frozen=True)
class RawModelDetection:
    label: str
    confidence: float
    bbox_xyxy: list[float]
    detection_id: str | None = None


def build_detection(
    label: str,
    confidence: float,
    bbox_xyxy: list[float],
    detection_id: str | None = None,
    *,
    model_name: str | None = None,
    width: int = 0,
    height: int = 0,
) -> Detection:
    bbox_norm: list[float] = []
    if width > 0 and height > 0 and len(bbox_xyxy) == 4:
        x1, y1, x2, y2 = bbox_xyxy
        bbox_norm = [
            round(max(0.0, min(1.0, x1 / width)), 4),
            round(max(0.0, min(1.0, y1 / height)), 4),
            round(max(0.0, min(1.0, x2 / width)), 4),
            round(max(0.0, min(1.0, y2 / height)), 4),
        ]
    return Detection(
        detection_id=detection_id,
        label=label,
        prompt_id=label,
        confidence=round(confidence, 4),
        bbox_xyxy=[round(v, 1) for v in bbox_xyxy],
        bbox_norm_xyxy=bbox_norm,
        model_name=model_name,
    )


def attach_epp_to_persons(
    persons: list[RawModelDetection],
    epp_items: list[RawModelDetection],
) -> list[Detection]:
    """Asocia cascos/chalecos a personas y asigna detection_id derivado (utilidad legacy)."""
    from eovrt_control.perception.tracking import (
        bbox_center,
        center_inside_region,
        person_torso_region,
        person_upper_body_region,
    )

    output: list[Detection] = []
    for index, person in enumerate(persons):
        subject_id = person.detection_id or f"person_{index:03d}"
        output.append(
            build_detection(
                label="person",
                confidence=person.confidence,
                bbox_xyxy=person.bbox_xyxy,
                detection_id=subject_id,
            )
        )
        upper = person_upper_body_region(person.bbox_xyxy)
        torso = person_torso_region(person.bbox_xyxy)
        for item in epp_items:
            center = bbox_center(item.bbox_xyxy)
            if item.label == "helmet" and center_inside_region(center, upper):
                output.append(
                    build_detection(
                        label="helmet",
                        confidence=item.confidence,
                        bbox_xyxy=item.bbox_xyxy,
                        detection_id=f"{subject_id}_helmet",
                    )
                )
            elif item.label == "vest" and center_inside_region(center, torso):
                output.append(
                    build_detection(
                        label="vest",
                        confidence=item.confidence,
                        bbox_xyxy=item.bbox_xyxy,
                        detection_id=f"{subject_id}_vest",
                    )
                )
    return output


def build_detection_event(
    *,
    run_id: str,
    unit_id: str,
    source_id: str,
    source_type: str,
    frame_index: int | None,
    timestamp_ms: float | None,
    width: int,
    height: int,
    model_name: str,
    model_id: str | None,
    device: str,
    prompt_set_id: str,
    detections: list[Detection],
    normalize_ms: float = 0.0,
    inference_ms: float = 0.0,
    postprocess_ms: float = 0.0,
    write_ms: float = 0.0,
    total_ms: float = 0.0,
) -> DetectionEvent:
    return DetectionEvent(
        run_id=run_id,
        unit_id=unit_id,
        source=DetectionEventSource(
            source_id=source_id,
            source_type=source_type,
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
            width=width,
            height=height,
        ),
        model=DetectionEventModel(
            name=model_name,
            model_id=model_id,
            device=device,
        ),
        prompts=DetectionEventPrompts(prompt_set_id=prompt_set_id),
        detections=detections,
        timing=DetectionEventTiming(
            normalize_ms=round(normalize_ms, 2),
            inference_ms=round(inference_ms, 2),
            postprocess_ms=round(postprocess_ms, 2),
            write_ms=round(write_ms, 2),
            total_ms=round(total_ms, 2),
        ),
    )


def serialize_event(event: DetectionEvent) -> str:
    return event.model_dump_json(exclude_none=True)
