"""Contratos de alertas internas."""

from __future__ import annotations

from pydantic import BaseModel

from eovrt_control.contracts.pattern import PatternEvidence


class AlertEvent(BaseModel):
    schema_version: str = "control.alert.v1"
    event_type: str = "alert_event"
    control_run_id: str
    media_run_id: str
    unit_id: str
    source_id: str
    alert_id: str
    pattern_id: str
    condition_id: str
    subject_key: str
    severity: str
    state: str = "open"
    evidence: PatternEvidence
    frame_index: int | None = None
    timestamp_ms: float | None = None

