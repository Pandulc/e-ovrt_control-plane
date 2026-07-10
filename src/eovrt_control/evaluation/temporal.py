"""Evaluacion temporal de alertas contra ground truth debil."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from eovrt_control.contracts.alerts import AlertEvent

logger = logging.getLogger(__name__)


class ExpectedAlert(BaseModel):
    id: str
    condition_id: str
    # `level` gobierna el matching de forma EXPLICITA. Nunca se re-infiere de la
    # presencia de `subject_key`: un GT de sujeto al que le falta la clave debe
    # fallar ruidosamente, no caer a matching por escena e inflar el F1.
    level: Literal["scene", "subject"] = "subject"
    subject_key: str | None = None
    source_id: str | None = None
    first_evidence_frame_index: int
    expected_alert_frame_index: int
    max_alert_frame_index: int | None = None
    first_evidence_timestamp_ms: float | None = None

    @model_validator(mode="after")
    def _require_key_for_level(self) -> ExpectedAlert:
        if self.level == "subject" and self.subject_key is None:
            raise ValueError(
                f"Alerta esperada {self.id!r}: level='subject' exige `subject_key`."
            )
        if self.level == "scene" and self.source_id is None:
            raise ValueError(
                f"Alerta esperada {self.id!r}: level='scene' exige `source_id` "
                "(el matching de escena es estructurado por condition_id + source_id)."
            )
        return self


class TemporalGroundTruth(BaseModel):
    scenario_id: str
    description: str | None = None
    expected_alerts: list[ExpectedAlert] = Field(default_factory=list)


class ClipEpisode(BaseModel):
    """Episodio a nivel escena-condicion (o subject) del schema clip_gt.v2."""

    id: str
    condition_id: str
    level: Literal["scene", "subject"] = "scene"
    source_id: str | None = None
    subject_key: str | None = None
    first_evidence_frame_index: int
    expected_alert_frame_index: int
    max_alert_frame_index: int | None = None
    first_evidence_timestamp_ms: float | None = None
    subjects_in_evidence: int | None = None

    @model_validator(mode="after")
    def _require_key_for_level(self) -> ClipEpisode:
        if self.level == "subject" and self.subject_key is None:
            raise ValueError(
                f"Episodio {self.id!r}: level='subject' exige `subject_key`. "
                "Sin el, el matching caeria a nivel escena y matchearia la alerta "
                "de otro sujeto de la misma fuente (F1 inflado)."
            )
        if self.level == "scene" and self.source_id is None:
            raise ValueError(f"Episodio {self.id!r}: level='scene' exige `source_id`.")
        return self


class ClipGroundTruthV2(BaseModel):
    schema_version: Literal["clip_gt.v2"]
    clip_id: str
    episodes: list[ClipEpisode] = Field(default_factory=list)


class AlertMatch(BaseModel):
    expected_id: str
    alert_id: str
    condition_id: str
    subject_key: str
    expected_alert_frame_index: int
    alert_frame_index: int | None
    latency_frames_from_first_evidence: int | None
    latency_ms_from_first_evidence: float | None = None


class MissedAlert(BaseModel):
    expected_id: str
    condition_id: str
    # `None` en episodios de escena: ahi no existe una clave de sujeto y
    # fabricar una sintetica solo confundiria a quien la cruce con las claves
    # reales del motor. `source_id` es el identificador del episodio de escena.
    subject_key: str | None = None
    source_id: str | None = None
    expected_alert_frame_index: int
    max_alert_frame_index: int | None = None


class UnexpectedAlert(BaseModel):
    alert_id: str
    condition_id: str
    subject_key: str
    frame_index: int | None
    reason: str


class TemporalAlertEvaluation(BaseModel):
    schema_version: str = "control.eval.temporal.v1"
    scenario_id: str
    alerts_path: str
    ground_truth_path: str
    expected_alerts_count: int
    observed_alerts_count: int
    matched_alerts_count: int
    missed_alerts_count: int
    unexpected_alerts_count: int
    duplicate_alerts_count: int
    precision: float
    recall: float
    f1: float
    avg_latency_frames_from_first_evidence: float | None = None
    avg_latency_ms_from_first_evidence: float | None = None
    # Senal ruidosa ante un GT y unas alertas de granularidades incompatibles.
    warnings: list[str] = Field(default_factory=list)
    matches: list[AlertMatch] = Field(default_factory=list)
    missed_alerts: list[MissedAlert] = Field(default_factory=list)
    unexpected_alerts: list[UnexpectedAlert] = Field(default_factory=list)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


def _load_ground_truth(path: Path) -> TemporalGroundTruth:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") == "clip_gt.v2":
        clip = ClipGroundTruthV2.model_validate(raw)
        return TemporalGroundTruth(
            scenario_id=clip.clip_id,
            expected_alerts=[
                ExpectedAlert(
                    id=episode.id,
                    condition_id=episode.condition_id,
                    level=episode.level,
                    subject_key=episode.subject_key,
                    source_id=episode.source_id,
                    first_evidence_frame_index=episode.first_evidence_frame_index,
                    expected_alert_frame_index=episode.expected_alert_frame_index,
                    max_alert_frame_index=episode.max_alert_frame_index,
                    first_evidence_timestamp_ms=episode.first_evidence_timestamp_ms,
                )
                for episode in clip.episodes
            ],
        )
    return TemporalGroundTruth.model_validate(raw)


def _load_alerts(path: Path) -> list[AlertEvent]:
    return [AlertEvent.model_validate(row) for row in _read_jsonl(path)]


def _is_scene_key(subject_key: str) -> bool:
    """Una clave de escena es `{pattern_id}:{source_id}`; una de sujeto agrega
    `:{track_id}` (spatial_absence.state_key)."""
    return subject_key.count(":") == 1


def _expected_key_matches(alert: AlertEvent, expected: ExpectedAlert) -> bool:
    if expected.level == "subject":
        return alert.subject_key == expected.subject_key
    # Episodio de escena: matching estructurado por source_id, inmune a que la
    # clave de estado del motor use pattern_id != condition_id (spec 41 §2.1).
    return alert.source_id == expected.source_id


def _granularity_warnings(
    ground_truth: TemporalGroundTruth, alerts: list[AlertEvent], matched: int
) -> list[str]:
    """Detecta el caso silencioso: un GT por sujeto evaluado contra alertas de
    escena (o al reves). Sin esto el resultado es F1 = 0 sin ninguna senal, y
    quien re-corra una evaluacion historica contra el motor G0 lo leeria como
    'el motor no detecta nada'."""
    if matched or not alerts or not ground_truth.expected_alerts:
        return []
    expected_levels = {expected.level for expected in ground_truth.expected_alerts}
    observed_scene = all(_is_scene_key(alert.subject_key) for alert in alerts)
    if expected_levels == {"subject"} and observed_scene:
        return [
            "granularity_mismatch: el ground truth es por sujeto pero todas las alertas "
            "son de escena (granularity: scene). Ningun match es posible. Migra el GT a "
            "clip_gt.v2 con episodios level='scene', o corre el motor con "
            "granularity: subject."
        ]
    if expected_levels == {"scene"} and not observed_scene:
        return [
            "granularity_mismatch: el ground truth es de escena pero las alertas traen "
            "clave de sujeto (granularity: subject)."
        ]
    return []


def _frame_in_window(alert: AlertEvent, expected: ExpectedAlert) -> bool:
    if alert.frame_index is None:
        return False
    max_frame = expected.max_alert_frame_index or expected.expected_alert_frame_index
    return expected.expected_alert_frame_index <= alert.frame_index <= max_frame


def _safe_div(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def evaluate_temporal_alerts(
    alerts_path: str | Path,
    ground_truth_path: str | Path,
    output_path: str | Path | None = None,
) -> TemporalAlertEvaluation:
    """Compara alertas emitidas por replay contra expectativas temporales.

    El ground truth es deliberadamente debil: no exige cajas por frame, solo alerta
    esperada por condicion/sujeto y ventana temporal esperada.
    """

    alerts_path = Path(alerts_path)
    ground_truth_path = Path(ground_truth_path)
    ground_truth = _load_ground_truth(ground_truth_path)
    alerts = _load_alerts(alerts_path)

    matched_alert_ids: set[str] = set()
    matched_keys: set[tuple[str, str]] = set()
    matches: list[AlertMatch] = []
    missed: list[MissedAlert] = []

    for expected in ground_truth.expected_alerts:
        candidates = [
            alert
            for alert in alerts
            if alert.alert_id not in matched_alert_ids
            and alert.condition_id == expected.condition_id
            and _expected_key_matches(alert, expected)
            and _frame_in_window(alert, expected)
        ]
        candidates.sort(key=lambda alert: alert.frame_index if alert.frame_index is not None else 10**9)
        if not candidates:
            missed.append(
                MissedAlert(
                    expected_id=expected.id,
                    condition_id=expected.condition_id,
                    source_id=expected.source_id,
                    subject_key=expected.subject_key,
                    expected_alert_frame_index=expected.expected_alert_frame_index,
                    max_alert_frame_index=expected.max_alert_frame_index,
                )
            )
            continue

        alert = candidates[0]
        matched_alert_ids.add(alert.alert_id)
        matched_keys.add((alert.condition_id, alert.subject_key))
        latency_frames = (
            alert.frame_index - expected.first_evidence_frame_index
            if alert.frame_index is not None
            else None
        )
        latency_ms = (
            alert.timestamp_ms - expected.first_evidence_timestamp_ms
            if alert.timestamp_ms is not None and expected.first_evidence_timestamp_ms is not None
            else None
        )
        matches.append(
            AlertMatch(
                expected_id=expected.id,
                alert_id=alert.alert_id,
                condition_id=alert.condition_id,
                subject_key=alert.subject_key,
                expected_alert_frame_index=expected.expected_alert_frame_index,
                alert_frame_index=alert.frame_index,
                latency_frames_from_first_evidence=latency_frames,
                latency_ms_from_first_evidence=latency_ms,
            )
        )

    unexpected: list[UnexpectedAlert] = []
    duplicate_count = 0
    for alert in alerts:
        if alert.alert_id in matched_alert_ids:
            continue
        key = (alert.condition_id, alert.subject_key)
        if key in matched_keys:
            duplicate_count += 1
            reason = "duplicate_alert_for_matched_subject"
        else:
            reason = "no_matching_expected_alert"
        unexpected.append(
            UnexpectedAlert(
                alert_id=alert.alert_id,
                condition_id=alert.condition_id,
                subject_key=alert.subject_key,
                frame_index=alert.frame_index,
                reason=reason,
            )
        )

    matched_count = len(matches)
    observed_count = len(alerts)
    precision = _safe_div(matched_count, observed_count)
    recall = _safe_div(matched_count, len(ground_truth.expected_alerts))
    f1 = round((2 * precision * recall) / (precision + recall), 6) if precision + recall else 0.0

    frame_latencies = [
        match.latency_frames_from_first_evidence
        for match in matches
        if match.latency_frames_from_first_evidence is not None
    ]
    ms_latencies = [
        match.latency_ms_from_first_evidence
        for match in matches
        if match.latency_ms_from_first_evidence is not None
    ]

    warnings = _granularity_warnings(ground_truth, alerts, matched_count)
    for message in warnings:
        logger.warning(message)

    evaluation = TemporalAlertEvaluation(
        scenario_id=ground_truth.scenario_id,
        alerts_path=str(alerts_path),
        ground_truth_path=str(ground_truth_path),
        expected_alerts_count=len(ground_truth.expected_alerts),
        observed_alerts_count=observed_count,
        matched_alerts_count=matched_count,
        missed_alerts_count=len(missed),
        unexpected_alerts_count=len(unexpected),
        duplicate_alerts_count=duplicate_count,
        warnings=warnings,
        precision=precision,
        recall=recall,
        f1=f1,
        avg_latency_frames_from_first_evidence=(
            round(mean(frame_latencies), 6) if frame_latencies else None
        ),
        avg_latency_ms_from_first_evidence=round(mean(ms_latencies), 6) if ms_latencies else None,
        matches=matches,
        missed_alerts=missed,
        unexpected_alerts=unexpected,
    )

    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(evaluation.model_dump_json(indent=2) + "\n", encoding="utf-8")

    return evaluation
