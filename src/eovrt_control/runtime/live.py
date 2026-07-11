"""Runtime live: el motor consume el bus del media-plane (spec 41 SS4, ADR-007).

La corrida es 1:1 con el run del media-plane: nace suscripta, consume hasta
`run_finished`/END y escribe los mismos artefactos que el replay.
"""

from __future__ import annotations

from pathlib import Path

from eovrt_control.config import ReplayConfig, load_replay_config
from eovrt_control.contracts.metrics import RunSummary
from eovrt_control.runtime.core import PreparedRun, RunProgress, execute_over_source, prepare_run
from eovrt_control.sources.base import MediaEventSource
from eovrt_control.sources.bus import BusSource


def build_bus_source(config: ReplayConfig, control_run_id: str) -> BusSource:
    """Construir el BusSource ES suscribirse (spec 40 SS3.2 regla 1).

    El orquestador debe llamarlo ANTES de disparar el run en el media-plane.
    """
    if config.input.bus is None:
        raise ValueError("input.bus no esta configurado")
    bus = config.input.bus
    return BusSource(
        endpoint=bus.endpoint,
        control_run_id=control_run_id,
        topics=bus.topics,
        hwm=bus.hwm,
        recv_timeout_ms=bus.recv_timeout_ms,
        idle_timeout_s=bus.idle_timeout_s,
        poll_url=bus.finish.poll_url,
        poll_interval_s=bus.finish.poll_interval_s,
    )


def run_live_from_config(
    config: ReplayConfig,
    *,
    source: MediaEventSource | None = None,
    prepared: PreparedRun | None = None,
    progress: RunProgress | None = None,
) -> RunSummary:
    """Corre el motor contra el bus sobre una config ya cargada.

    `source` inyectada permite suscribirse ANTES de disparar el run del
    media-plane (es lo que hace el servicio) y testear sin red.
    """
    prepared = prepared or prepare_run(config)
    if source is None:
        if config.input.type != "bus":
            raise ValueError(f"run_live requiere input.type='bus', no {config.input.type!r}")
        source = build_bus_source(config, prepared.control_run_id)
    return execute_over_source(
        config=config,
        source=source,
        control_run_id=prepared.control_run_id,
        artifacts=prepared.artifacts,
        active_patterns=prepared.active_patterns,
        progress=progress,
    )


def run_live(
    config_path: str | Path, *, source: MediaEventSource | None = None
) -> RunSummary:
    return run_live_from_config(load_replay_config(config_path), source=source)
