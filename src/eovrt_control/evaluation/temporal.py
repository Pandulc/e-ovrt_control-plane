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


class SubThresholdEvent(BaseModel):
    """Evento real pero no alertable (spec 43 SS4.1): permite distinguir un FP
    verdadero de una alerta a un evento sub-umbral."""

    condition_id: str
    start_ms: float
    end_ms: float
    reason: str | None = None


class ClipEpisodeV2(BaseModel):
    """Episodio escena-condicion (o subject) del schema clip_gt.v2 (spec 43 SS4), en ms."""

    id: str
    condition_id: str
    level: Literal["scene", "subject"] = "scene"
    source_id: str | None = None
    subject_key: str | None = None
    start_ms: float  # inicio anotado de la condicion (t0 oficial, spec 43 SS4.1)
    end_ms: float
    subjects_in_evidence: int | None = None

    @model_validator(mode="after")
    def _require_key_for_level(self) -> ClipEpisodeV2:
        if self.level == "subject" and self.subject_key is None:
            raise ValueError(f"Episodio {self.id!r}: level='subject' exige `subject_key`.")
        if self.level == "scene" and self.source_id is None:
            raise ValueError(f"Episodio {self.id!r}: level='scene' exige `source_id`.")
        if self.end_ms < self.start_ms:
            raise ValueError(f"Episodio {self.id!r}: end_ms < start_ms.")
        return self


class ClipGroundTruthV2(BaseModel):
    schema_version: Literal["clip_gt.v2"]
    clip_id: str
    source_file: str | None = None
    block: str | None = None
    scenario: str | None = None
    fps_nominal: float | None = None
    duration_ms: float | None = None
    recording: dict[str, Any] | None = None
    annotation: dict[str, Any] | None = None
    negative: bool = False
    episodes: list[ClipEpisodeV2] = Field(default_factory=list)
    sub_threshold_events: list[SubThresholdEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _internal_consistency(self) -> ClipGroundTruthV2:
        if self.negative and self.episodes:
            raise ValueError(f"Clip {self.clip_id!r}: negative=True pero trae episodios.")
        if self.duration_ms is not None:
            for ep in self.episodes:
                if ep.end_ms > self.duration_ms:
                    raise ValueError(
                        f"Clip {self.clip_id!r}: episodio {ep.id!r} excede duration_ms."
                    )
        return self


class MatchingWindow(BaseModel):
    """Ventana de matching alerta<->episodio (spec 43 SS4.1). La alerta legitima
    cae en [start_ms + persistencia_min, start_ms + t_alert_max]."""

    persistencia_min_ms: float
    t_alert_max_ms: float


# Tabla D.4 vigente (spec 43 SS4.1 / SS10). Parametrizable: el fixture sintetico usa
# timings comprimidos y el evaluador acepta un override por corrida/pattern set.
DEFAULT_MATCHING_WINDOWS: dict[str, MatchingWindow] = {
    "CR-01": MatchingWindow(persistencia_min_ms=3000.0, t_alert_max_ms=10000.0),
    "CR-02": MatchingWindow(persistencia_min_ms=5000.0, t_alert_max_ms=20000.0),
}


def _episode_key_matches(alert: AlertEvent, episode: ClipEpisodeV2) -> bool:
    if episode.level == "subject":
        return alert.subject_key == episode.subject_key
    return alert.source_id == episode.source_id


def _alert_in_episode_window(
    alert: AlertEvent, episode: ClipEpisodeV2, window: MatchingWindow
) -> bool:
    if alert.timestamp_ms is None:
        return False
    lo = episode.start_ms + window.persistencia_min_ms
    hi = episode.start_ms + window.t_alert_max_ms
    return lo <= alert.timestamp_ms <= hi


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
    # Campos v2 (episodio, ADR-011/ADR-006). Aditivos: schema_version no cambia.
    re_alerts_count: int = 0
    sub_threshold_count: int = 0
    applicability_state: str | None = None
    applicability_cause: str | None = None
    avg_latency_ms_from_episode_start: float | None = None
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


def _load_ground_truth(path: Path) -> TemporalGroundTruth | ClipGroundTruthV2:
    """Carga el ground truth SIN aplanar: v1 (`TemporalGroundTruth`, expected_alerts
    por frame) y v2 (`ClipGroundTruthV2`, episodios en ms, spec 43 SS4) son schemas
    distintos; el dispatch de `evaluate_temporal_alerts` decide segun el tipo."""
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") == "clip_gt.v2":
        return ClipGroundTruthV2.model_validate(raw)
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
    matching_windows: dict[str, MatchingWindow] | None = None,
) -> TemporalAlertEvaluation:
    """Compara alertas emitidas por replay contra expectativas temporales.

    Dispatcha por el tipo de ground truth ya cargado: `TemporalGroundTruth` (v1,
    frame-based, `control.eval.temporal.v1`) va a `_evaluate_v1`; `ClipGroundTruthV2`
    (v2, episodios en ms, spec 43 SS4) va a `_evaluate_v2` con matching por ventana
    (ADR-011: re_alerts, sub_threshold, aplicabilidad ADR-006).
    """

    alerts_path = Path(alerts_path)
    ground_truth_path = Path(ground_truth_path)
    ground_truth = _load_ground_truth(ground_truth_path)
    alerts = _load_alerts(alerts_path)

    if isinstance(ground_truth, ClipGroundTruthV2):
        evaluation = _evaluate_v2(
            alerts,
            ground_truth,
            alerts_path,
            ground_truth_path,
            matching_windows or DEFAULT_MATCHING_WINDOWS,
        )
    else:
        evaluation = _evaluate_v1(alerts, ground_truth, alerts_path, ground_truth_path)

    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(evaluation.model_dump_json(indent=2) + "\n", encoding="utf-8")

    return evaluation


