"""Orquestacion de replay DBE del plano de control."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from time import perf_counter

from eovrt_control.config import PatternDefinition, ReplayConfig, load_replay_config
from eovrt_control.contracts.metrics import ApplicabilityState, ControlMetricSample, RunSummary
from eovrt_control.engine.pattern_engine import PatternEngine
from eovrt_control.sinks.artifacts import RunArtifacts
from eovrt_control.sinks.jsonl import JsonlSink
from eovrt_control.sources.media_jsonl import iter_media_jsonl
from eovrt_control.sinks.alerts_csv import export_alert_details_csv

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _blocks_confirmation_on_non_temporal_source(pattern: PatternDefinition) -> bool:
    """True si el patron NO PUEDE alertar jamas sobre una fuente no temporal.

    Sobre imagenes independientes `source_id` cambia en cada unidad, asi que la
    clave de estado tambien: `hit_count` nunca supera 1. Solo un umbral de
    confirmacion por FRAMES > 1 vuelve la alerta inalcanzable.

    Los umbrales en ms NO bloquean: `_confirmation_met` exige `timestamp_ms`, que
    en imagenes es `None`, de modo que cae al camino por frames (default 1) y
    confirma igual. Tampoco bloquean `resolve_*` ni la expiracion por ausencia:
    gobiernan la salida del episodio, no su confirmacion. Verificado empiricamente.
    """
    return pattern.timing.confirm_after_frames > 1


def _has_inert_temporal_thresholds(pattern: PatternDefinition) -> bool:
    """True si el patron declara umbrales temporales que sobre una fuente no
    temporal se ignoran EN SILENCIO (no bloquean, no se aplican).

    Es una trampa distinta de la anterior y merece su propia causa: un operador
    que declara `confirm_after_ms: 4000` sobre imagenes obtiene confirmacion
    instantanea, no una ventana de 4 segundos.
    """
    timing = pattern.timing
    return (
        timing.confirm_after_ms is not None
        or timing.resolve_after_ms is not None
        or timing.resolve_after_frames > 1
        or timing.subject_absent_timeout_frames is not None
        or timing.subject_absent_timeout_ms is not None
    )


def _pattern_evaluation_state(
    source_types: set[str], active_patterns: list[PatternDefinition]
) -> ApplicabilityState:
    """ADR-013: la temporalidad se detecta, no se configura."""
    if not source_types:
        # Ningun source_type observado: 0 unidades procesadas (input
        # inexistente, vacio, o todas las lineas fallaron el parseo). No hay
        # base para declarar la evaluacion "computed" (cero silencioso).
        return ApplicabilityState(state="applicable_not_computed", causes=["no_units_processed"])
    if len(source_types) > 1:
        # Fuentes mixtas no tienen una semantica temporal coherente: no
        # sabemos si tratar la corrida como temporal o no.
        return ApplicabilityState(state="not_interpretable", causes=["mixed_source_types"])
    if source_types <= {"image"}:
        causes = ["non_temporal_source"]
        if any(_blocks_confirmation_on_non_temporal_source(p) for p in active_patterns):
            causes.append("persistence_unreachable_on_non_temporal_source")
        if any(_has_inert_temporal_thresholds(p) for p in active_patterns):
            causes.append("inert_temporal_thresholds")
        return ApplicabilityState(state="not_applicable", causes=causes)
    return ApplicabilityState(state="computed")


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
    degradation_causes: set[str] = set()
    source_types: set[str] = set()

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
                source_types.add(event.source.source_type)
                units_processed += 1
                processing_times.append(processing_ms)
                pattern_events_count += len(result.pattern_events)
                alerts_count += len(result.alerts)
                # Senal en vivo: la degradacion se avisa la PRIMERA vez que
                # aparece, no solo agregada en el summary al terminar. En una
                # corrida larga (RTSP) el summary llega demasiado tarde.
                for cause in result.degradation_causes - degradation_causes:
                    logger.warning("Motor degradado: causa %r (unidad %s)", cause, event.unit_id)
                degradation_causes |= result.degradation_causes

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
    pattern_evaluation = _pattern_evaluation_state(source_types, active_patterns)
    if pattern_evaluation.state != "computed":
        logger.warning(
            "Evaluacion de patrones %s: %s",
            pattern_evaluation.state,
            ", ".join(pattern_evaluation.causes) or "sin causa declarada",
        )

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
        degraded=bool(degradation_causes),
        degradation_causes=sorted(degradation_causes),
        pattern_evaluation=pattern_evaluation,
        started_at=started_at,
        finished_at=_utc_now(),
    )
    artifacts.write_summary(summary)
    return summary

