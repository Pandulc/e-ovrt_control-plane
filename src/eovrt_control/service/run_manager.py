"""RunManager del control-plane: un run activo por vez (ADR-008)."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
import zmq

from eovrt_control.config import ReplayConfig, load_replay_config, load_replay_config_data
from eovrt_control.runtime.core import PreparedRun, RunProgress, prepare_run
from eovrt_control.runtime.live import build_bus_source, run_live_from_config
from eovrt_control.runtime.replay import run_replay_from_config
from eovrt_control.service.run_ids import new_control_run_id
from eovrt_control.service.run_request import ControlRunRequest
from eovrt_control.service.settings import ServiceSettings
from eovrt_control.sources.base import MediaEventSource

logger = logging.getLogger(__name__)

_MODE_TO_INPUT_TYPE = {"replay": "media_jsonl", "live": "bus"}


class RunBusyError(RuntimeError):
    def __init__(self, active_run_id: str) -> None:
        super().__init__(f"Ya hay un run activo: {active_run_id}")
        self.active_run_id = active_run_id


class UnknownRunError(KeyError):
    pass


class InvalidRunConfigError(ValueError):
    """La config de la corrida no se pudo cargar o preparar: es un error del CLIENTE.

    Hereda de `ValueError` a proposito, para que el `except (ValueError,
    FileNotFoundError)` del router la siga mapeando a 422 sin ensancharse a
    `Exception` (eso convertiria bugs propios en 422 tambien).
    """


@dataclass
class ActiveRun:
    control_run_id: str
    mode: str
    config: ReplayConfig
    prepared: PreparedRun
    source: MediaEventSource | None
    progress: RunProgress
    started_at: datetime
    thread: threading.Thread | None = None
    status: str = "running"
    error: str | None = None
    finished: threading.Event = field(default_factory=threading.Event)


class RunManager:
    def __init__(self, settings: ServiceSettings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._active: ActiveRun | None = None
        self._last_run_id: str | None = None

    # --- API ---

    def start_run(self, request: ControlRunRequest) -> str:
        with self._lock:
            if self._active is not None:
                raise RunBusyError(self._active.control_run_id)

            # Solo este tramo (cargar/preparar la config y suscribirse al bus) se
            # traduce a `InvalidRunConfigError`: son excepciones de LIBRERIA que
            # significan "la config del cliente no sirve", no bugs nuestros.
            # `ValueError` propio (mode vs input.type, validacion de pydantic) pasa
            # sin tocar porque `InvalidRunConfigError` ya hereda de `ValueError` y el
            # router no distingue subclases.
            try:
                config = self._load_config(request)
                expected = _MODE_TO_INPUT_TYPE[request.mode]
                if config.input.type != expected:
                    raise ValueError(
                        f"mode={request.mode!r} requiere input.type={expected!r}, "
                        f"no {config.input.type!r}"
                    )

                config.run.id = new_control_run_id(config)
                # Revision final, hallazgo 1: reusar un `run.id` de una corrida ya
                # terminada truncaria sus `.jsonl` (los sinks abren en modo "w") y,
                # si la nueva corrida falla a mitad de camino, `get()` seguiria
                # devolviendo el `summary.json` VIEJO (succeeded) con artefactos
                # parciales de la nueva. Se rechaza ANTES de tocar nada en disco: no
                # se borra el `summary.json` viejo, es evidencia de la corrida
                # anterior.
                if (self._run_dir(config.run.id) / "summary.json").exists():
                    raise InvalidRunConfigError(
                        f"Ya existe una corrida terminada con run.id={config.run.id!r}. "
                        f"Usa otro run.id o omitilo para que se genere uno unico."
                    )
                prepared = prepare_run(config)

                source: MediaEventSource | None = None
                if request.mode == "live":
                    # Construir el BusSource ES suscribirse (spec 40 SS3.2 regla 1).
                    # Ocurre ACA, antes de que el handler devuelva 201, porque el
                    # orquestador dispara el media-plane recien despues de esa
                    # respuesta.
                    source = build_bus_source(config, prepared.control_run_id)
            except (yaml.YAMLError, zmq.ZMQError, OSError) as exc:
                # `FileNotFoundError` es un `OSError` y ya mapeaba a 422; traducirlo
                # tambien es inocuo y uniforma el mensaje con el resto de esta lista.
                raise InvalidRunConfigError(
                    f"No se pudo cargar/preparar la config: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            active = ActiveRun(
                control_run_id=prepared.control_run_id,
                mode=request.mode,
                config=config,
                prepared=prepared,
                source=source,
                progress=RunProgress(),
                started_at=datetime.now(tz=UTC),
            )
            self._active = active
            self._last_run_id = active.control_run_id

        thread = threading.Thread(
            target=self._execute, args=(active,), daemon=True, name="control-run-executor"
        )
        active.thread = thread
        thread.start()
        return active.control_run_id

    def get(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            active = self._active
        if active is not None and active.control_run_id == run_id:
            return self._describe_active(active)

        summary_path = self._run_dir(run_id) / "summary.json"
        if not summary_path.exists():
            raise UnknownRunError(run_id)
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # Summary truncado (kill a mitad de escritura) o borrado: el run no es legible.
            logger.warning("summary ilegible para %s: %s", run_id, exc)
            raise UnknownRunError(run_id) from exc
        return {
            "control_run_id": run_id,
            # Si hay summary.json, la corrida llego al final del bucle: succeeded.
            # Una corrida que exploto no lo escribe, y `get` la reporta como desconocida.
            "status": "succeeded",
            "live": False,
            "summary": summary,
            "output_files": summary.get("output_files", {}),
            # Revision final, hallazgo 3: aditivo, para que un poller que solo mira
            # el nivel superior (no `summary`) vea la degradacion. No toca el
            # vocabulario de `status`.
            "degraded": summary.get("degraded", False),
            "degradation_causes": summary.get("degradation_causes", []),
        }

    def current(self) -> dict[str, Any]:
        with self._lock:
            active = self._active
        if active is None:
            raise UnknownRunError("no hay run activo")
        return self._describe_active(active)

    def alerts(self, run_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        run_dir = self._run_dir(run_id)
        if not run_dir.is_dir():
            raise UnknownRunError(run_id)
        alerts_path = run_dir / "alerts.jsonl"
        if not alerts_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with alerts_path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    # Linea corrupta (kill a mitad de escritura): se saltea, no
                    # se rompe el endpoint entero por una sola linea ilegible.
                    logger.warning(
                        "alerta ilegible en %s linea %d: %s", run_id, line_no, exc
                    )
        if limit is not None:
            rows = rows[: max(limit, 0)]
        return rows

    def pattern_progress(self, run_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        run_dir = self._run_dir(run_id)
        if not run_dir.is_dir():
            raise UnknownRunError(run_id)
        path = run_dir / "pattern_progress.jsonl"
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    # Linea corrupta (kill a mitad de escritura): se saltea, no
                    # se rompe el endpoint entero por una sola linea ilegible.
                    logger.warning(
                        "progreso ilegible en %s linea %d: %s", run_id, line_no, exc
                    )
        if limit is not None:
            rows = rows[: max(limit, 0)]
        return rows

    def list_runs(self, media_run_id: str | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        runs_dir = self._settings.runs_dir
        if not runs_dir.is_dir():
            return rows
        for run_dir in runs_dir.iterdir():
            summary_path = run_dir / "summary.json"
            if not summary_path.is_file():
                continue
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("summary ilegible en %s: %s", run_dir.name, exc)
                continue
            if media_run_id is not None and summary.get("media_run_id") != media_run_id:
                continue
            rows.append(
                {
                    "control_run_id": run_dir.name,
                    "status": summary.get("status"),
                    "started_at": summary.get("started_at"),
                    "alerts_count": summary.get("alerts_count"),
                    "media_run_id": summary.get("media_run_id"),
                }
            )
        rows.sort(key=lambda r: r.get("started_at") or "", reverse=True)
        return rows

    def received_units(self, run_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        run_dir = self._run_dir(run_id)
        if not run_dir.is_dir():
            raise UnknownRunError(run_id)
        path = run_dir / "metrics.jsonl"
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    sample = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "metrica ilegible en %s linea %d: %s", run_id, line_no, exc
                    )
                    continue
                unit_id = sample.get("unit_id")
                if unit_id is not None:
                    rows.append({"unit_id": unit_id})
        if limit is not None:
            rows = rows[: max(limit, 0)]
        return rows

    def effective_config(self) -> dict[str, Any]:
        with self._lock:
            active = self._active
            last_run_id = self._last_run_id
        run_id = active.control_run_id if active is not None else last_run_id
        if run_id is None:
            raise UnknownRunError("todavia no hubo ninguna corrida")
        path = self._run_dir(run_id) / "effective_config.yaml"
        if not path.exists():
            raise UnknownRunError(run_id)
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def join_active(self, timeout: float) -> None:
        with self._lock:
            active = self._active
        if active is not None:
            active.finished.wait(timeout=timeout)

    def shutdown(self) -> None:
        """Pide la parada de la corrida activa. NO cierra el socket desde este hilo:
        eso es un uso multi-hilo que libzmq aborta con SIGABRT. La fuente ve la
        bandera entre polls y cierra su propio socket, en su propio hilo."""
        with self._lock:
            active = self._active
        if active is not None and active.source is not None:
            active.source.request_stop()

    # --- interno ---

    def _run_dir(self, run_id: str) -> Path:
        return self._settings.runs_dir / run_id

    def _load_config(self, request: ControlRunRequest) -> ReplayConfig:
        if request.config_path is not None:
            config = load_replay_config(request.config_path)
        else:
            data = dict(request.config or {})
            # Pre-seteo requerido por `load_replay_config_data`: valida que
            # `outputs.base_dir` sea absoluto cuando no hay YAML de respaldo.
            data["outputs"] = {**data.get("outputs", {}),
                               "base_dir": str(self._settings.runs_dir)}
            config = load_replay_config_data(data)
        # El dueno del `runs_dir` es el DESPLIEGUE, no el experimento (ADR-009 SS2).
        # Se fuerza en las DOS ramas, despues de cargar la config: en la rama por
        # referencia esto pisa a proposito el `outputs.base_dir` (o el default
        # relativo) que traiga el YAML, porque el servicio decide donde viven
        # sus corridas, no el experimento.
        config.outputs.base_dir = str(self._settings.runs_dir)
        if request.experiment_id is not None:
            config.run.experiment_id = request.experiment_id
        return config

    def _describe_active(self, active: ActiveRun) -> dict[str, Any]:
        return {
            "control_run_id": active.control_run_id,
            "status": active.status,
            "mode": active.mode,
            "live": True,
            "started_at": active.started_at.isoformat(),
            "experiment_id": active.config.run.experiment_id,
            "error": active.error,
            # Invariante de ADR/spec: en `live`, al devolver 201 el BusSource ya
            # existe, y construirlo ES suscribirse. Lo exponemos para que un test
            # pueda verificar el ORDEN, que es lo que importa (el timing de red no
            # es observable de forma determinista).
            # En `replay` el `source` se construye dentro del runtime (no en
            # `start_run`), asi que aca siempre da `False`: el campo solo tiene
            # sentido para `live`.
            "subscribed": active.source is not None,
            "progress": {
                "units_processed": active.progress.units_processed,
                "units_failed": active.progress.units_failed,
                "errors_count": active.progress.errors_count,
                "pattern_events_count": active.progress.pattern_events_count,
                "alerts_count": active.progress.alerts_count,
                "bus_dropped_events": active.progress.bus_dropped_events,
            },
        }

    def _execute(self, active: ActiveRun) -> None:
        status, error = "succeeded", None
        try:
            if active.mode == "live":
                run_live_from_config(
                    active.config,
                    source=active.source,
                    prepared=active.prepared,
                    progress=active.progress,
                )
            else:
                run_replay_from_config(
                    active.config, prepared=active.prepared, progress=active.progress
                )
        except Exception as exc:  # noqa: BLE001 — el estado failed captura la causa
            status, error = "failed", str(exc)
            logger.exception("Corrida %s fallo", active.control_run_id)
        except BaseException as exc:
            # No hereda de Exception (SystemExit, KeyboardInterrupt, GeneratorExit):
            # se registra el estado igual, pero se relanza, no se traga.
            status, error = "failed", str(exc)
            logger.error(
                "Corrida %s fallo con %s no recuperable", active.control_run_id, type(exc).__name__
            )
            raise
        finally:
            active.status = status
            active.error = error
            # ORDEN IMPORTANTE: soltar el slot ANTES de marcar `finished`. Al reves,
            # un `get()` inmediatamente despues de `join_active()` todavia veria el run
            # como activo y devolveria el estado en vivo en vez del summary del disco.
            with self._lock:
                self._active = None
            active.finished.set()
