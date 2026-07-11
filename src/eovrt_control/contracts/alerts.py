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
    # Maximo episodico de PatternEvidence.subjects_in_evidence (spec 41 SS2.1).
    # Copiado del PatternStateChanged que confirmo la alerta. Aditivo.
    subjects_in_evidence_max: int | None = None
    # Instante monotonico (ms) en que el motor escribio esta alerta (spec 40
    # SS5.2.4). Propio del proceso: no comparable entre corridas (replay vs
    # bus). Aditivo.
    alert_registered_ms: float | None = None
    # Hito de primera evidencia del episodio (spec 40 SS5.2.4), copiado de
    # PatternRuntimeState.first_evidence_* al momento de confirmar la alerta.
    # Aditivo.
    first_evidence_ms: float | None = None
    first_evidence_unit_id: str | None = None
    first_evidence_frame_index: int | None = None
    # ADR-004: identificador de experimento, propagado desde config.run al
    # motor y de ahi a cada alerta (no solo al RunSummary). Aditivo.
    experiment_id: str | None = None

