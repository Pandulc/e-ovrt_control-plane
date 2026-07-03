"""Orquestacion de replay DBE del plano de control."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from time import perf_counter

from eovrt_control.config import PatternDefinition, ReplayConfig, load_replay_config
from eovrt_control.contracts.media import DetectionEvent
from eovrt_control.contracts.metrics import ControlMetricSample, RunSummary
from eovrt_control.engine.pattern_engine import PatternEngine
from eovrt_control.sinks.artifacts import RunArtifacts
from eovrt_control.sinks.jsonl import JsonlSink
from eovrt_control.sources.media_jsonl import iter_media_jsonl
from eovrt_control.sinks.alerts_csv import export_alert_details_csv

logger = logging.getLogger(__name__)

_PERSON_LABELS = {"person", "worker", "human", "people"}
_AUTOGEN_DETECTION_ID = re.compile(r"^det_\d+$")


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _requires_temporal_persistence(pattern: PatternDefinition) -> bool:
    return pattern.timing.confirm_after_frames > 1 or pattern.timing.confirm_after_ms is not None


def _is_stable_subject_id(detection_id: str | None) -> bool:
    """Un id es estable si viene del tracking o del media-plane, no del fallback det_NNN."""
    if not detection_id:
        return False
    return _AUTOGEN_DETECTION_ID.match(detection_id) is None


def _count_person_ids(event: DetectionEvent) -> tuple[int, int]:
    persons = 0
    stable = 0
    for detection in event.detections:
        if (detection.label or "").strip().lower() not in _PERSON_LABELS:
            continue
        persons += 1
        if _is_stable_subject_id(detection.detection_id):
            stable += 1
    return persons, stable


def _control_run_id(config: ReplayConfig) -> str:
    if config.run.id:
        return config.run.id
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{config.run.name}_{stamp}"


def run_replay(config_path: str | Path) -> RunSummary:
    config = load_replay_config(config_path)
    if config.patterns_file is None:
        raise ValueError("La configuracion no tiene patterns_file resuelto")

    control_run_id = _control_run_id(config)
    base_dir = config.resolve_path(config.outputs.base_dir)
    artifacts = RunArtifacts(base_dir / control_run_id)
    artifacts.write_effective_config(config)

    active_patterns = config.patterns_file.active_patterns(config.patterns.active_ids)
    engine = PatternEngine(control_run_id=control_run_id, patterns=active_patterns)
    input_path = config.resolve_path(config.input.path)

    started_at = _utc_now()
    media_run_ids: set[str] = set()
    processing_times: list[float] = []
    units_processed = 0
    units_failed = 0
    pattern_events_count = 0
    alerts_count = 0
    errors_count = 0
    persistence_required = any(_requires_temporal_persistence(p) for p in active_patterns)
    persons_seen = 0
    persons_with_stable_id = 0

    with (
        JsonlSink(artifacts.pattern_events_path) as pattern_sink,
        JsonlSink(artifacts.alerts_path) as alert_sink,
        JsonlSink(artifacts.metrics_path) as metric_sink,
        JsonlSink(artifacts.errors_path) as error_sink,
    ):
        if not input_path.exists():
            error = {
                "schema_version": "control.error.v1",
                "event_type": "control_error",
                "control_run_id": control_run_id,
                "message": f"Archivo de entrada no encontrado: {input_path}",
                "error_type": "FileNotFoundError",
            }
            error_sink.write(error)
            errors_count += 1
        else:
            for _, event, error in iter_media_jsonl(input_path, control_run_id):
                if error is not None:
                    error_sink.write(error)
                    errors_count += 1
                    units_failed += 1
                    continue
                if event is None:
                    continue

                start = perf_counter()
                result = engine.process(event)
                processing_ms = (perf_counter() - start) * 1000.0

                media_run_ids.add(event.run_id)
                units_processed += 1
                processing_times.append(processing_ms)
                pattern_events_count += len(result.pattern_events)
                alerts_count += len(result.alerts)
                event_persons, event_stable = _count_person_ids(event)
                persons_seen += event_persons
                persons_with_stable_id += event_stable

                for pattern_event in result.pattern_events:
                    pattern_sink.write(pattern_event)
                for alert in result.alerts:
                    alert_sink.write(alert)

                metric_sink.write(
                    ControlMetricSample(
                        control_run_id=control_run_id,
                        media_run_id=event.run_id,
                        unit_id=event.unit_id,
                        source_id=event.source.source_id,
                        detections_count=len(event.detections),
                        subjects_count=result.subjects_count,
                        pattern_evidence_count=result.evidences_count,
                        pattern_events_count=len(result.pattern_events),
                        alerts_count=len(result.alerts),
                        processing_ms=processing_ms,
                    )
                )

    export_alert_details_csv(artifacts.alerts_path, artifacts.alerts_csv_path)

    warnings: list[str] = []
    if persistence_required and persons_seen > 0 and persons_with_stable_id == 0:
        message = (
            "Persistencia temporal inactiva: los patrones exigen confirmacion multi-frame "
            "pero ninguna persona trae detection_id estable (solo fallback det_NNN). "
            "El motor no podra confirmar condiciones; genera detecciones con tracking "
            "(eovrt-labs generate-detections --track) o publica IDs estables desde el media-plane."
        )
        warnings.append(message)
        logger.warning(message)

    summary = RunSummary(
        control_run_id=control_run_id,
        media_run_ids=sorted(media_run_ids),
        scenario=config.run.scenario,
        pattern_set_id=config.patterns_file.pattern_set.id,
        active_pattern_ids=[pattern.id for pattern in active_patterns],
        units_processed=units_processed,
        units_failed=units_failed,
        pattern_events_count=pattern_events_count,
        alerts_count=alerts_count,
        errors_count=errors_count,
        avg_processing_ms=mean(processing_times) if processing_times else 0.0,
        output_files={
            "effective_config": str(artifacts.effective_config_path),
            "pattern_events": str(artifacts.pattern_events_path),
            "alerts": str(artifacts.alerts_path),
            "alerts_csv": str(artifacts.alerts_csv_path),
            "metrics": str(artifacts.metrics_path),
            "errors": str(artifacts.errors_path),
            "summary": str(artifacts.summary_path),
        },
        warnings=warnings,
        started_at=started_at,
        finished_at=_utc_now(),
    )
    artifacts.write_summary(summary)
    return summary

