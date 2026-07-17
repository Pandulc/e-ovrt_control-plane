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
    # G0 (spec 41 §2.1): cuantos sujetos aportan evidencia en esta unidad.
    # Bajo `subject` con track_id es 1; si el patron degrada a escena por falta
    # de track_id (causa `no_track_id`), la clave es de escena y puede ser >1.
    # Insumo del GT clip_gt.v2.
    subjects_in_evidence: int | None = None


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
    # Maximo episodico de PatternEvidence.subjects_in_evidence visto hasta este
    # instante (spec 41 SS2.1, insumo del GT clip_gt.v2). Aditivo.
    subjects_in_evidence_max: int | None = None
    # Hito de primera evidencia positiva del episodio (spec 40 SS5.2.4): instante
    # monotonico de recepcion, unit_id (clave de join) y frame_index de la unidad
    # que abrio el episodio. Se mantiene fijo hasta resolve/expire. Aditivo.
    first_evidence_ms: float | None = None
    first_evidence_unit_id: str | None = None
    first_evidence_frame_index: int | None = None
    # ADR-004: identificador de experimento, propagado desde config.run al
    # motor y de ahi a cada evento (no solo al RunSummary). Aditivo.
    experiment_id: str | None = None


class PatternProgress(BaseModel):
    """Progreso parcial de un patron en estado candidate (spec pattern-progress).

    Un registro por (frame, patron, sujeto) mientras la condicion esta en curso.
    Convencion de ids identica a PatternStateChanged. Aditivo: no reemplaza nada.
    """

    schema_version: str = "control.pattern_progress.v1"
    event_type: str = "pattern_progress"
    control_run_id: str
    media_run_id: str
    unit_id: str
    source_id: str | None = None
    pattern_id: str
    condition_id: str
    subject_key: str
    frame_index: int | None = None
    timestamp_ms: float | None = None
    mode: str  # "time" | "frames" — cual umbral rige (espeja _confirmation_met)
    elapsed_ms: float | None = None      # solo mode=time
    threshold_ms: float | None = None    # solo mode=time
    elapsed_frames: int                  # = hit_count, siempre
    threshold_frames: int | None = None  # solo si el patron define confirm_after_frames
    progress: float                      # 0..1 clamp
    experiment_id: str | None = None

