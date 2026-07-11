"""Orquestacion de replay DBE del plano de control (spec 41 SS3: `JsonlSource`)."""

from __future__ import annotations

from pathlib import Path

from eovrt_control.config import ReplayConfig, load_replay_config
from eovrt_control.contracts.metrics import RunSummary
from eovrt_control.runtime.core import PreparedRun, RunProgress, execute_over_source, prepare_run
from eovrt_control.sources.jsonl import JsonlSource


def run_replay_from_config(
    config: ReplayConfig,
    *,
    prepared: PreparedRun | None = None,
    progress: RunProgress | None = None,
) -> RunSummary:
    """Corre un replay sobre una config ya cargada (el camino del servicio)."""
    if config.input.type != "media_jsonl":
        raise ValueError(f"run_replay requiere input.type='media_jsonl', no {config.input.type!r}")
    prepared = prepared or prepare_run(config)
    # `JsonlSource` emite el error de archivo inexistente como item de fuente;
    # el core lo escribe en errors.jsonl sin contarlo como unidad fallida.
    source = JsonlSource(config.resolve_path(config.input.path), prepared.control_run_id)
    return execute_over_source(
        config=config,
        source=source,
        control_run_id=prepared.control_run_id,
        artifacts=prepared.artifacts,
        active_patterns=prepared.active_patterns,
        progress=progress,
    )


def run_replay(config_path: str | Path) -> RunSummary:
    return run_replay_from_config(load_replay_config(config_path))
