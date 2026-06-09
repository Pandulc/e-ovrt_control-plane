"""Contratos compatibles con eventos del plano de medios."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Detection(BaseModel):
    detection_id: str | None = None
    label: str
    prompt_id: str | None = None
    confidence: float
    bbox_xyxy: list[float] = Field(description="Bounding box en pixeles [x1, y1, x2, y2]")
    bbox_norm_xyxy: list[float] = Field(
        default_factory=list,
        description="Bounding box normalizado [0, 1] [x1, y1, x2, y2]",
    )
    area_px: float | None = None
    model_name: str | None = None

    @model_validator(mode="after")
    def derive_area(self) -> "Detection":
        if self.area_px is None and len(self.bbox_xyxy) == 4:
            x1, y1, x2, y2 = self.bbox_xyxy
            self.area_px = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        return self


class DetectionEventSource(BaseModel):
    source_id: str
    source_type: str
    frame_index: int | None = None
    timestamp_ms: float | None = None
    width: int = 0
    height: int = 0


class DetectionEventModel(BaseModel):
    name: str
    model_id: str | None = None
    device: str = "cpu"


class DetectionEventPrompts(BaseModel):
    prompt_set_id: str = "unknown"


class DetectionEventTiming(BaseModel):
    read_ms: float = 0.0
    preprocess_ms: float = 0.0
    inference_ms: float = 0.0
    postprocess_ms: float = 0.0
    write_ms: float = 0.0
    total_ms: float = 0.0


class DetectionEvent(BaseModel):
    schema_version: str = "media.detection.v1"
    event_type: str = "detection_event"
    run_id: str
    unit_id: str
    source: DetectionEventSource
    model: DetectionEventModel
    prompts: DetectionEventPrompts
    detections: list[Detection]
    timing: DetectionEventTiming = Field(default_factory=DetectionEventTiming)

    source_path: str | None = None
    model_adapter: str | None = None
    prompt_version: str | None = None
    timing_ms: dict[str, float] | None = None

    @model_validator(mode="before")
    @classmethod
    def backport_flat_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        if "source_path" in data and "source" not in data:
            data["source"] = {
                "source_id": Path(data["source_path"]).name,
                "source_type": data.get("source_type", "image"),
                "frame_index": data.get("frame_index"),
                "timestamp_ms": data.get("timestamp_ms"),
                "width": data.get("width", 0),
                "height": data.get("height", 0),
            }
        if "model_adapter" in data and "model" not in data:
            data["model"] = {
                "name": data["model_adapter"],
                "model_id": data.get("model_id"),
                "device": data.get("device", "cpu"),
            }
        if "prompt_version" in data and "prompts" not in data:
            data["prompts"] = {"prompt_set_id": data["prompt_version"]}
        if "timing_ms" in data and "timing" not in data:
            timing_ms = data["timing_ms"] or {}
            data["timing"] = {
                "inference_ms": timing_ms.get("inference", 0.0),
                "total_ms": timing_ms.get("total", 0.0),
            }

        if "source" in data and not data.get("source_path") and isinstance(data["source"], dict):
            data["source_path"] = data["source"].get("source_id", "")
        if "model" in data and not data.get("model_adapter") and isinstance(data["model"], dict):
            data["model_adapter"] = data["model"].get("name", "")
        if "prompts" in data and not data.get("prompt_version") and isinstance(data["prompts"], dict):
            data["prompt_version"] = data["prompts"].get("prompt_set_id", "")
        if "timing" in data and not data.get("timing_ms") and isinstance(data["timing"], dict):
            timing = data["timing"]
            data["timing_ms"] = {
                "total": timing.get("total_ms", 0.0),
                "inference": timing.get("inference_ms", 0.0),
            }
        return data

