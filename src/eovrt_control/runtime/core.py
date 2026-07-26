"""Bucle motor<->fuente, agnostico de la entrada (spec 41 SS3-SS4).

`run_replay` y `run_live` son constructores de fuente; el trabajo real -y la
semantica del summary- vive aca, una sola vez. Es la condicion del test de
paridad replay<->stream (spec 40 SS3.4).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean
from time import perf_counter

from eovrt_control.config import PatternDefinition, ReplayConfig
from eovrt_control.contracts.metrics import ApplicabilityState, ControlMetricSample, RunSummary
from eovrt_control.engine.pattern_engine import PatternEngine
from eovrt_control.metrics.latency import percentiles
from eovrt_control.sinks.alerts_csv import export_alert_details_csv
from eovrt_control.sinks.artifacts import RunArtifacts
from eovrt_control.sinks.jsonl import JsonlSink
from eovrt_control.sources.base import MediaEventSource
from eovrt_control.transport.alert_bus import ALERT_TOPIC_PREFIX, AlertBusPublisher

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def control_run_id(config: ReplayConfig) -> str:
    if config.run.id:
        return config.run.id
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{config.run.name}_{stamp}"


@dataclass
class RunProgress:
    """Contadores en vivo de una corrida (los lee `GET /api/runs/current`).

    Se mutan desde el hilo de ejecucion y se leen desde el hilo del servidor:
    son enteros y el GIL los hace atomicos; no hace falta lock.
    """

    units_processed: int = 0
    units_failed: int = 0
    errors_count: int = 0
    pattern_events_count: int = 0
    alerts_count: int = 0
    bus_dropped_events: int = 0
    # Referencia al PatternEngine de la corrida en curso (solo-lectura desde el
    # hilo del servidor via `PatternEngine.snapshot_active()`). Se asigna UNA
    # sola vez, al arrancar `execute_over_source`, y nunca se reasigna: es una
    # simple referencia de objeto, atomica bajo el GIL igual que los enteros de
    # arriba. `snapshot_active()` es quien hace la copia defensiva de
    # `PatternEngine._state` antes de iterar, no este campo.
    engine: PatternEngine | None = None


@dataclass
class PreparedRun:
    """Todo lo que hay que resolver ANTES de consumir la primera unidad.

    El servicio necesita el `control_run_id` antes de construir el `BusSource`
    (construirlo es suscribirse, y hay que suscribirse antes de disparar el run
    del media-plane).
    """

    control_run_id: str
    artifacts: RunArtifacts
    active_patterns: list[PatternDefinition]


def prepare_run(config: ReplayConfig) -> PreparedRun:
    if config.patterns_file is None:
        raise ValueError("La configuracion no tiene patterns_file resuelto")
    run_id = control_run_id(config)
    base_dir = config.resolve_path(config.outputs.base_dir)
    artifacts = RunArtifacts(base_dir / run_id)
    artifacts.write_effective_config(config)
    return PreparedRun(
        control_run_id=run_id,
        artifacts=artifacts,
        active_patterns=config.patterns_file.active_patterns(config.patterns.active_ids),
    )


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


def execute_over_source(
    *,
    config: ReplayConfig,
    source: MediaEventSource,
    control_run_id: str,
    artifacts: RunArtifacts,
    active_patterns: list[PatternDefinition],
    progress: RunProgress | None = None,
) -> RunSummary:
    """Corre el motor sobre una fuente cualquiera y estampa los artefactos."""
    engine = PatternEngine(
        control_run_id=control_run_id,
        patterns=active_patterns,
        experiment_id=config.run.experiment_id,
    )
    if progress is not None:
        # Publicar el engine ANTES del bucle: `GET /api/runs/current` puede
        # llegar en cualquier momento de la corrida, incluida la primera unidad.
        progress.engine = engine

    started_at = _utc_now()
    media_run_ids: set[str] = set()
    processing_times: list[float] = []
    ttfa_internal_ms: list[float] = []
    units_processed = 0
    units_failed = 0
    pattern_events_count = 0
    alerts_count = 0
    errors_count = 0
    degradation_causes: set[str] = set()
    source_types: set[str] = set()

    # Sentinela de cierre para run.lifecycle.v1/run_finished (mirror del
    # bus_status del media-plane): default succeeded, degrada a failed/stopped
    # segun como termine la corrida. Nunca se silencia una excepcion real.
    run_status = "succeeded"
    alert_publisher: AlertBusPublisher | None = None
    if config.alert_bus.enabled:
        try:
            alert_publisher = AlertBusPublisher(
                config.alert_bus.endpoint,
                hwm=config.alert_bus.hwm,
                wait_for_subscriber_ms=config.alert_bus.wait_for_subscriber_ms,
            )
        except Exception:  # noqa: BLE001 - la corrida continua sin bus
            logger.warning(
                "alert_bus: no se pudo iniciar el publisher; corrida sin bus", exc_info=True
            )
            alert_publisher = None

    try:
        with (
            JsonlSink(artifacts.pattern_events_path) as pattern_sink,
            JsonlSink(artifacts.pattern_progress_path) as progress_sink,
            JsonlSink(artifacts.alerts_path) as alert_sink,
            JsonlSink(artifacts.metrics_path) as metric_sink,
            JsonlSink(artifacts.errors_path) as error_sink,
        ):
            for _, event, error, ts_receive_ms in source:
                if error is not None:
                    error_sink.write(error)
                    errors_count += 1
                    # `line_number is None` => error de fuente, no unidad perdida.
                    if error.line_number is not None:
                        units_failed += 1
                    # Parada cooperativa (BusSource.request_stop): la fuente
                    # emite este error y termina su iteracion sola, sin
                    # excepcion. Es la unica forma de distinguir "stopped" de
                    # un fin de corrida normal.
                    if error.error_type == "BusStopRequested":
                        run_status = "stopped"
                    if progress is not None:
                        progress.errors_count = errors_count
                        progress.units_failed = units_failed
                    continue
                if event is None:
                    continue

                start = perf_counter()
                result = engine.process(event, ts_receive_ms=ts_receive_ms)
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
                for progress_record in result.progress:
                    progress_sink.write(progress_record)
                for alert in result.alerts:
                    alert_sink.write(alert)  # persiste PRIMERO
                    if alert_publisher is not None:
                        # Misma serializacion que usa JsonlSink.write (sinks/jsonl.py):
                        # model_dump(mode="json") + json.dumps(ensure_ascii=True), NO
                        # model_dump_json(exclude_none=True) — con exclude_none los
                        # campos opcionales en None desaparecerian del payload del bus
                        # pero seguirian como `null` en la linea de alerts.jsonl, y el
                        # invariante de paridad de bytes se rompe (test_alert_bus.py).
                        payload = json.dumps(
                            alert.model_dump(mode="json"), ensure_ascii=True
                        ).encode("utf-8")
                        alert_publisher.publish(
                            f"{ALERT_TOPIC_PREFIX}{control_run_id}", alert.source_id, payload
                        )
                    if (
                        alert.alert_registered_ms is not None
                        and alert.first_evidence_ms is not None
                    ):
                        ttfa_internal_ms.append(alert.alert_registered_ms - alert.first_evidence_ms)

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
                        ts_receive_ms=ts_receive_ms,
                        experiment_id=config.run.experiment_id,
                    )
                )

                if progress is not None:
                    progress.units_processed = units_processed
                    progress.pattern_events_count = pattern_events_count
                    progress.alerts_count = alerts_count
                    progress.bus_dropped_events = source.dropped_events
    except BaseException:
        # BaseException (no solo Exception): mirror del bus_status del
        # media-plane. Una parada dura (KeyboardInterrupt, SystemExit) tambien
        # debe cerrar la corrida como "failed" del lado del bus.
        run_status = "failed"
        raise
    finally:
        if alert_publisher is not None:
            try:
                alert_publisher.publish_run_finished(control_run_id, run_status)
            finally:
                alert_publisher.close()
        source.close()

    # ADR-003 regla 2: los drops del bus nunca se silencian.
    bus_dropped_events = source.dropped_events
    if bus_dropped_events:
        degradation_causes.add("bus_dropped_events")
        logger.warning("Bus: %d eventos perdidos; corrida degradada", bus_dropped_events)
    if progress is not None:
        progress.bus_dropped_events = bus_dropped_events

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
        # 1:1 (ADR-007): un unico run de medios. Si la entrada mezclo varios,
        # la corrida no es 1:1 y el campo queda None en vez de mentir.
        media_run_id=next(iter(media_run_ids)) if len(media_run_ids) == 1 else None,
        experiment_id=config.run.experiment_id,
        source=source.kind,
        bus_dropped_events=bus_dropped_events,
        scenario=config.run.scenario,
        pattern_set_id=config.patterns_file.pattern_set.id,
        active_pattern_ids=[pattern.id for pattern in active_patterns],
        units_processed=units_processed,
        units_failed=units_failed,
        pattern_events_count=pattern_events_count,
        alerts_count=alerts_count,
        errors_count=errors_count,
        avg_processing_ms=mean(processing_times) if processing_times else 0.0,
        processing_ms_percentiles=percentiles(processing_times),
        ttfa_internal_ms_percentiles=percentiles(ttfa_internal_ms),
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
