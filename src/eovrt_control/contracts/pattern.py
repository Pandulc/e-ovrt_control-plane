"""Contratos de evidencia y estado de patrones."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    detection_id: str | None = None
    label: str
    confidence: float
    bbox_xyxy: list[float]


class PatternEvidence(BaseModel):
    pattern_id: str
    condition_id: str
    subject_key: str
    subject: EvidenceRef
    missing_class: str
    supporting: list[EvidenceRef] = Field(default_factory=list)
    score: float
    rationale: str


class PatternStateChanged(BaseModel):
    schema_version: str = "control.pattern_state.v1"
    event_type: str = "pattern_state_changed"
    control_run_id: str
    media_run_id: str
    unit_id: str
    source_id: str
    pattern_id: str
    condition_id: str
    subject_key: str
    previous_state: str
    state: str
    severity: str
    evidence: PatternEvidence
    frame_index: int | None = None
    timestamp_ms: float | None = None