def _evaluate_v1(
    alerts: list[AlertEvent],
    ground_truth: TemporalGroundTruth,
    alerts_path: Path,
    ground_truth_path: Path,
) -> TemporalAlertEvaluation:
    """Evaluacion v1 (frame-based, `control.eval.temporal.v1`). Logica sin cambios
    respecto de la version previa a Task 3: solo recibe los objetos ya cargados."""

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
        applicability_state="computed",
        applicability_cause=None,
    )

    return evaluation


def _evaluate_v2(
    alerts: list[AlertEvent],
    ground_truth: ClipGroundTruthV2,
    alerts_path: Path,
    ground_truth_path: Path,
    matching_windows: dict[str, MatchingWindow],
) -> TemporalAlertEvaluation:
    """Evaluacion v2 a nivel episodio (spec 43 SS4, ADR-011, ADR-006).

    Aplicabilidad primero: una fuente sin timestamps (todas las alertas con
    `timestamp_ms is None`) no es evaluable temporalmente y se declara
    `not_applicable` en vez de contarse silenciosamente como missed/FP.

    Por episodio: candidatos = alertas con `condition_id` igual, misma clave
    (escena o sujeto) y dentro de la ventana de matching. El primer candidato
    por `timestamp_ms` es el match; el resto de candidatos del MISMO episodio
    son `re_alerts` (ADR-011: no son FP). Las alertas no consumidas por ningun
    episodio caen a `sub_threshold` (si estan dentro de un `sub_threshold_event`
    de su condicion) o a `unexpected` (FP verdadero) en caso contrario.
    """

    warnings: list[str] = []

    if alerts and all(alert.timestamp_ms is None for alert in alerts):
        return TemporalAlertEvaluation(
            scenario_id=ground_truth.clip_id,
            alerts_path=str(alerts_path),
            ground_truth_path=str(ground_truth_path),
            expected_alerts_count=len(ground_truth.episodes),
            observed_alerts_count=len(alerts),
            matched_alerts_count=0,
            missed_alerts_count=0,
            unexpected_alerts_count=0,
            duplicate_alerts_count=0,
            re_alerts_count=0,
            sub_threshold_count=0,
            precision=0.0,
            recall=0.0,
            f1=0.0,
            applicability_state="not_applicable",
            applicability_cause="non_temporal_source",
            warnings=warnings,
        )

    consumed_alert_ids: set[str] = set()
    matched_count = 0
    re_alerts_count = 0
    missed_count = 0
    latencies: list[float] = []

    for episode in ground_truth.episodes:
        window = matching_windows.get(episode.condition_id) or DEFAULT_MATCHING_WINDOWS.get(
            episode.condition_id
        )
        if window is None:
            warnings.append(
                f"sin matching_window para condition_id={episode.condition_id!r} "
                f"(episodio {episode.id!r}): no se puede evaluar, se cuenta como missed."
            )
            missed_count += 1
            continue

        candidates = sorted(
            (
                alert
                for alert in alerts
                # Una alerta ya consumida (match o re_alert) por un episodio previo
                # no puede volver a matchear otro episodio: dos episodios del mismo
                # condition_id + clave con ventanas solapadas (t_alert_max_ms llega
                # a 10-20s) comparten alertas candidatas, y sin esta exclusion una
                # sola alerta infla matched_alerts_count/recall en ambos episodios.
                if alert.alert_id not in consumed_alert_ids
                and alert.condition_id == episode.condition_id
                and _episode_key_matches(alert, episode)
                and _alert_in_episode_window(alert, episode, window)
            ),
            key=lambda alert: alert.timestamp_ms,
        )
        if not candidates:
            missed_count += 1
            continue

        first = candidates[0]
        consumed_alert_ids.add(first.alert_id)
        matched_count += 1
        latencies.append(first.timestamp_ms - episode.start_ms)
        for extra in candidates[1:]:
            consumed_alert_ids.add(extra.alert_id)
            re_alerts_count += 1

    unexpected: list[UnexpectedAlert] = []
    sub_threshold_count = 0
    for alert in alerts:
        if alert.alert_id in consumed_alert_ids:
            continue
        in_sub_threshold = alert.timestamp_ms is not None and any(
            event.condition_id == alert.condition_id and event.start_ms <= alert.timestamp_ms <= event.end_ms
            for event in ground_truth.sub_threshold_events
        )
        if in_sub_threshold:
            sub_threshold_count += 1
            continue
        unexpected.append(
            UnexpectedAlert(
                alert_id=alert.alert_id,
                condition_id=alert.condition_id,
                subject_key=alert.subject_key,
                frame_index=alert.frame_index,
                reason="outside_all_episode_windows",
            )
        )

    unexpected_count = len(unexpected)
    # ADR-011: re_alerts y sub_threshold NO son FP, no entran al denominador de precision.
    precision = _safe_div(matched_count, matched_count + unexpected_count)
    recall = _safe_div(matched_count, len(ground_truth.episodes))
    f1 = round((2 * precision * recall) / (precision + recall), 6) if precision + recall else 0.0

    for message in warnings:
        logger.warning(message)

    return TemporalAlertEvaluation(
        scenario_id=ground_truth.clip_id,
        alerts_path=str(alerts_path),
        ground_truth_path=str(ground_truth_path),
        expected_alerts_count=len(ground_truth.episodes),
        observed_alerts_count=len(alerts),
        matched_alerts_count=matched_count,
        missed_alerts_count=missed_count,
        unexpected_alerts_count=unexpected_count,
        duplicate_alerts_count=0,
        re_alerts_count=re_alerts_count,
        sub_threshold_count=sub_threshold_count,
        precision=precision,
        recall=recall,
        f1=f1,
        avg_latency_ms_from_episode_start=round(mean(latencies), 6) if latencies else None,
        applicability_state="computed",
        applicability_cause=None,
        warnings=warnings,
        unexpected_alerts=unexpected,
    )
