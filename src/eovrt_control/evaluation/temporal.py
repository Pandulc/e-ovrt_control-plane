"""Evaluacion temporal de alertas contra ground truth debil."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

from pydantic import BaseModel, Field

from eovrt_control.contracts.alerts import AlertEvent


class ExpectedAlert(BaseModel):
    id: str
    condition_id: str
    subject_key: str
    first_evidence_frame_index: int
    expected_alert_frame_index: int
    max_alert_frame_index: int | None = None
    first_evidence_timestamp_ms: float | None = None


class TemporalGroundTruth(BaseModel):
    scenario_id: str
    description: str | None = None
    expected_alerts: list[ExpectedAlert] = Field(default_factory=list)


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
    subject_key: str
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
    return TemporalGroundTruth.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _load_alerts(path: Path) -> list[AlertEvent]:
    return [AlertEvent.model_validate(row) for row in _read_jsonl(path)]


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
            and alert.subject_key == expected.subject_key
            and _frame_in_window(alert, expected)
        ]
        candidates.sort(key=lambda alert: alert.frame_index if alert.frame_index is not None else 10**9)
        if not candidates:
            missed.append(
                MissedAlert(
                    expected_id=expected.id,
                    condition_id=expected.condition_id,
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
