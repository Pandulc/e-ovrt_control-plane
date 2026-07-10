"""Contratos de metricas y resumen."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ApplicabilityState(BaseModel):
    """Vocabulario cerrado de ADR-006 para declarar si la evaluacion de
    patrones aplico sobre la corrida (ADR-013)."""

    state: Literal["computed", "applicable_not_computed", "not_applicable", "not_interpretable"]
    causes: list[str] = Field(default_factory=list)


class ControlMetricSample(BaseModel):
    schema_version: str = "control.metric.v1"
    event_type: str = "control_metric"
    control_run_id: str
    media_run_id: str
    unit_id: str
    source_id: str
    detections_count: int
    subjects_count: int
    pattern_evidence_count: int
    pattern_events_count: int
    alerts_count: int
    processing_ms: float


class RunSummary(BaseModel):
    schema_version: str = "control.summary.v1"
    control_run_id: str
    media_run_ids: list[str] = Field(default_factory=list)
    scenario: str
    pattern_set_id: str
    active_pattern_ids: list[str]
    units_processed: int
    units_failed: int
    pattern_events_count: int
    alerts_count: int
    errors_count: int
    avg_processing_ms: float
    output_files: dict[str, str]
    warnings: list[str] = Field(default_factory=list)
    degraded: bool = False
    degradation_causes: list[str] = Field(default_factory=list)
    # Aditivo (ADR-013): declara si la evaluacion de patrones (episodios,
    # persistencia, histeresis) aplico sobre esta corrida. None = corridas
    # previas a esta task, que no lo calculaban.
    pattern_evaluation: ApplicabilityState | None = None
    started_at: str
    finished_at: str

