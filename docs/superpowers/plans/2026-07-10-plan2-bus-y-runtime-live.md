# Plan 2 — `MediaEventSource`, bus ZeroMQ y runtime live

> **EJECUTADO el 2026-07-10.** Las 7 tareas están completas. Resultados, evidencia y deuda:
> `docs/operacion/37-plan2-bus-y-live-resultados.md` (repo `docs`).
>
> **El código de referencia de este plan tenía defectos reales**, hallados por la revisión
> adversarial por tarea y corregidos durante la ejecución. **No lo copies verbatim**: el código
> vigente es el del working tree. Los defectos, todos sobre garantías no negociables:
> `publish()`/`close()`/la construcción del `BusPublisher` podían propagar excepciones y matar la
> corrida; `run_node_b` no garantizaba el `run_finished`; `_check_seq` retrocedía el contador ante
> un `seq` duplicado e inflaba `dropped_events` (siendo la única señal de pérdida del sistema);
> cuatro caminos por los que un mensaje malformado mataba la corrida; `_drain()` sin cota; y la
> byte-compatibilidad no tenía test que la sostuviera. Ver doc 37 §6.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el motor de patrones consuma eventos del media-plane por un bus ZeroMQ
en vivo con la misma semántica que el replay desde archivo, y que la corrida live
cierre 1:1 con el run del media-plane.

**Architecture:** Se interpone una interfaz `MediaEventSource` (iterador de
`event | error | END`) entre el motor y su entrada; `JsonlSource` y `MemorySource` la
implementan primero (refactor puro, sin cambio de comportamiento), y `BusSource`
después. Del lado del media-plane, un `BusPublishingArtifactWriter` decora el
`RunArtifactWriter` y publica cada `DetectionEvent` ya persistido dentro de un envelope
msgpack `bus.envelope.v1` por un socket ZeroMQ XPUB, más un `run.lifecycle.v1 /
run_finished` al cerrar. El JSONL sigue siendo la verdad: el publicador nunca bloquea
la inferencia y el consumidor detecta pérdidas por huecos en `seq`.

**Tech Stack:** Python 3.11+, Pydantic v2, pyzmq (XPUB/SUB), msgpack, pytest, ruff.
Dos repos hermanos: `e-ovrt_media-plane` (publicador) y `e-ovrt_control-plane`
(consumidor). **No se importan entre sí**: el envelope se implementa por duplicado y
el contrato de wire se pinea con tests en ambos lados.

## Global Constraints

Copiados textualmente de los specs y del handoff (`docs/operacion/36`):

- **Nunca commitear sin pedido explícito del usuario en ese turno.** Los pasos de este
  plan que dicen "Commit" preparan el commit; se ejecutan sólo si el usuario lo pide en
  ese turno. Si no lo pidió, dejar el árbol de trabajo limpio de `git commit` y avisar.
- **Nunca agregar `Co-Authored-By`** a un commit.
- **Nada en GitHub; todo local.** No crear remotes ni pushear.
- **Contratos SIEMPRE aditivos, sin bump de `schema_version`** (spec 40 §1).
- `bus.envelope.v1` (spec 40 §3.1), exactamente estos campos y nombres:
  `{schema_version, topic, key, seq, ts_publish_ms, payload}`.
- `topic = "media.detection.v1.<media_run_id>"` para detecciones y
  `"run.lifecycle.v1.<media_run_id>"` para el cierre. `key = source_id`.
- `payload` es **byte-compatible con la línea JSONL**: exactamente
  `event.model_dump_json(exclude_none=True).encode("utf-8")` (spec 40 §3.1).
- `seq`: contador **por publicador**, monótono **desde 0**, incrementado **aunque el
  envío se descarte** — así el consumidor ve el hueco.
- **El publicador nunca bloquea la inferencia**: HWM finito, `zmq.NOBLOCK`, drop antes
  que frenar (spec 40 §3.2 regla 3).
- **El consumidor se suscribe ANTES de que el run se dispare** (spec 40 §3.2 regla 1).
- **Huecos en `seq` ⇒ `bus_dropped_events > 0` ⇒ corrida degradada con causa. Nunca se
  silencia** (spec 40 §3.2 regla 2).
- `ruff` con `line-length = 100` en ambos repos; `target-version = "py311"`.
- Comentarios y docstrings en español, sin tildes en los identificadores, siguiendo el
  estilo del código existente (los docstrings existentes mezclan; imitar el archivo
  vecino).
- **Gate global (líneas base MEDIDAS el 2026-07-10, no supuestas):**
  - control-plane: `pytest -q --ignore=tests/labs` → **57 passed**. Debe terminar en
    57 + los tests nuevos. `tests/labs/` falla por `numpy` ausente (conocido, no
    bloqueante) — siempre correr con `--ignore=tests/labs`.
  - media-plane: `pytest -q` → **440 passed, 1 warning**. Debe terminar en 440 + los
    nuevos, sin fallas.
- **Cada repo tiene su propio venv** y `python3` del sistema NO tiene `pydantic`. Correr
  todo con el intérprete del venv: `/home/simonll4/projects/e-ovrt_control-plane/.venv/bin/python`
  y `/home/simonll4/projects/e-ovrt_media-plane/.venv/bin/python` (o `source .venv/bin/activate`
  primero). Donde el plan escribe `python3 -m pytest`, léase el python del venv del repo
  en el que estás parado.

## File Structure

**`e-ovrt_control-plane`:**

| Archivo | Responsabilidad |
|---|---|
| `src/eovrt_control/sources/base.py` (nuevo) | Interfaz `MediaEventSource`, tipo `SourceItem`. |
| `src/eovrt_control/sources/jsonl.py` (nuevo) | `JsonlSource`: lee `detections.jsonl`. |
| `src/eovrt_control/sources/memory.py` (nuevo) | `MemorySource`: lista en memoria, para tests. |
| `src/eovrt_control/sources/media_jsonl.py` (modificado) | Sólo el alias de compat `iter_media_jsonl`. |
| `src/eovrt_control/sources/bus.py` (nuevo) | `BusSource`: SUB de ZeroMQ, huecos de `seq`, cierre por lifecycle o polling. |
| `src/eovrt_control/runtime/core.py` (nuevo) | Bucle motor↔fuente + summary. Es el cuerpo que hoy vive dentro de `run_replay`. |
| `src/eovrt_control/runtime/replay.py` (modificado) | Sólo arma `JsonlSource` y delega en `core`. |
| `src/eovrt_control/runtime/live.py` (nuevo) | Arma `BusSource` y delega en `core`. |
| `src/eovrt_control/config.py` (modificado) | `input.type: bus`, `input.bus`, `run.experiment_id`. |
| `src/eovrt_control/contracts/metrics.py` (modificado) | Campos aditivos del `RunSummary`. |
| `src/eovrt_control/cli.py` (modificado) | Subcomando `live`. |
| `pyproject.toml` (modificado) | deps `pyzmq`, `msgpack`. |
| `configs/live_ebe_cr01_cr02.yaml` (nuevo) | Config de ejemplo de corrida live. |
| `tests/test_sources.py` (nuevo) | `JsonlSource` / `MemorySource` / alias. |
| `tests/test_bus_source.py` (nuevo) | `BusSource` contra un publicador de prueba. |
| `tests/test_live.py` (nuevo) | `run_live` sobre `MemorySource`; degradación por drops. |
| `tests/test_bus_parity.py` (nuevo) | **Gate**: paridad replay↔stream. |

**`e-ovrt_media-plane`:**

| Archivo | Responsabilidad |
|---|---|
| `src/eovrt_media/transport/bus.py` (nuevo) | `encode_envelope` + `BusPublisher` (XPUB). |
| `src/eovrt_media/service/bus_writer.py` (nuevo) | `BusPublishingArtifactWriter`. |
| `src/eovrt_media/config/schemas.py` (modificado) | `BusConfig` + `RunConfig.bus`. |
| `src/eovrt_media/service/run_request.py` (modificado) | `BusSpec` en el request, traducción a raw. |
| `src/eovrt_media/runtime/pipeline.py` (modificado) | Cableado del writer + `run_finished`. |
| `src/eovrt_media/runtime/two_node.py` (modificado) | Ídem en el Nodo B. |
| `tests/test_bus_publisher.py` (nuevo) | Envelope, `seq`, entrega a un SUB real. |
| `tests/test_bus_writer.py` (nuevo) | N detecciones ⇒ N envelopes con payload == línea JSONL; lifecycle. |

**Fuera de alcance de este plan** (agendados, no implementar):
tracker/`track_id` (spec 42 §3), servicio mínimo del control-plane (spec 41 §5),
instrumentación `t_capture→alert` (spec 40 §5.2), publisher de alertas
`control.alert.v1.*` (spec 45), `experiment_id` en el `POST /api/runs` del media-plane
(spec 42 §4.1 — su propio gate). Sí entra `run.experiment_id` en la config del
control-plane porque el summary live lo exige (spec 41 §4).

---

## Task 1: `MediaEventSource`, `JsonlSource` y `MemorySource`

Refactor puro del lado del control-plane. Al terminar, `run_replay` consume una
`MediaEventSource` y no sabe de archivos.

**Files:**
- Create: `src/eovrt_control/sources/base.py`
- Create: `src/eovrt_control/sources/jsonl.py`
- Create: `src/eovrt_control/sources/memory.py`
- Modify: `src/eovrt_control/sources/media_jsonl.py` (reemplazo completo)
- Test: `tests/test_sources.py`

**Interfaces:**
- Produces:
  - `SourceItem = tuple[int, DetectionEvent | None, ErrorEvent | None]`
  - `class MediaEventSource(ABC)` con `kind: str`, `__iter__() -> Iterator[SourceItem]`,
    `close() -> None`, `dropped_events -> int` (propiedad, default 0).
  - `class JsonlSource(MediaEventSource)`: `__init__(self, path: Path, control_run_id: str)`.
  - `class MemorySource(MediaEventSource)`: `__init__(self, events: Iterable[DetectionEvent])`.
  - `iter_media_jsonl(path, control_run_id)` sigue existiendo como alias.

**Convención clave** (la usa Task 2): en un `ErrorEvent` producido por una fuente,
`line_number is None` significa **error de fuente** (archivo inexistente, transporte
caído): cuenta en `errors_count` pero **no** en `units_failed`. `line_number is not
None` significa **unidad que falló el parseo**: cuenta en ambos. Hoy `run_replay`
distingue estos dos casos a mano; la convención los unifica sin cambiar los números.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_sources.py`:

```python
import json

import pytest

from eovrt_control.contracts.media import DetectionEvent
from eovrt_control.sources.base import MediaEventSource
from eovrt_control.sources.jsonl import JsonlSource
from eovrt_control.sources.media_jsonl import iter_media_jsonl
from eovrt_control.sources.memory import MemorySource


def _event(unit_id: str = "unit-1") -> dict:
    return {
        "run_id": "media-run",
        "unit_id": unit_id,
        "source": {
            "source_id": "image.jpg",
            "source_type": "image",
            "width": 640,
            "height": 480,
        },
        "model": {"name": "mock", "device": "cpu"},
        "prompts": {"prompt_set_id": "cr01_cr02_v1"},
        "detections": [],
    }


def test_jsonl_source_yields_events_with_line_numbers(tmp_path) -> None:
    path = tmp_path / "detections.jsonl"
    path.write_text(
        json.dumps(_event("unit-1")) + "\n" + json.dumps(_event("unit-2")) + "\n",
        encoding="utf-8",
    )

    items = list(JsonlSource(path, "control-run"))

    assert [line for line, _, _ in items] == [1, 2]
    assert [event.unit_id for _, event, _ in items] == ["unit-1", "unit-2"]
    assert all(error is None for _, _, error in items)


def test_jsonl_source_skips_blank_lines(tmp_path) -> None:
    path = tmp_path / "detections.jsonl"
    path.write_text(json.dumps(_event()) + "\n\n   \n", encoding="utf-8")

    items = list(JsonlSource(path, "control-run"))

    assert len(items) == 1


def test_jsonl_source_reports_unparseable_line_as_unit_error(tmp_path) -> None:
    path = tmp_path / "detections.jsonl"
    path.write_text("{no es json}\n", encoding="utf-8")

    (line_number, event, error) = next(iter(JsonlSource(path, "control-run")))

    assert line_number == 1
    assert event is None
    # line_number presente => unidad fallida (cuenta en units_failed).
    assert error.line_number == 1
    assert error.error_type == "JSONDecodeError"
    assert error.control_run_id == "control-run"


def test_jsonl_source_reports_invalid_schema_as_unit_error(tmp_path) -> None:
    path = tmp_path / "detections.jsonl"
    path.write_text(json.dumps({"run_id": "media-run"}) + "\n", encoding="utf-8")

    (_, event, error) = next(iter(JsonlSource(path, "control-run")))

    assert event is None
    assert error.error_type == "ValidationError"
    assert error.line_number == 1


def test_jsonl_source_reports_missing_file_as_source_error(tmp_path) -> None:
    path = tmp_path / "no_existe.jsonl"

    items = list(JsonlSource(path, "control-run"))

    assert len(items) == 1
    (_, event, error) = items[0]
    assert event is None
    assert error.error_type == "FileNotFoundError"
    # line_number ausente => error de FUENTE, no cuenta en units_failed.
    assert error.line_number is None


def test_jsonl_source_declares_kind_and_zero_drops(tmp_path) -> None:
    path = tmp_path / "detections.jsonl"
    path.write_text(json.dumps(_event()) + "\n", encoding="utf-8")
    source = JsonlSource(path, "control-run")

    assert isinstance(source, MediaEventSource)
    assert source.kind == "jsonl"
    assert source.dropped_events == 0
    source.close()


def test_memory_source_yields_events_in_order() -> None:
    events = [DetectionEvent.model_validate(_event("a")), DetectionEvent.model_validate(_event("b"))]

    items = list(MemorySource(events))

    assert [index for index, _, _ in items] == [1, 2]
    assert [event.unit_id for _, event, _ in items] == ["a", "b"]
    assert MemorySource(events).kind == "memory"


def test_iter_media_jsonl_alias_delegates_to_jsonl_source(tmp_path) -> None:
    path = tmp_path / "detections.jsonl"
    path.write_text(json.dumps(_event()) + "\n", encoding="utf-8")

    items = list(iter_media_jsonl(path, "control-run"))

    assert len(items) == 1
    assert items[0][1].unit_id == "unit-1"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/simonll4/projects/e-ovrt_control-plane
python3 -m pytest tests/test_sources.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'eovrt_control.sources.base'`.

- [ ] **Step 3: Write `sources/base.py`**

```python
"""Interfaz unica de fuentes de eventos del plano de medios (spec 41 SS3).

Una fuente es un iterador de `event | error | END`, donde END es el agotamiento
del iterador. Tres implementaciones: `JsonlSource` (DBE/replay), `MemorySource`
(tests deterministas) y `BusSource` (EBE/live).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from eovrt_control.contracts.errors import ErrorEvent
from eovrt_control.contracts.media import DetectionEvent

# (indice, evento, error): exactamente uno de los dos ultimos no es None.
# El indice es el numero de linea (JSONL) o el `seq` del envelope (bus).
SourceItem = tuple[int, DetectionEvent | None, ErrorEvent | None]


class MediaEventSource(ABC):
    """Iterador de eventos del plano de medios.

    Convencion de errores: un `ErrorEvent` con `line_number is None` es un error
    de FUENTE (archivo inexistente, transporte caido) y no representa una unidad
    perdida; con `line_number` presente es una unidad que fallo el parseo.
    """

    kind: str = "unknown"

    @abstractmethod
    def __iter__(self) -> Iterator[SourceItem]:
        """Emite items hasta END (agotamiento)."""

    def close(self) -> None:
        """Libera recursos. Idempotente. Default: nada que liberar."""
        return None

    @property
    def dropped_events(self) -> int:
        """Eventos que la fuente sabe que se perdieron en transito."""
        return 0
```

- [ ] **Step 4: Write `sources/jsonl.py`**

```python
"""Lectura de eventos JSONL del plano de medios (DBE / replay)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from pydantic import ValidationError

from eovrt_control.contracts.errors import ErrorEvent
from eovrt_control.contracts.media import DetectionEvent
from eovrt_control.sources.base import MediaEventSource, SourceItem


class JsonlSource(MediaEventSource):
    """Fuente sobre `detections.jsonl` del media-plane."""

    kind = "jsonl"

    def __init__(self, path: Path, control_run_id: str) -> None:
        self.path = Path(path)
        self.control_run_id = control_run_id

    def __iter__(self) -> Iterator[SourceItem]:
        if not self.path.exists():
            # Error de FUENTE: sin `line_number`, para que el runtime lo cuente
            # en errors_count pero no en units_failed (no hubo unidad).
            yield (
                0,
                None,
                ErrorEvent(
                    control_run_id=self.control_run_id,
                    message=f"Archivo de entrada no encontrado: {self.path}",
                    error_type="FileNotFoundError",
                ),
            )
            return

        with self.path.open("r", encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                    yield line_number, DetectionEvent.model_validate(data), None
                except (json.JSONDecodeError, ValidationError) as exc:
                    yield (
                        line_number,
                        None,
                        ErrorEvent(
                            control_run_id=self.control_run_id,
                            message=str(exc),
                            error_type=type(exc).__name__,
                            line_number=line_number,
                        ),
                    )
```

- [ ] **Step 5: Write `sources/memory.py`**

```python
"""Fuente en memoria: tests deterministas del motor sin tocar disco ni red."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from eovrt_control.contracts.media import DetectionEvent
from eovrt_control.sources.base import MediaEventSource, SourceItem


class MemorySource(MediaEventSource):
    kind = "memory"

    def __init__(self, events: Iterable[DetectionEvent]) -> None:
        self._events = list(events)

    def __iter__(self) -> Iterator[SourceItem]:
        for index, event in enumerate(self._events, start=1):
            yield index, event, None
```

- [ ] **Step 6: Replace `sources/media_jsonl.py` with the compat alias**

Reemplazar el archivo entero por:

```python
"""Alias de compatibilidad. La implementacion vive en `sources/jsonl.py`.

`iter_media_jsonl` se conserva porque hay configs y codigo externo que la
importan por nombre (spec 41 SS3: "renombre ... (compat: alias)").
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from eovrt_control.sources.base import SourceItem
from eovrt_control.sources.jsonl import JsonlSource

__all__ = ["JsonlSource", "iter_media_jsonl"]


def iter_media_jsonl(path: Path, control_run_id: str) -> Iterator[SourceItem]:
    yield from JsonlSource(path, control_run_id)
```

- [ ] **Step 7: Run the new tests**

```bash
python3 -m pytest tests/test_sources.py -q
```

Expected: PASS (8 passed).

- [ ] **Step 8: Run the full suite — the replay must be intact**

```bash
python3 -m pytest -q --ignore=tests/labs
```

Expected: `65 passed` (57 previos + 8 nuevos). Si `test_missing_input_...` falla, es
porque `run_replay` todavía chequea `input_path.exists()` por su cuenta y ahora el
error se duplica: **no tocar `run_replay` en esta task**, todavía usa
`iter_media_jsonl` sobre un archivo que ya validó. El alias no cambia esa ruta.

- [ ] **Step 9: Lint**

```bash
python3 -m ruff check src tests
```

Expected: `All checks passed!`

- [ ] **Step 10: Commit (sólo si el usuario lo pidió en este turno)**

```bash
git add src/eovrt_control/sources tests/test_sources.py
git commit -m "refactor(sources): interfaz MediaEventSource con JsonlSource y MemorySource"
```

---

## Task 2: `runtime/core.py` — el bucle motor↔fuente, agnóstico de la entrada

Extrae el cuerpo de `run_replay` a una función que recibe una `MediaEventSource`.
`run_replay` queda como constructor de `JsonlSource`. Es el paso que hace posible
`run_live` sin duplicar el bucle. **Sin cambio de comportamiento observable** salvo los
tres campos aditivos del summary.

**Files:**
- Create: `src/eovrt_control/runtime/core.py`
- Modify: `src/eovrt_control/runtime/replay.py` (reemplazo completo)
- Modify: `src/eovrt_control/contracts/metrics.py:33-56` (campos aditivos)
- Modify: `src/eovrt_control/config.py:12-17` (`RunSection.experiment_id`)
- Test: `tests/test_replay.py` (agregar 2 tests al final)

**Interfaces:**
- Consumes: `MediaEventSource`, `JsonlSource` (Task 1).
- Produces:
  - `runtime.core.control_run_id(config: ReplayConfig) -> str`
  - `runtime.core.execute_over_source(*, config: ReplayConfig, source: MediaEventSource,
    control_run_id: str, artifacts: RunArtifacts, active_patterns:
    list[PatternDefinition]) -> RunSummary`
  - `RunSummary` gana `media_run_id: str | None`, `experiment_id: str | None`,
    `source: str = "jsonl"`, `bus_dropped_events: int = 0`.
  - `RunSection` gana `experiment_id: str | None = None`.

- [ ] **Step 1: Write the failing tests**

Agregar al final de `tests/test_replay.py`:

```python
def test_replay_summary_declares_jsonl_source_and_media_run_id(tmp_path) -> None:
    """Spec 41 SS4: el summary declara de que fuente vino la corrida."""
    summary = run_replay(_write_config(tmp_path))

    assert summary.source == "jsonl"
    assert summary.bus_dropped_events == 0
    assert summary.media_run_id == summary.media_run_ids[0]


def test_replay_summary_carries_experiment_id(tmp_path) -> None:
    """Spec 41 SS8.1: experiment_id viaja de la config al summary."""
    config_path = _write_config(tmp_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["run"]["experiment_id"] = "exp-42"
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    summary = run_replay(config_path)

    assert summary.experiment_id == "exp-42"
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_replay.py -q -k "declares_jsonl_source or experiment_id"
```

Expected: FAIL — `AttributeError: 'RunSummary' object has no attribute 'source'`.

- [ ] **Step 3: Add the additive `RunSummary` fields**

En `src/eovrt_control/contracts/metrics.py`, dentro de `class RunSummary`, **después de**
`degradation_causes` y **antes de** `pattern_evaluation`:

```python
    # Aditivos (spec 41 SS4): trazabilidad de la corrida live 1:1. `media_run_id`
    # es el run del media-plane con el que esta corrida es 1:1 (None si la
    # entrada mezclo varios). `source` declara por que camino llegaron los
    # eventos. `bus_dropped_events` > 0 implica corrida degradada (ADR-003).
    media_run_id: str | None = None
    experiment_id: str | None = None
    source: str = "jsonl"
    bus_dropped_events: int = 0
```

- [ ] **Step 4: Add `experiment_id` to `RunSection`**

En `src/eovrt_control/config.py`:

```python
class RunSection(BaseModel):
    id: str | None = None
    scenario: str = "DBE"
    name: str = "control_replay"
    description: str | None = None
    # ADR-004: clave de la corrida paraguas; viaja al summary y a los eventos.
    experiment_id: str | None = None
```

- [ ] **Step 5: Write `runtime/core.py`**

Es el cuerpo actual de `run_replay` (`runtime/replay.py:26-217`), movido tal cual, con
la fuente inyectada. Contenido completo:

```python
"""Bucle motor<->fuente, agnostico de la entrada (spec 41 SS3-SS4).

`run_replay` y `run_live` son constructores de fuente; el trabajo real -y la
semantica del summary- vive aca, una sola vez. Es la condicion del test de
paridad replay<->stream (spec 40 SS3.4).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from statistics import mean
from time import perf_counter

from eovrt_control.config import PatternDefinition, ReplayConfig
from eovrt_control.contracts.metrics import ApplicabilityState, ControlMetricSample, RunSummary
from eovrt_control.engine.pattern_engine import PatternEngine
from eovrt_control.sinks.alerts_csv import export_alert_details_csv
from eovrt_control.sinks.artifacts import RunArtifacts
from eovrt_control.sinks.jsonl import JsonlSink
from eovrt_control.sources.base import MediaEventSource

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def control_run_id(config: ReplayConfig) -> str:
    if config.run.id:
        return config.run.id
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{config.run.name}_{stamp}"


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
) -> RunSummary:
    """Corre el motor sobre una fuente cualquiera y estampa los artefactos."""
    engine = PatternEngine(control_run_id=control_run_id, patterns=active_patterns)

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

    try:
        with (
            JsonlSink(artifacts.pattern_events_path) as pattern_sink,
            JsonlSink(artifacts.alerts_path) as alert_sink,
            JsonlSink(artifacts.metrics_path) as metric_sink,
            JsonlSink(artifacts.errors_path) as error_sink,
        ):
            for _, event, error in source:
                if error is not None:
                    error_sink.write(error)
                    errors_count += 1
                    # `line_number is None` => error de fuente, no unidad perdida.
                    if error.line_number is not None:
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
    finally:
        source.close()

    # ADR-003 regla 2: los drops del bus nunca se silencian.
    bus_dropped_events = source.dropped_events
    if bus_dropped_events:
        degradation_causes.add("bus_dropped_events")
        logger.warning("Bus: %d eventos perdidos; corrida degradada", bus_dropped_events)

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
```

- [ ] **Step 6: Replace `runtime/replay.py`**

Reemplazar el archivo entero por:

```python
"""Orquestacion de replay DBE del plano de control (spec 41 SS3: `JsonlSource`)."""

from __future__ import annotations

from pathlib import Path

from eovrt_control.config import ReplayConfig, load_replay_config
from eovrt_control.contracts.metrics import RunSummary
from eovrt_control.runtime.core import control_run_id, execute_over_source
from eovrt_control.sinks.artifacts import RunArtifacts
from eovrt_control.sources.jsonl import JsonlSource


def run_replay(config_path: str | Path) -> RunSummary:
    config: ReplayConfig = load_replay_config(config_path)
    if config.patterns_file is None:
        raise ValueError("La configuracion no tiene patterns_file resuelto")
    if config.input.type != "media_jsonl":
        raise ValueError(f"run_replay requiere input.type='media_jsonl', no {config.input.type!r}")

    run_id = control_run_id(config)
    base_dir = config.resolve_path(config.outputs.base_dir)
    artifacts = RunArtifacts(base_dir / run_id)
    artifacts.write_effective_config(config)

    active_patterns = config.patterns_file.active_patterns(config.patterns.active_ids)
    # `JsonlSource` emite el error de archivo inexistente como item de fuente;
    # el core lo escribe en errors.jsonl sin contarlo como unidad fallida.
    source = JsonlSource(config.resolve_path(config.input.path), run_id)

    return execute_over_source(
        config=config,
        source=source,
        control_run_id=run_id,
        artifacts=artifacts,
        active_patterns=active_patterns,
    )
```

- [ ] **Step 7: Run the whole suite**

```bash
python3 -m pytest -q --ignore=tests/labs
```

Expected: `67 passed`. Prestar atención a estos cuatro, que verifican que el refactor no
cambió la semántica de `pattern_evaluation`:
`test_missing_input_marks_pattern_evaluation_applicable_not_computed`,
`test_empty_input_marks_pattern_evaluation_applicable_not_computed`,
`test_ms_thresholds_on_images_are_inert_not_unreachable`,
`test_frame_threshold_on_images_is_genuinely_unreachable`.

**Gotcha verificado:** el `errors.jsonl` del caso "archivo inexistente" cambia de forma:
antes era un dict crudo sin `unit_id`/`source_id`/`line_number`; ahora es un `ErrorEvent`
serializado, que incluye esas tres claves en `null`. Ningún test lo asserta y el
`error_type` (`"FileNotFoundError"`) y el `message` son idénticos. Es el precio de tener
una sola ruta de error, y lo hace consistente con el bus.

- [ ] **Step 8: Lint**

```bash
python3 -m ruff check src tests
```

Expected: `All checks passed!`

- [ ] **Step 9: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_control/runtime src/eovrt_control/contracts/metrics.py \
        src/eovrt_control/config.py tests/test_replay.py
git commit -m "refactor(runtime): extraer execute_over_source; summary declara source/media_run_id"
```

---

## Task 3: media-plane — envelope `bus.envelope.v1` y `BusPublisher`

**Files:**
- Create: `e-ovrt_media-plane/src/eovrt_media/transport/bus.py`
- Test: `e-ovrt_media-plane/tests/test_bus_publisher.py`

`pyzmq` y `msgpack` **ya son dependencias** del media-plane (`pyproject.toml:6-15`): no
agregar nada.

**Interfaces:**
- Produces:
  - `ENVELOPE_SCHEMA_VERSION = "bus.envelope.v1"`
  - `DETECTION_TOPIC_PREFIX = "media.detection.v1."`
  - `LIFECYCLE_TOPIC_PREFIX = "run.lifecycle.v1."`
  - `encode_envelope(*, topic: str, key: str, seq: int, payload: bytes,
    ts_publish_ms: float) -> bytes`
  - `class BusPublisher`: `__init__(self, endpoint: str, *, hwm: int = 1000,
    wait_for_subscriber_ms: int = 0)`, `wait_for_subscriber(timeout_ms: int) -> bool`,
    `publish(topic: str, key: str, payload: bytes) -> int`, `close() -> None`,
    atributo `send_failures: int`.

**Por qué XPUB y no PUB:** son el mismo socket del lado del envío, pero XPUB **recibe una
notificación cuando alguien se suscribe**. Eso permite implementar honestamente la regla
1 de spec 40 §3.2 (el consumidor suscripto antes del disparo) en vez de dormir un rato y
rezar. Con `wait_for_subscriber_ms: 0` (default) el comportamiento es idéntico a PUB.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_bus_publisher.py`:

```python
import socket

import msgpack
import pytest
import zmq

from eovrt_media.transport.bus import (
    DETECTION_TOPIC_PREFIX,
    ENVELOPE_SCHEMA_VERSION,
    BusPublisher,
    encode_envelope,
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_encode_envelope_has_exactly_the_spec_fields() -> None:
    raw = encode_envelope(
        topic="media.detection.v1.run-1",
        key="cam-1",
        seq=7,
        payload=b'{"a": 1}',
        ts_publish_ms=1234.5,
    )

    envelope = msgpack.unpackb(raw, raw=False)

    assert set(envelope) == {
        "schema_version",
        "topic",
        "key",
        "seq",
        "ts_publish_ms",
        "payload",
    }
    assert envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert envelope["seq"] == 7
    # El payload viaja como bytes crudos, byte-compatible con la linea JSONL.
    assert envelope["payload"] == b'{"a": 1}'


@pytest.fixture()
def publisher_and_subscriber():
    endpoint = f"tcp://127.0.0.1:{_free_port()}"
    publisher = BusPublisher(endpoint, hwm=100)
    context = zmq.Context.instance()
    subscriber = context.socket(zmq.SUB)
    subscriber.setsockopt_string(zmq.SUBSCRIBE, DETECTION_TOPIC_PREFIX)
    subscriber.connect(endpoint)
    # XPUB avisa cuando la suscripcion llego: cierra la ventana de slow joiner.
    assert publisher.wait_for_subscriber(3000) is True
    yield publisher, subscriber
    subscriber.close(linger=0)
    publisher.close()


def test_publish_delivers_envelope_to_a_real_subscriber(publisher_and_subscriber) -> None:
    publisher, subscriber = publisher_and_subscriber

    publisher.publish("media.detection.v1.run-1", "cam-1", b'{"unit_id": "u1"}')

    topic, raw = subscriber.recv_multipart()
    envelope = msgpack.unpackb(raw, raw=False)
    assert topic == b"media.detection.v1.run-1"
    assert envelope["key"] == "cam-1"
    assert envelope["payload"] == b'{"unit_id": "u1"}'


def test_seq_starts_at_zero_and_is_monotonic(publisher_and_subscriber) -> None:
    publisher, subscriber = publisher_and_subscriber

    seqs = [publisher.publish("media.detection.v1.run-1", "cam-1", b"{}") for _ in range(3)]

    assert seqs == [0, 1, 2]
    received = [
        msgpack.unpackb(subscriber.recv_multipart()[1], raw=False)["seq"] for _ in range(3)
    ]
    assert received == [0, 1, 2]


def test_wait_for_subscriber_times_out_without_blocking_the_run() -> None:
    publisher = BusPublisher(f"tcp://127.0.0.1:{_free_port()}", hwm=10)
    try:
        # Sin suscriptor: devuelve False y la corrida sigue (el JSONL es la verdad).
        assert publisher.wait_for_subscriber(50) is False
        assert publisher.publish("media.detection.v1.run-1", "cam-1", b"{}") == 0
    finally:
        publisher.close()
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/simonll4/projects/e-ovrt_media-plane
python3 -m pytest tests/test_bus_publisher.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'eovrt_media.transport.bus'`.

- [ ] **Step 3: Write `transport/bus.py`**

```python
"""Publicador del bus media->control: envelope `bus.envelope.v1` sobre ZeroMQ (ADR-003).

Reglas no negociables (spec 40 SS3.2):
- No bloquea la inferencia: HWM finito + NOBLOCK; el JSONL es la verdad.
- `seq` es monotonico por publicador y se incrementa AUNQUE el envio se descarte,
  para que el consumidor vea el hueco y marque la corrida degradada.
"""

from __future__ import annotations

import logging
import time

import msgpack
import zmq

logger = logging.getLogger(__name__)

ENVELOPE_SCHEMA_VERSION = "bus.envelope.v1"
DETECTION_TOPIC_PREFIX = "media.detection.v1."
LIFECYCLE_TOPIC_PREFIX = "run.lifecycle.v1."
LIFECYCLE_SCHEMA_VERSION = "run.lifecycle.v1"


def encode_envelope(
    *, topic: str, key: str, seq: int, payload: bytes, ts_publish_ms: float
) -> bytes:
    """Serializa el envelope. `payload` son los bytes del evento tal cual van al JSONL."""
    return msgpack.packb(
        {
            "schema_version": ENVELOPE_SCHEMA_VERSION,
            "topic": topic,
            "key": key,
            "seq": seq,
            "ts_publish_ms": ts_publish_ms,
            "payload": payload,
        },
        use_bin_type=True,
    )


class BusPublisher:
    """Socket XPUB con contador de secuencia por corrida.

    XPUB en vez de PUB: del lado del envio son equivalentes, pero XPUB entrega una
    notificacion cuando un SUB se suscribe, lo que permite implementar la regla de
    "consumidor suscripto antes del disparo" sin dormir a ciegas.
    """

    def __init__(
        self, endpoint: str, *, hwm: int = 1000, wait_for_subscriber_ms: int = 0
    ) -> None:
        self.endpoint = endpoint
        self.send_failures = 0
        self._seq = 0
        self._closed = False
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.XPUB)
        self._sock.setsockopt(zmq.SNDHWM, hwm)
        self._sock.setsockopt(zmq.RCVHWM, 16)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.setsockopt(zmq.XPUB_VERBOSE, 1)
        self._sock.bind(endpoint)
        if wait_for_subscriber_ms > 0:
            self.wait_for_subscriber(wait_for_subscriber_ms)

    def wait_for_subscriber(self, timeout_ms: int) -> bool:
        """Espera la notificacion de suscripcion del XPUB (spec 40 SS3.2 regla 1).

        Devuelve False si vencio el timeout: la corrida sigue igual, sin bus.
        """
        poller = zmq.Poller()
        poller.register(self._sock, zmq.POLLIN)
        try:
            if not dict(poller.poll(timeout=timeout_ms)):
                logger.warning(
                    "bus: ningun suscriptor en %d ms; se publica igual (el JSONL es la verdad)",
                    timeout_ms,
                )
                return False
            self._sock.recv()  # b"\x01<topic>"
            return True
        finally:
            poller.unregister(self._sock)

    def publish(self, topic: str, key: str, payload: bytes) -> int:
        """Publica y devuelve el `seq` asignado. Nunca bloquea, nunca levanta."""
        seq = self._seq
        self._seq += 1
        envelope = encode_envelope(
            topic=topic, key=key, seq=seq, payload=payload, ts_publish_ms=time.time() * 1000.0
        )
        try:
            self._sock.send_multipart([topic.encode("utf-8"), envelope], flags=zmq.NOBLOCK)
        except zmq.Again:
            # HWM alcanzado. El `seq` ya se consumio: el consumidor vera el hueco.
            self.send_failures += 1
            logger.warning("bus: HWM alcanzado, envelope seq=%d descartado", seq)
        return seq

    def close(self) -> None:
        if not self._closed:
            self._sock.close(linger=0)
            self._closed = True
```

- [ ] **Step 4: Run the tests**

```bash
python3 -m pytest tests/test_bus_publisher.py -q
```

Expected: PASS (4 passed).

- [ ] **Step 5: Lint**

```bash
python3 -m ruff check src tests
```

Expected: `All checks passed!`

- [ ] **Step 6: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_media/transport/bus.py tests/test_bus_publisher.py
git commit -m "feat(transport): BusPublisher XPUB con envelope bus.envelope.v1"
```

---

## Task 4: media-plane — `BusPublishingArtifactWriter`, config y cableado

**Files:**
- Create: `src/eovrt_media/service/bus_writer.py`
- Modify: `src/eovrt_media/config/schemas.py` (nueva `BusConfig`; campo en `RunConfig:379-394`)
- Modify: `src/eovrt_media/service/run_request.py` (`BusSpec`, `RunRequest.bus`, `to_raw_run_config`)
- Modify: `src/eovrt_media/runtime/pipeline.py:384-525` (`execute_run`)
- Modify: `src/eovrt_media/runtime/two_node.py:101-169` (`run_node_b`)
- Test: `tests/test_bus_writer.py`

**Interfaces:**
- Consumes: `BusPublisher`, `encode_envelope`, prefijos de topic (Task 3).
- Produces:
  - `class BusPublishingArtifactWriter`: `__init__(self, inner, publisher: BusPublisher,
    run_id: str)`, `write_detection(event)`, `publish_run_finished(status: str)`,
    `close()`, `__getattr__` delegante.
  - `class BusConfig(BaseModel)`: `enabled: bool = False`,
    `endpoint: str = "tcp://0.0.0.0:5557"`, `hwm: int = 1000`,
    `wait_for_subscriber_ms: int = 0`.
  - `RunConfig.bus: BusConfig`.
  - `class BusSpec(BaseModel)` en el request; `RunRequest.bus: BusSpec | None = None`.

**El `run_finished` se publica desde `execute_run`, no desde `RunManager`.** Es donde
vive el writer y donde el ciclo de vida del publicador termina. El status se deriva ahí
(`failed` si escapó una excepción, `stopped` si hubo `control.stop_requested`, si no
`succeeded`). No coincide exactamente con el `RunManager` (que además distingue
`stalled`), y no importa: el consumidor sólo necesita **una** señal terminal.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_bus_writer.py`:

```python
import json
import socket

import msgpack
import pytest
import zmq

from eovrt_media.contracts.events import DetectionEvent
from eovrt_media.service.bus_writer import BusPublishingArtifactWriter
from eovrt_media.transport.bus import (
    DETECTION_TOPIC_PREFIX,
    LIFECYCLE_TOPIC_PREFIX,
    BusPublisher,
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _detection_event(unit_id: str) -> DetectionEvent:
    # OJO: en el media-plane `timing` es un campo REQUERIDO de DetectionEvent
    # (contracts/events.py:55, sin default), a diferencia del contrato del
    # control-plane donde tiene default_factory. Omitirlo es un ValidationError.
    return DetectionEvent.model_validate(
        {
            "run_id": "run-1",
            "unit_id": unit_id,
            "source": {
                "source_id": "cam-1",
                "source_type": "video",
                "frame_index": 0,
                "timestamp_ms": 0.0,
                "width": 640,
                "height": 480,
            },
            "model": {"name": "mock", "device": "cpu"},
            "prompts": {"prompt_set_id": "p1"},
            "detections": [],
            "timing": {},
        }
    )


class _RecordingWriter:
    """Sustituto del RunArtifactWriter: registra lo que se le persistio."""

    def __init__(self) -> None:
        self.detections: list[DetectionEvent] = []
        self.closed = False

    def write_detection(self, event: DetectionEvent) -> None:
        self.detections.append(event)

    def close(self) -> None:
        self.closed = True

    def write_summary(self, tracker) -> None:  # atributo delegado por __getattr__
        pass


@pytest.fixture()
def bus():
    endpoint = f"tcp://127.0.0.1:{_free_port()}"
    publisher = BusPublisher(endpoint)
    subscriber = zmq.Context.instance().socket(zmq.SUB)
    subscriber.setsockopt_string(zmq.SUBSCRIBE, DETECTION_TOPIC_PREFIX)
    subscriber.setsockopt_string(zmq.SUBSCRIBE, LIFECYCLE_TOPIC_PREFIX)
    subscriber.connect(endpoint)
    assert publisher.wait_for_subscriber(3000) is True
    yield publisher, subscriber
    subscriber.close(linger=0)
    publisher.close()


def test_payload_is_byte_identical_to_the_jsonl_line(bus) -> None:
    publisher, subscriber = bus
    inner = _RecordingWriter()
    writer = BusPublishingArtifactWriter(inner, publisher, "run-1")
    event = _detection_event("u1")

    writer.write_detection(event)

    _topic, raw = subscriber.recv_multipart()
    envelope = msgpack.unpackb(raw, raw=False)
    # Exactamente lo que JsonlSink.write_event escribe en detections.jsonl.
    assert envelope["payload"] == event.model_dump_json(exclude_none=True).encode("utf-8")
    assert envelope["topic"] == "media.detection.v1.run-1"
    assert envelope["key"] == "cam-1"
    # El JSONL sigue siendo la verdad: se persistio primero.
    assert inner.detections == [event]


def test_n_detections_produce_n_envelopes_then_run_finished(bus) -> None:
    publisher, subscriber = bus
    writer = BusPublishingArtifactWriter(_RecordingWriter(), publisher, "run-1")

    for index in range(5):
        writer.write_detection(_detection_event(f"u{index}"))
    writer.publish_run_finished("succeeded")

    topics = []
    envelopes = []
    for _ in range(6):
        topic, raw = subscriber.recv_multipart()
        topics.append(topic.decode())
        envelopes.append(msgpack.unpackb(raw, raw=False))

    assert topics[:5] == ["media.detection.v1.run-1"] * 5
    assert topics[5] == "run.lifecycle.v1.run-1"
    assert [envelope["seq"] for envelope in envelopes] == [0, 1, 2, 3, 4, 5]
    lifecycle = json.loads(envelopes[5]["payload"])
    assert lifecycle == {
        "schema_version": "run.lifecycle.v1",
        "event": "run_finished",
        "media_run_id": "run-1",
        "status": "succeeded",
    }


def test_writer_delegates_unknown_attributes_and_close(bus) -> None:
    publisher, _ = bus
    inner = _RecordingWriter()
    writer = BusPublishingArtifactWriter(inner, publisher, "run-1")

    writer.write_summary(tracker=None)
    writer.close()

    assert inner.closed is True


def test_bus_is_disabled_by_default() -> None:
    from eovrt_media.config.schemas import BusConfig

    assert BusConfig().enabled is False
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_bus_writer.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'eovrt_media.service.bus_writer'`.

- [ ] **Step 3: Write `service/bus_writer.py`**

```python
"""Publicacion al bus media->control: decorador del RunArtifactWriter (spec 42 SS2).

Hermano de `EventEmittingArtifactWriter` (service/events.py): aquel emite resumenes
para el WebSocket de la consola; este publica el `DetectionEvent` COMPLETO, ya
persistido, para el plano de control. Son piezas distintas.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from eovrt_media.transport.bus import (
    DETECTION_TOPIC_PREFIX,
    LIFECYCLE_SCHEMA_VERSION,
    LIFECYCLE_TOPIC_PREFIX,
    BusPublisher,
)

logger = logging.getLogger(__name__)


class BusPublishingArtifactWriter:
    """Persiste como siempre y ademas publica al bus. El JSONL es la verdad."""

    def __init__(self, inner: Any, publisher: BusPublisher, run_id: str) -> None:
        self._inner = inner
        self._publisher = publisher
        self._run_id = run_id
        self._detection_topic = f"{DETECTION_TOPIC_PREFIX}{run_id}"
        self._lifecycle_topic = f"{LIFECYCLE_TOPIC_PREFIX}{run_id}"

    def write_detection(self, event: Any) -> None:
        # Persistir PRIMERO: si el bus se cae, el artefacto ya esta en disco.
        self._inner.write_detection(event)
        # Byte-compatible con JsonlSink.write_event (spec 40 SS3.1): el evento del
        # bus y el releido del archivo son el mismo objeto.
        payload = event.model_dump_json(exclude_none=True).encode("utf-8")
        self._publisher.publish(self._detection_topic, event.source.source_id, payload)

    def publish_run_finished(self, status: str) -> None:
        """Sentinela END de la corrida 1:1 (ADR-007)."""
        payload = json.dumps(
            {
                "schema_version": LIFECYCLE_SCHEMA_VERSION,
                "event": "run_finished",
                "media_run_id": self._run_id,
                "status": status,
            }
        ).encode("utf-8")
        self._publisher.publish(self._lifecycle_topic, self._run_id, payload)

    def close(self) -> None:
        self._inner.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
```

- [ ] **Step 4: Add `BusConfig` to `config/schemas.py`**

Insertar **inmediatamente después** de `class TopologyConfig` (línea 216-219):

**Verificado:** `schemas.py` importa sólo `BaseModel, Field, model_validator` de pydantic
y **ninguna** de sus secciones declara `model_config` (0 ocurrencias). No agregar
`ConfigDict` acá — `BusConfig` sigue el estilo de sus vecinas:

```python
class BusConfig(BaseModel):
    """Bus media->control (ADR-003). Apagado por default: el JSONL es la verdad."""

    enabled: bool = False
    endpoint: str = "tcp://0.0.0.0:5557"
    hwm: int = Field(default=1000, gt=0)
    # > 0 bloquea el arranque del run hasta que un SUB se suscriba (spec 40
    # SS3.2 regla 1). 0 = no esperar (comportamiento de un PUB comun).
    wait_for_subscriber_ms: int = Field(default=0, ge=0)
```

En `class RunConfig` (línea 379), agregar el campo después de `topology`:

```python
    topology: TopologyConfig = Field(default_factory=TopologyConfig)
    bus: BusConfig = Field(default_factory=BusConfig)
```

- [ ] **Step 5: Accept `bus` in the run request**

En `src/eovrt_media/service/run_request.py`, agregar antes de `class RunRequest`:

```python
class BusSpec(BaseModel):
    """Bus media->control por payload (ADR-009): es del experimento, no del despliegue."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    endpoint: str | None = None
    hwm: int | None = None
    wait_for_subscriber_ms: int | None = None
```

En `class RunRequest`, agregar el campo:

```python
class RunRequest(BaseModel):
    # extra="forbid": una sección 'model' (u otra desconocida) en el body → 422.
    model_config = ConfigDict(extra="forbid")
    ingest: IngestSpec
    prompts: PromptsSpec
    run: RunParams = Field(default_factory=RunParams)
    bus: BusSpec | None = None
```

En `to_raw_run_config`, antes del `return raw`:

```python
    if request.bus is not None:
        raw["bus"] = request.bus.model_dump(exclude_none=True)
    return raw
```

- [ ] **Step 6: Wire it into `execute_run`**

En `src/eovrt_media/runtime/pipeline.py`, reemplazar la función `execute_run`
(líneas 384-525) por esta versión. El cuerpo interno es idéntico; lo que cambia es el
envoltorio del publicador y el `try/finally` externo que garantiza el `run_finished`:

```python
def execute_run(
    config: RunConfig,
    adapter,
    *,
    console: Console | None = None,
    control: RunControl | None = None,
    event_sink=None,
) -> str:
    """Ejecuta un run con un adapter YA CARGADO (no lo crea, no lo cierra).

    Camino del servicio (Spec A §6): el modelo vive a nivel proceso; stop vía
    RunControl; telemetría opcional por event_sink (decorando el writer).
    """
    run_context = RunContext(config)
    artifact_writer = RunArtifactWriter(run_context)
    if event_sink is not None:
        from eovrt_media.service.events import EventEmittingArtifactWriter

        artifact_writer = EventEmittingArtifactWriter(artifact_writer, event_sink)

    bus_publisher = None
    if config.bus.enabled:
        from eovrt_media.service.bus_writer import BusPublishingArtifactWriter
        from eovrt_media.transport.bus import BusPublisher

        bus_publisher = BusPublisher(
            config.bus.endpoint,
            hwm=config.bus.hwm,
            wait_for_subscriber_ms=config.bus.wait_for_subscriber_ms,
        )
        artifact_writer = BusPublishingArtifactWriter(
            artifact_writer, bus_publisher, run_context.run_id
        )

    tracker = LatencyTracker()
    producer = None
    consumer_stalled = False
    bus_status = "succeeded"

    if console is not None:
        console.print(f"[bold green]▶ Corrida:[/bold green] {run_context.run_id}")
        console.print(f"[dim]  Directorio de salida: {run_context.run_dir}[/dim]")

    try:
        try:
            if config.config_path:
                artifact_writer.write_original_config(config.config_path)
            artifact_writer.write_effective_config()

            source = create_source(config)
            if control is not None:
                control.bind_source(source)
            try:
                source_count = len(source)
                progress_total: int | None = source_count if source_count >= 0 else None
            except TypeError:
                progress_total = None
            normalizer = DetectionNormalizer(
                min_confidence=config.postprocess.min_confidence,
                min_box_area_px=config.postprocess.min_box_area_px,
                normalize_boxes=config.postprocess.normalize_boxes,
            )
            plan = config.build_prompt_plan(adapter.PROMPT_BACKEND)
            prompt_set_id = (
                config.prompts_file.resolved_set_id() if config.prompts_file else "unknown"
            )
            reset_gpu_peak_memory()

            rate_control = config.rate_control
            transport = create_transport(
                backend=config.transport.backend,
                policy=rate_control.policy,
                max_queue_size=rate_control.max_queue_size,
                buffer_size=rate_control.buffer_size,
                max_staleness_ms=rate_control.max_staleness_ms,
                endpoint=config.transport.endpoint,
            )
            should_continue = (
                (lambda: not control.stop_requested) if control is not None else None
            )
            timings: dict[str, float] = {"backpressure_wait_ms": 0.0}
            producer = threading.Thread(
                target=run_producer_loop,
                args=(
                    source,
                    RateGate(stride=rate_control.stride),
                    adapter.input_spec,
                    PayloadFormat(config.transport.payload_format),
                    transport,
                    run_context.run_id,
                    run_context._errors_queue,
                    timings,
                    should_continue,
                ),
                daemon=True,
                name="pipeline-producer",
            )
            producer.start()

            def _consume(progress=None, task=None) -> None:
                nonlocal consumer_stalled
                consumer_stalled = run_consumer_loop(
                    transport=transport,
                    adapter=adapter,
                    normalizer=normalizer,
                    artifact_writer=artifact_writer,
                    run_context=run_context,
                    tracker=tracker,
                    config=config,
                    plan=plan,
                    prompt_set_id=prompt_set_id,
                    timings=timings,
                    progress=progress,
                    task=task,
                    drain_errors=True,
                    control=control,
                )

            try:
                if console is not None:
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(),
                        TaskProgressColumn(),
                        console=console,
                    ) as progress:
                        task = progress.add_task(
                            "Procesando unidades visuales...", total=progress_total
                        )
                        _consume(progress, task)
                else:
                    _consume()
            except KeyboardInterrupt:
                if console is not None:
                    console.print(
                        "\n[yellow]⚠ Corrida interrumpida — guardando artefactos...[/yellow]"
                    )
                source.stop()
                transport.close()
                producer.join(timeout=5.0)

            _drain_producer_errors(run_context._errors_queue, artifact_writer, run_context)
            run_context.units_dropped = getattr(transport, "units_dropped", 0)
            run_context.backpressure_wait_ms = timings["backpressure_wait_ms"]
        finally:
            if producer is not None:
                join_timeout = (
                    PRODUCER_JOIN_TIMEOUT_AFTER_STALL_S if consumer_stalled else 30.0
                )
                producer.join(timeout=join_timeout)
            artifact_writer.close()

        run_context.gpu_memory_peak_mb = get_gpu_memory_peak_mb()
        run_context.finish()
        artifact_writer.write_summary(tracker)
        artifact_writer.write_provenance()
        artifact_writer.write_manifest()
        if control is not None and control.stop_requested:
            bus_status = "stopped"
        return run_context.run_id
    except BaseException:
        bus_status = "failed"
        raise
    finally:
        if bus_publisher is not None:
            # Sentinela END de la corrida 1:1 (ADR-007): sale pase lo que pase,
            # incluso si el run murio. Nunca deja al consumidor colgado.
            try:
                artifact_writer.publish_run_finished(bus_status)
            except Exception:  # noqa: BLE001 — el bus nunca rompe la corrida
                logger.warning("bus: no se pudo publicar run_finished", exc_info=True)
            bus_publisher.close()
```

Verificar que `logger` existe en `pipeline.py` (hay `logging` en el módulo); si no,
agregar `logger = logging.getLogger(__name__)` bajo los imports.

- [ ] **Step 7: Wire it into `run_node_b`**

En `src/eovrt_media/runtime/two_node.py`, en `run_node_b`, después de
`artifact_writer = RunArtifactWriter(run_context)` (línea 105):

```python
    bus_publisher = None
    if config.bus.enabled:
        from eovrt_media.service.bus_writer import BusPublishingArtifactWriter
        from eovrt_media.transport.bus import BusPublisher

        # Spec 42 SS2: en two-node el publisher vive en el Nodo B; el Nodo A no
        # publica nada. Este nodo garantiza finalizacion con status explicito.
        bus_publisher = BusPublisher(
            config.bus.endpoint,
            hwm=config.bus.hwm,
            wait_for_subscriber_ms=config.bus.wait_for_subscriber_ms,
        )
        artifact_writer = BusPublishingArtifactWriter(
            artifact_writer, bus_publisher, run_context.run_id
        )
```

Y al final de la función, justo después de `_finalize_summary_status(...)` (línea 165-169):

```python
    if bus_publisher is not None:
        try:
            artifact_writer.publish_run_finished(
                "failed" if failure is not None else "succeeded"
            )
        except Exception:  # noqa: BLE001 — el bus nunca rompe la corrida
            logger.warning("bus: no se pudo publicar run_finished", exc_info=True)
        bus_publisher.close()
```

Verificar que `logger` existe en `two_node.py`; si no, agregarlo.

- [ ] **Step 8: Run the new tests**

```bash
python3 -m pytest tests/test_bus_writer.py -q
```

Expected: PASS (4 passed).

- [ ] **Step 9: Verify no regression in the media-plane suite**

```bash
python3 -m pytest -q
```

Expected: `444 passed` (440 de línea base + los 4 de `test_bus_writer.py`), 0 fallas. Los
tests sensibles al cableado son `test_execute_run.py`, `test_pipeline_mock.py`,
`test_pipeline_two_threads.py` y `test_two_node.py`: con `bus.enabled: false` (default)
el `bus_publisher` es `None` y el camino es exactamente el anterior. Si alguno de esos
cuatro se rompe, el `try/except BaseException/finally` nuevo de `execute_run` cambió el
orden de cierre — revisar que `artifact_writer.close()` siga corriendo en el `finally`
interno, antes del `write_summary`.

- [ ] **Step 10: End-to-end manual del gate de spec 42 §6.3**

"Un consumidor de prueba recibe N eventos = N líneas del JSONL + END."

```bash
cd /home/simonll4/projects/e-ovrt_media-plane
python3 - <<'PY'
import json, subprocess, threading, time
import msgpack, zmq
# Suscriptor primero (regla 1): en un shell aparte levantar el servicio con
# EOVRT_MODEL_REF=mock y POSTear un run con {"bus": {"enabled": true,
# "endpoint": "tcp://127.0.0.1:5557", "wait_for_subscriber_ms": 5000}}.
ctx = zmq.Context.instance()
sub = ctx.socket(zmq.SUB)
for prefix in ("media.detection.v1.", "run.lifecycle.v1."):
    sub.setsockopt_string(zmq.SUBSCRIBE, prefix)
sub.connect("tcp://127.0.0.1:5557")
print("suscripto; disparar el run ahora")
detections, run_id = 0, None
while True:
    topic, raw = sub.recv_multipart()
    envelope = msgpack.unpackb(raw, raw=False)
    if topic.startswith(b"run.lifecycle.v1."):
        print("END:", json.loads(envelope["payload"]))
        break
    detections += 1
    run_id = json.loads(envelope["payload"])["run_id"]
print("eventos recibidos:", detections, "run:", run_id)
PY
# Comparar contra el archivo:
wc -l runs/<run_id>/detections.jsonl
```

Expected: `eventos recibidos` == líneas de `detections.jsonl`, y el `END` con
`status: "succeeded"`.

- [ ] **Step 11: Lint**

```bash
python3 -m ruff check src tests
```

- [ ] **Step 12: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_media/service/bus_writer.py src/eovrt_media/config/schemas.py \
        src/eovrt_media/service/run_request.py src/eovrt_media/runtime/pipeline.py \
        src/eovrt_media/runtime/two_node.py tests/test_bus_writer.py
git commit -m "feat(bus): BusPublishingArtifactWriter + run.lifecycle.v1 al cerrar el run"
```

---

## Task 5: control-plane — `BusSource`

**Files:**
- Modify: `pyproject.toml` (deps)
- Create: `src/eovrt_control/sources/bus.py`
- Test: `tests/test_bus_source.py`

**Interfaces:**
- Consumes: `MediaEventSource`, `SourceItem` (Task 1).
- Produces:
  - `class BusSource(MediaEventSource)` con `kind = "bus"` y
    `__init__(self, *, endpoint: str, control_run_id: str, topics: list[str] | None = None,
    hwm: int = 1000, recv_timeout_ms: int = 1000, idle_timeout_s: float = 300.0,
    poll_url: str | None = None, poll_interval_s: float = 5.0)`.
  - Constantes `DETECTION_TOPIC_PREFIX`, `LIFECYCLE_TOPIC_PREFIX`,
    `ENVELOPE_SCHEMA_VERSION`, `TERMINAL_STATUSES`.

**Decisiones de implementación, con su razón:**
- **El constructor conecta y suscribe.** Construir el `BusSource` *es* suscribirse. El
  llamador lo crea antes de disparar el run del media-plane (spec 40 §3.2 regla 1); si
  la suscripción viviera en `__iter__`, la garantía se perdería.
- **`seq` por `run_key`**, donde `run_key = topic.split(".v1.", 1)[1]` — es el
  `media_run_id` tanto en `media.detection.v1.<id>` como en `run.lifecycle.v1.<id>`. Un
  publicador = un run, así que "por `run_key`" y "por publicador" coinciden.
- **Sólo `seq > esperado` cuenta como pérdida.** TCP preserva el orden dentro de una
  conexión PUB/SUB, así que un `seq` menor sería un duplicado, no un hueco.
- **`urllib` y no `httpx`** para el polling: no agrega una dependencia al plano de
  control por una función que se usa una vez cada 5 segundos.

- [ ] **Step 1: Add the dependencies**

En `pyproject.toml` del control-plane:

```toml
dependencies = [
    "pydantic",
    "pyyaml",
    "typer",
    "rich",
    "pyzmq",
    "msgpack",
]
```

```bash
cd /home/simonll4/projects/e-ovrt_control-plane
python3 -m pip install -e ".[dev]"
```

- [ ] **Step 2: Write the failing test**

Crear `tests/test_bus_source.py`:

```python
import json
import socket
import threading
import time

import msgpack
import pytest
import zmq

from eovrt_control.sources.bus import BusSource


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _event_payload(unit_id: str) -> bytes:
    return json.dumps(
        {
            "run_id": "media-run",
            "unit_id": unit_id,
            "source": {
                "source_id": "cam-1",
                "source_type": "video_frame",
                "frame_index": 0,
                "timestamp_ms": 0.0,
                "width": 640,
                "height": 480,
            },
            "model": {"name": "mock", "device": "cpu"},
            "prompts": {"prompt_set_id": "p1"},
            "detections": [],
        }
    ).encode("utf-8")


class _Publisher:
    """Publicador de prueba: pinea el wire format del media-plane, sin importarlo."""

    def __init__(self, endpoint: str) -> None:
        self._sock = zmq.Context.instance().socket(zmq.XPUB)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.setsockopt(zmq.XPUB_VERBOSE, 1)
        self._sock.bind(endpoint)

    def wait_for_subscriber(self, timeout_ms: int = 3000) -> None:
        poller = zmq.Poller()
        poller.register(self._sock, zmq.POLLIN)
        assert dict(poller.poll(timeout=timeout_ms)), "el SUB no se suscribio a tiempo"
        self._sock.recv()

    def send(self, topic: str, seq: int, payload: bytes, key: str = "cam-1") -> None:
        envelope = msgpack.packb(
            {
                "schema_version": "bus.envelope.v1",
                "topic": topic,
                "key": key,
                "seq": seq,
                "ts_publish_ms": 0.0,
                "payload": payload,
            },
            use_bin_type=True,
        )
        self._sock.send_multipart([topic.encode("utf-8"), envelope])

    def finish(self, seq: int, status: str = "succeeded") -> None:
        payload = json.dumps(
            {
                "schema_version": "run.lifecycle.v1",
                "event": "run_finished",
                "media_run_id": "media-run",
                "status": status,
            }
        ).encode("utf-8")
        self.send("run.lifecycle.v1.media-run", seq, payload, key="media-run")

    def close(self) -> None:
        self._sock.close(linger=0)


@pytest.fixture()
def wired():
    endpoint = f"tcp://127.0.0.1:{_free_port()}"
    publisher = _Publisher(endpoint)
    source = BusSource(endpoint=endpoint, control_run_id="control-run", recv_timeout_ms=200)
    publisher.wait_for_subscriber()
    yield publisher, source
    source.close()
    publisher.close()


def test_bus_source_yields_events_until_run_finished(wired) -> None:
    publisher, source = wired
    for seq in range(3):
        publisher.send("media.detection.v1.media-run", seq, _event_payload(f"u{seq}"))
    publisher.finish(seq=3)

    items = list(source)

    assert [event.unit_id for _, event, _ in items] == ["u0", "u1", "u2"]
    assert source.dropped_events == 0
    assert source.kind == "bus"


def test_bus_source_counts_seq_gaps_as_dropped_events(wired) -> None:
    publisher, source = wired
    publisher.send("media.detection.v1.media-run", 0, _event_payload("u0"))
    # seq 1 y 2 se "perdieron" (HWM del publicador).
    publisher.send("media.detection.v1.media-run", 3, _event_payload("u3"))
    publisher.finish(seq=4)

    items = list(source)

    assert [event.unit_id for _, event, _ in items] == ["u0", "u3"]
    assert source.dropped_events == 2


def test_bus_source_reports_unparseable_payload_as_unit_error(wired) -> None:
    publisher, source = wired
    publisher.send("media.detection.v1.media-run", 0, b"{no es json}")
    publisher.finish(seq=1)

    items = list(source)

    assert len(items) == 1
    (index, event, error) = items[0]
    assert event is None
    assert index == 0
    # Una unidad que llego pero no valida ES una unidad fallida.
    assert error.line_number == 0
    assert error.error_type == "JSONDecodeError"


def test_bus_source_reports_bad_envelope_as_source_error(wired) -> None:
    publisher, source = wired
    bad = msgpack.packb({"schema_version": "bus.envelope.v9", "topic": "media.detection.v1.x"})
    publisher._sock.send_multipart([b"media.detection.v1.media-run", bad])
    publisher.finish(seq=0)

    items = list(source)

    (_, event, error) = items[0]
    assert event is None
    assert error.error_type == "EnvelopeSchemaMismatch"
    # Error de FUENTE: no cuenta como unidad fallida.
    assert error.line_number is None


def test_bus_source_closes_by_polling_fallback_when_lifecycle_is_lost(wired, monkeypatch) -> None:
    publisher, _unused = wired
    endpoint = publisher._sock.getsockopt_string(zmq.LAST_ENDPOINT)
    source = BusSource(
        endpoint=endpoint,
        control_run_id="control-run",
        recv_timeout_ms=100,
        poll_url="http://localhost:9/api/runs/media-run",
        poll_interval_s=0.05,
        idle_timeout_s=30.0,
    )
    publisher.wait_for_subscriber()
    publisher.send("media.detection.v1.media-run", 0, _event_payload("u0"))
    # El run_finished NUNCA se publica: solo el polling puede cerrar la corrida.
    monkeypatch.setattr(
        "eovrt_control.sources.bus.BusSource._run_finished_by_polling", lambda self: True
    )

    items = list(source)
    source.close()

    assert [event.unit_id for _, event, _ in items] == ["u0"]


def test_bus_source_closes_on_idle_timeout_with_a_source_error(wired) -> None:
    publisher, _unused = wired
    endpoint = publisher._sock.getsockopt_string(zmq.LAST_ENDPOINT)
    source = BusSource(
        endpoint=endpoint,
        control_run_id="control-run",
        recv_timeout_ms=50,
        idle_timeout_s=0.15,
    )
    publisher.wait_for_subscriber()

    items = list(source)
    source.close()

    assert len(items) == 1
    (_, event, error) = items[0]
    assert event is None
    assert error.error_type == "BusIdleTimeout"
    assert error.line_number is None
```

- [ ] **Step 3: Run to verify failure**

```bash
python3 -m pytest tests/test_bus_source.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'eovrt_control.sources.bus'`.

- [ ] **Step 4: Write `sources/bus.py`**

```python
"""Consumidor del bus media->control: ZeroMQ SUB + envelope `bus.envelope.v1`.

Obligaciones (ADR-003, spec 41 SS3, spec 40 SS3.2):
- Suscripcion ANTES del disparo del run: construir este objeto ES suscribirse.
- Huecos en `seq` -> `dropped_events` (la corrida se marca degradada aguas arriba).
- Cierre por `run.lifecycle.v1/run_finished`, con fallback por polling del estado
  del run en el media-plane.
- Deserializacion por la MISMA ruta de validacion que la linea JSONL: paridad por
  construccion con `JsonlSource` (spec 40 SS3.4).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Iterator

import msgpack
import zmq
from pydantic import ValidationError

from eovrt_control.contracts.errors import ErrorEvent
from eovrt_control.contracts.media import DetectionEvent
from eovrt_control.sources.base import MediaEventSource, SourceItem

logger = logging.getLogger(__name__)

ENVELOPE_SCHEMA_VERSION = "bus.envelope.v1"
DETECTION_TOPIC_PREFIX = "media.detection.v1."
LIFECYCLE_TOPIC_PREFIX = "run.lifecycle.v1."
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "stopped"})


class BusSource(MediaEventSource):
    kind = "bus"

    def __init__(
        self,
        *,
        endpoint: str,
        control_run_id: str,
        topics: list[str] | None = None,
        hwm: int = 1000,
        recv_timeout_ms: int = 1000,
        idle_timeout_s: float = 300.0,
        poll_url: str | None = None,
        poll_interval_s: float = 5.0,
    ) -> None:
        self.endpoint = endpoint
        self._control_run_id = control_run_id
        self._recv_timeout_ms = recv_timeout_ms
        self._idle_timeout_s = idle_timeout_s
        self._poll_url = poll_url
        self._poll_interval_s = poll_interval_s
        self._expected_seq: dict[str, int] = {}
        self._dropped = 0
        self._closed = False

        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.SUB)
        self._sock.setsockopt(zmq.RCVHWM, hwm)
        self._sock.setsockopt(zmq.LINGER, 0)
        for topic in topics or [DETECTION_TOPIC_PREFIX, LIFECYCLE_TOPIC_PREFIX]:
            self._sock.setsockopt_string(zmq.SUBSCRIBE, topic)
        self._sock.connect(endpoint)
        self._poller = zmq.Poller()
        self._poller.register(self._sock, zmq.POLLIN)

    @property
    def dropped_events(self) -> int:
        return self._dropped

    def close(self) -> None:
        if not self._closed:
            self._poller.unregister(self._sock)
            self._sock.close(linger=0)
            self._closed = True

    def __iter__(self) -> Iterator[SourceItem]:
        last_message = time.monotonic()
        last_poll = time.monotonic()
        while True:
            if dict(self._poller.poll(timeout=self._recv_timeout_ms)):
                _topic, raw = self._sock.recv_multipart()
                last_message = time.monotonic()
                item, finished = self._decode(raw)
                if item is not None:
                    yield item
                if finished:
                    return
                continue

            now = time.monotonic()
            if self._poll_url and now - last_poll >= self._poll_interval_s:
                last_poll = now
                if self._run_finished_by_polling():
                    logger.warning(
                        "bus: cierre por fallback de polling (%s); el run_finished no llego",
                        self._poll_url,
                    )
                    return
            if now - last_message >= self._idle_timeout_s:
                yield self._source_error(
                    "BusIdleTimeout",
                    f"Sin eventos del bus por {self._idle_timeout_s}s; cierre por timeout",
                )
                return

    # --- interno ---

    def _source_error(self, error_type: str, message: str) -> SourceItem:
        # `line_number=None` => error de FUENTE: no cuenta como unidad fallida.
        return (
            -1,
            None,
            ErrorEvent(
                control_run_id=self._control_run_id,
                message=message,
                error_type=error_type,
            ),
        )

    def _decode(self, raw: bytes) -> tuple[SourceItem | None, bool]:
        """Devuelve (item a emitir o None, se termino la corrida)."""
        try:
            envelope = msgpack.unpackb(raw, raw=False)
        except Exception as exc:  # noqa: BLE001 — msgpack levanta varios tipos
            return self._source_error("EnvelopeDecodeError", str(exc)), False
        if not isinstance(envelope, dict):
            return self._source_error("EnvelopeDecodeError", "el envelope no es un mapa"), False
        if envelope.get("schema_version") != ENVELOPE_SCHEMA_VERSION:
            return (
                self._source_error(
                    "EnvelopeSchemaMismatch",
                    f"schema_version inesperado: {envelope.get('schema_version')!r}",
                ),
                False,
            )

        topic = envelope["topic"]
        seq = int(envelope["seq"])
        # `media.detection.v1.<id>` y `run.lifecycle.v1.<id>` comparten publicador
        # y contador, asi que el sufijo del topic identifica la secuencia.
        self._check_seq(topic.split(".v1.", 1)[-1], seq)
        payload = envelope["payload"]

        if topic.startswith(LIFECYCLE_TOPIC_PREFIX):
            control = json.loads(payload)
            if control.get("event") == "run_finished":
                logger.info(
                    "bus: run_finished media_run_id=%s status=%s",
                    control.get("media_run_id"),
                    control.get("status"),
                )
                return None, True
            return None, False

        try:
            # Misma ruta de validacion que la linea JSONL: paridad por construccion.
            event = DetectionEvent.model_validate(json.loads(payload))
        except (json.JSONDecodeError, ValidationError) as exc:
            return (
                (
                    seq,
                    None,
                    ErrorEvent(
                        control_run_id=self._control_run_id,
                        message=str(exc),
                        error_type=type(exc).__name__,
                        line_number=seq,
                    ),
                ),
                False,
            )
        return (seq, event, None), False

    def _check_seq(self, run_key: str, seq: int) -> None:
        expected = self._expected_seq.get(run_key)
        if expected is not None and seq > expected:
            gap = seq - expected
            self._dropped += gap
            logger.warning(
                "bus: hueco de seq en %s (esperado %d, recibido %d): %d eventos perdidos",
                run_key,
                expected,
                seq,
                gap,
            )
        # seq < expected seria un duplicado: TCP preserva el orden, no lo contamos.
        self._expected_seq[run_key] = seq + 1

    def _run_finished_by_polling(self) -> bool:
        """Fallback de cierre: GET /api/runs/{id} del media-plane (spec 41 SS3)."""
        try:
            with urllib.request.urlopen(self._poll_url, timeout=5.0) as response:
                body = json.loads(response.read())
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            logger.warning("bus: polling de %s fallo: %s", self._poll_url, exc)
            return False
        return body.get("status") in TERMINAL_STATUSES
```

- [ ] **Step 5: Run the tests**

```bash
python3 -m pytest tests/test_bus_source.py -q
```

Expected: PASS (6 passed). Si `test_bus_source_closes_on_idle_timeout...` cuelga, el
`idle_timeout_s` no está siendo comparado contra `time.monotonic()` en el camino sin
mensajes: revisar que `last_message` sólo se actualice al recibir.

- [ ] **Step 6: Full suite + lint**

```bash
python3 -m pytest -q --ignore=tests/labs && python3 -m ruff check src tests
```

Expected: `73 passed`, `All checks passed!`

- [ ] **Step 7: Commit (sólo si el usuario lo pidió)**

```bash
git add pyproject.toml src/eovrt_control/sources/bus.py tests/test_bus_source.py
git commit -m "feat(sources): BusSource ZeroMQ SUB con deteccion de huecos de seq"
```

---

## Task 6: control-plane — config `bus`, `runtime/live.py` y CLI `live`

**Files:**
- Modify: `src/eovrt_control/config.py` (`InputSection`, secciones nuevas, quitar `validate_input_type`)
- Create: `src/eovrt_control/runtime/live.py`
- Modify: `src/eovrt_control/cli.py` (subcomando `live`)
- Create: `configs/live_ebe_cr01_cr02.yaml`
- Test: `tests/test_live.py`

**Interfaces:**
- Consumes: `execute_over_source`, `control_run_id` (Task 2); `BusSource` (Task 5);
  `MemorySource` (Task 1).
- Produces:
  - `class BusFinishSection`, `class BusInputSection` en `config.py`.
  - `InputSection.type: Literal["media_jsonl", "bus"]`, `path: str | None`,
    `bus: BusInputSection | None`.
  - `runtime.live.build_bus_source(config: ReplayConfig, control_run_id: str) -> BusSource`
  - `runtime.live.run_live(config_path: str | Path, *, source: MediaEventSource | None = None)
    -> RunSummary`

**Por qué `run_live` acepta una fuente inyectada:** la regla "suscripto antes del
disparo" obliga al orquestador (webconsole, runner, o el test) a crear el `BusSource`,
*después* POSTear el run al media-plane, y *recién entonces* consumir. Con la fuente
inyectada eso es expresable; si `run_live` la construyera siempre por su cuenta, la
suscripción ocurriría demasiado tarde.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_live.py`:

```python
import json
from pathlib import Path

import pytest
import yaml

from eovrt_control.config import load_replay_config
from eovrt_control.contracts.media import DetectionEvent
from eovrt_control.runtime.live import build_bus_source, run_live
from eovrt_control.sources.memory import MemorySource

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATTERNS = _REPO_ROOT / "configs/patterns/cr01_cr02_v1.yaml"


def _live_config(tmp_path: Path, **bus_overrides) -> Path:
    bus = {
        "endpoint": "tcp://127.0.0.1:5557",
        "hwm": 1000,
        "finish": {"signal": "run_lifecycle", "poll_url": None, "poll_interval_s": 5},
    }
    bus.update(bus_overrides)
    config_path = tmp_path / "live.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "run": {
                    "id": "control-live",
                    "scenario": "EBE",
                    "name": "live",
                    "experiment_id": "exp-1",
                },
                "input": {"type": "bus", "bus": bus},
                "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
                "outputs": {"base_dir": str(tmp_path / "runs")},
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _event(unit_id: str) -> DetectionEvent:
    return DetectionEvent.model_validate(
        {
            "run_id": "media-run",
            "unit_id": unit_id,
            "source": {
                "source_id": "cam-1",
                "source_type": "video_frame",
                "frame_index": 0,
                "timestamp_ms": 0.0,
                "width": 640,
                "height": 480,
            },
            "model": {"name": "mock", "device": "cpu"},
            "prompts": {"prompt_set_id": "cr01_cr02_v1"},
            "detections": [
                {
                    "detection_id": "p1",
                    "label": "person",
                    "prompt_id": "person",
                    "confidence": 0.9,
                    "bbox_xyxy": [100, 100, 220, 420],
                }
            ],
        }
    )


def test_run_live_over_injected_source_writes_the_same_artifacts(tmp_path) -> None:
    summary = run_live(_live_config(tmp_path), source=MemorySource([_event("u1")]))

    assert summary.units_processed == 1
    assert summary.alerts_count == 1
    assert summary.source == "memory"
    assert summary.media_run_id == "media-run"
    assert summary.experiment_id == "exp-1"
    assert summary.scenario == "EBE"
    run_dir = tmp_path / "runs" / "control-live"
    for name in ("summary.json", "alerts.jsonl", "pattern_events.jsonl", "metrics.jsonl"):
        assert (run_dir / name).exists()


def test_run_live_marks_run_degraded_when_the_bus_dropped_events(tmp_path) -> None:
    class _LossySource(MemorySource):
        kind = "bus"

        @property
        def dropped_events(self) -> int:
            return 3

    summary = run_live(_live_config(tmp_path), source=_LossySource([_event("u1")]))

    assert summary.bus_dropped_events == 3
    assert summary.degraded is True
    assert "bus_dropped_events" in summary.degradation_causes


def test_build_bus_source_reads_endpoint_and_finish_from_config(tmp_path) -> None:
    config = load_replay_config(_live_config(tmp_path, endpoint="tcp://127.0.0.1:5599"))

    source = build_bus_source(config, "control-live")
    try:
        assert source.endpoint == "tcp://127.0.0.1:5599"
        assert source.kind == "bus"
    finally:
        source.close()


def test_bus_config_requires_the_bus_section(tmp_path) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "run": {"name": "x"},
                "input": {"type": "bus"},
                "patterns": {"file": str(_PATTERNS)},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="input.bus"):
        load_replay_config(config_path)


def test_jsonl_config_requires_a_path(tmp_path) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "run": {"name": "x"},
                "input": {"type": "media_jsonl"},
                "patterns": {"file": str(_PATTERNS)},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="input.path"):
        load_replay_config(config_path)
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_live.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'eovrt_control.runtime.live'`.

- [ ] **Step 3: Extend `config.py`**

Reemplazar `class InputSection` (líneas 19-21) por:

```python
class BusFinishSection(BaseModel):
    """Como se entera el consumidor de que la corrida termino (spec 41 SS3)."""

    signal: Literal["run_lifecycle"] = "run_lifecycle"
    # Fallback por polling de GET /api/runs/{id} del media-plane.
    poll_url: str | None = None
    poll_interval_s: float = 5.0


class BusInputSection(BaseModel):
    endpoint: str
    topics: list[str] = Field(
        default_factory=lambda: ["media.detection.v1.", "run.lifecycle.v1."]
    )
    hwm: int = 1000
    recv_timeout_ms: int = 1000
    # Corte de seguridad si el publicador muere sin `run_finished` y no hay poll_url.
    idle_timeout_s: float = 300.0
    finish: BusFinishSection = Field(default_factory=BusFinishSection)


class InputSection(BaseModel):
    type: Literal["media_jsonl", "bus"] = "media_jsonl"
    path: str | None = None
    bus: BusInputSection | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "InputSection":
        if self.type == "media_jsonl" and not self.path:
            raise ValueError("input.type='media_jsonl' requiere input.path")
        if self.type == "bus" and self.bus is None:
            raise ValueError("input.type='bus' requiere input.bus")
        return self
```

Y **borrar** el validador `validate_input_type` de `ReplayConfig` (líneas 125-129), que
ya rechazaba todo lo que no fuera `media_jsonl`.

- [ ] **Step 4: Write `runtime/live.py`**

```python
"""Runtime live: el motor consume el bus del media-plane (spec 41 SS4, ADR-007).

La corrida es 1:1 con el run del media-plane: nace suscripta, consume hasta
`run_finished`/END y escribe los mismos artefactos que el replay.
"""

from __future__ import annotations

from pathlib import Path

from eovrt_control.config import ReplayConfig, load_replay_config
from eovrt_control.contracts.metrics import RunSummary
from eovrt_control.runtime.core import control_run_id, execute_over_source
from eovrt_control.sinks.artifacts import RunArtifacts
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


def run_live(
    config_path: str | Path, *, source: MediaEventSource | None = None
) -> RunSummary:
    """Corre el motor contra el bus. `source` inyectada permite suscribirse antes
    de disparar el run del media-plane (y testear sin red)."""
    config: ReplayConfig = load_replay_config(config_path)
    if config.patterns_file is None:
        raise ValueError("La configuracion no tiene patterns_file resuelto")

    run_id = control_run_id(config)
    base_dir = config.resolve_path(config.outputs.base_dir)
    artifacts = RunArtifacts(base_dir / run_id)
    artifacts.write_effective_config(config)

    active_patterns = config.patterns_file.active_patterns(config.patterns.active_ids)
    if source is None:
        if config.input.type != "bus":
            raise ValueError(f"run_live requiere input.type='bus', no {config.input.type!r}")
        source = build_bus_source(config, run_id)

    return execute_over_source(
        config=config,
        source=source,
        control_run_id=run_id,
        artifacts=artifacts,
        active_patterns=active_patterns,
    )
```

- [ ] **Step 5: Add the `live` CLI command**

En `src/eovrt_control/cli.py`, importar `run_live` junto a `run_replay` y agregar el
subcomando debajo de `replay`:

```python
@app.command()
def live(config: Path) -> None:
    """Consume el bus del media-plane en vivo (input.type='bus')."""
    summary = run_live(config)
    console.print(f"Control run: {summary.control_run_id}")
    console.print(f"Media run: {summary.media_run_id}")
    console.print(f"Unidades procesadas: {summary.units_processed}")
    console.print(f"Alertas: {summary.alerts_count}")
    if summary.bus_dropped_events:
        console.print(
            f"[yellow]Bus degradado:[/yellow] {summary.bus_dropped_events} eventos perdidos"
        )
    console.print(f"Resumen: {summary.output_files['summary']}")
```

**Advertencia operativa a documentar en el docstring del comando:** `eovrt-control live`
se suscribe recién al ejecutarse. Si el run del media-plane ya está corriendo, se pierden
los eventos previos. El camino correcto es levantar `live` primero y disparar el run
después (o usar `wait_for_subscriber_ms` del lado del publicador).

- [ ] **Step 6: Write `configs/live_ebe_cr01_cr02.yaml`**

```yaml
# Corrida live (EBE): el motor consume el bus del media-plane (ADR-003, spec 41 SS4).
# Levantar ESTA corrida ANTES de disparar el run en el media-plane: el SUB debe
# estar suscripto antes del primer evento (spec 40 SS3.2 regla 1).
run:
  scenario: EBE
  name: control_live_cr01_cr02
  description: Corrida live 1:1 con un run del media-plane
  experiment_id: null

input:
  type: bus
  bus:
    endpoint: tcp://127.0.0.1:5557
    topics:
      - media.detection.v1.
      - run.lifecycle.v1.
    hwm: 1000
    recv_timeout_ms: 1000
    idle_timeout_s: 300
    finish:
      signal: run_lifecycle
      # Descomentar con el run_id real para habilitar el fallback por polling:
      # poll_url: http://localhost:8080/api/runs/<media_run_id>
      poll_url: null
      poll_interval_s: 5

patterns:
  file: configs/patterns/cr01_cr02_v1.yaml
  active_ids:
    - CR-01
    - CR-02

outputs:
  base_dir: runs
```

- [ ] **Step 7: Run the tests**

```bash
python3 -m pytest tests/test_live.py -q
```

Expected: PASS (5 passed).

- [ ] **Step 8: Verify the CLI and the config validate**

```bash
python3 -m eovrt_control.cli --help 2>/dev/null || python3 -c "
from typer.testing import CliRunner
from eovrt_control.cli import app
print(CliRunner().invoke(app, ['--help']).stdout)"
python3 -c "
from eovrt_control.config import load_replay_config
c = load_replay_config('configs/live_ebe_cr01_cr02.yaml')
print(c.input.type, c.input.bus.endpoint, c.input.bus.finish.signal)"
```

Expected: el help lista `live`; la segunda línea imprime
`bus tcp://127.0.0.1:5557 run_lifecycle`.

- [ ] **Step 9: Full suite + lint**

```bash
python3 -m pytest -q --ignore=tests/labs && python3 -m ruff check src tests
```

Expected: `78 passed`, `All checks passed!`

- [ ] **Step 10: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_control/config.py src/eovrt_control/runtime/live.py \
        src/eovrt_control/cli.py configs/live_ebe_cr01_cr02.yaml tests/test_live.py
git commit -m "feat(runtime): corrida live 1:1 sobre el bus, con config input.type=bus"
```

---

## Task 7: **Gate** — test de paridad replay↔stream

Es el criterio de que "el motor es agnóstico de la fuente" (spec 40 §3.4, spec 41 §10.3).
Corre el mismo fixture por `JsonlSource` y por `BusSource` + un publicador local, y exige
`pattern_events.jsonl` y `alerts.jsonl` **idénticos módulo timestamps de procesamiento**.

**Files:**
- Test: `tests/test_bus_parity.py`

**Qué significa "módulo timestamps de procesamiento":** verificado contra
`contracts/pattern.py` y `contracts/alerts.py` — **ningún** campo de `PatternStateChanged`
ni de `AlertEvent` guarda un instante de procesamiento. `timestamp_ms` y `frame_index`
vienen del evento de medios (idénticos por ambas vías). Los únicos campos que difieren
son `control_run_id` (distinto por corrida) y `alert_id`, que es
`uuid5(NAMESPACE_URL, f"{control_run_id}:{run_id}:{unit_id}:{pattern_id}:{subject_key}")`
(`engine/pattern_engine.py:451-460`) y por lo tanto deriva del `control_run_id`. La
normalización descarta exactamente esos dos.

- [ ] **Step 1: Write the test**

Crear `tests/test_bus_parity.py`:

```python
"""Gate del tramo plataforma: el motor es agnostico de la fuente (spec 40 SS3.4)."""

import json
import socket
import threading
from pathlib import Path

import msgpack
import pytest
import yaml
import zmq

from eovrt_control.runtime.live import run_live
from eovrt_control.runtime.replay import run_replay
from eovrt_control.sources.bus import BusSource

_REPO_ROOT = Path(__file__).resolve().parents[1]
# 12 eventos, un unico run_id ("simulated-media-cr01-cr02-temporal"),
# source_type "video" (NO "video_frame") => pattern_evaluation "computed".
_FIXTURE = _REPO_ROOT / "fixtures/simulated_media/cr01_cr02_temporal/detections.jsonl"
# El pattern set con persistencia/histeresis: confirm_after_frames 3,
# resolve_after_frames 2. Ejercita la maquina de estados a lo largo de frames,
# que es lo que el gate debe probar. `cr01_cr02_v1.yaml` confirma en 1 frame y
# no distinguiria un motor sin memoria de episodio.
_PATTERNS = _REPO_ROOT / "configs/patterns/cr01_cr02_temporal_eval.yaml"

# Campos que dependen del control_run_id y no de la fuente.
_VOLATILE = ("control_run_id", "alert_id")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _normalize(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        for field in _VOLATILE:
            record.pop(field, None)
        records.append(record)
    return records


def _write_config(tmp_path: Path, *, name: str, input_section: dict) -> Path:
    config_path = tmp_path / f"{name}.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "run": {"id": name, "scenario": "DBE", "name": name},
                "input": input_section,
                "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01", "CR-02"]},
                "outputs": {"base_dir": str(tmp_path / "runs")},
            }
        ),
        encoding="utf-8",
    )
    return config_path


class _FixturePublisher:
    """Reproduce el fixture por el bus con el wire format de `bus.envelope.v1`.

    Es un publicador propio a proposito: los dos repos no se importan, asi que
    este test tambien pinea el contrato de wire del lado del consumidor.
    """

    def __init__(self, endpoint: str, media_run_id: str) -> None:
        self._media_run_id = media_run_id
        self._sock = zmq.Context.instance().socket(zmq.XPUB)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.setsockopt(zmq.XPUB_VERBOSE, 1)
        self._sock.setsockopt(zmq.SNDHWM, 10_000)
        self._sock.bind(endpoint)
        self._seq = 0

    def wait_for_subscriber(self, timeout_ms: int = 5000) -> None:
        poller = zmq.Poller()
        poller.register(self._sock, zmq.POLLIN)
        assert dict(poller.poll(timeout=timeout_ms)), "el BusSource no se suscribio a tiempo"
        self._sock.recv()

    def _send(self, topic: str, key: str, payload: bytes) -> None:
        envelope = msgpack.packb(
            {
                "schema_version": "bus.envelope.v1",
                "topic": topic,
                "key": key,
                "seq": self._seq,
                "ts_publish_ms": 0.0,
                "payload": payload,
            },
            use_bin_type=True,
        )
        self._seq += 1
        self._sock.send_multipart([topic.encode("utf-8"), envelope])

    def replay_file(self, path: Path) -> None:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            # Byte-compatible: el payload es la linea tal cual (spec 40 SS3.1).
            self._send(
                f"media.detection.v1.{self._media_run_id}",
                event["source"]["source_id"],
                line.encode("utf-8"),
            )

    def finish(self) -> None:
        payload = json.dumps(
            {
                "schema_version": "run.lifecycle.v1",
                "event": "run_finished",
                "media_run_id": self._media_run_id,
                "status": "succeeded",
            }
        ).encode("utf-8")
        self._send(f"run.lifecycle.v1.{self._media_run_id}", self._media_run_id, payload)

    def close(self) -> None:
        self._sock.close(linger=0)


@pytest.mark.integration
def test_replay_and_stream_produce_identical_pattern_events_and_alerts(tmp_path) -> None:
    assert _FIXTURE.exists(), f"falta el fixture temporal: {_FIXTURE}"
    media_run_id = json.loads(_FIXTURE.read_text(encoding="utf-8").splitlines()[0])["run_id"]

    # (a) Replay desde archivo.
    replay_summary = run_replay(
        _write_config(
            tmp_path, name="replay", input_section={"type": "media_jsonl", "path": str(_FIXTURE)}
        )
    )

    # (b) Stream por el bus. Orden obligatorio: publicador bind -> BusSource
    # suscripto -> recien entonces se publican los eventos.
    endpoint = f"tcp://127.0.0.1:{_free_port()}"
    publisher = _FixturePublisher(endpoint, media_run_id)
    live_config = _write_config(
        tmp_path,
        name="live",
        input_section={
            "type": "bus",
            "bus": {"endpoint": endpoint, "recv_timeout_ms": 200, "idle_timeout_s": 30},
        },
    )
    source = BusSource(endpoint=endpoint, control_run_id="live", recv_timeout_ms=200,
                       idle_timeout_s=30.0)
    publisher.wait_for_subscriber()

    live_summary: dict = {}

    def _consume() -> None:
        live_summary["value"] = run_live(live_config, source=source)

    consumer = threading.Thread(target=_consume, name="live-consumer")
    consumer.start()
    publisher.replay_file(_FIXTURE)
    publisher.finish()
    consumer.join(timeout=60.0)
    publisher.close()

    assert not consumer.is_alive(), "run_live no cerro con run_finished"
    stream_summary = live_summary["value"]

    # Sin perdidas, no hay corrida degradada por el bus.
    assert stream_summary.bus_dropped_events == 0
    assert "bus_dropped_events" not in stream_summary.degradation_causes
    assert stream_summary.source == "bus"
    assert replay_summary.source == "jsonl"

    # Mismos contadores.
    assert stream_summary.units_processed == replay_summary.units_processed
    assert stream_summary.alerts_count == replay_summary.alerts_count
    assert stream_summary.pattern_events_count == replay_summary.pattern_events_count
    assert stream_summary.alerts_count > 0, "el fixture debe producir al menos una alerta"

    # Mismos artefactos, modulo control_run_id/alert_id.
    runs = tmp_path / "runs"
    for artifact in ("pattern_events.jsonl", "alerts.jsonl"):
        assert _normalize(runs / "live" / artifact) == _normalize(runs / "replay" / artifact), (
            f"{artifact} difiere entre replay y stream"
        )
```

- [ ] **Step 2: Register the `integration` marker**

En `pyproject.toml` del control-plane, dentro de `[tool.pytest.ini_options]`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
markers = [
    "integration: usa ZeroMQ sobre localhost (spec 40 SS3.4, test de paridad)",
]
```

- [ ] **Step 3: Run the gate**

```bash
cd /home/simonll4/projects/e-ovrt_control-plane
python3 -m pytest tests/test_bus_parity.py -q -v
```

Expected: PASS. Si falla por `assert stream_summary.units_processed == ...`, el
publicador está enviando antes de que el SUB se suscriba: verificar que
`publisher.wait_for_subscriber()` corre **antes** de `replay_file`.

Si falla por `alerts.jsonl difiere`, imprimir el primer par distinto:

```bash
python3 -m pytest tests/test_bus_parity.py -q -x --tb=long
```

- [ ] **Step 4: Full suite + lint**

```bash
python3 -m pytest -q --ignore=tests/labs && python3 -m ruff check src tests
```

Expected: `79 passed`, `All checks passed!`

- [ ] **Step 5: Corrida live end-to-end real (criterio de terminado, spec 41 §11)**

Dos terminales. **Terminal 1 (media-plane):**

```bash
cd /home/simonll4/projects/e-ovrt_media-plane
source .venv/bin/activate
EOVRT_MODEL_REF=grounding-dino/gdino-tiny \
  uvicorn --factory eovrt_media.service.app:create_app --port 8080
# Esperar /readyz (GDINO tarda 30-60 s en cargar).
```

**Terminal 2 (control-plane), ANTES de disparar el run:**

```bash
cd /home/simonll4/projects/e-ovrt_control-plane
source .venv/bin/activate 2>/dev/null || true
python3 -m eovrt_control.cli live configs/live_ebe_cr01_cr02.yaml
```

**Terminal 3 (disparo):**

```bash
curl -X POST http://localhost:8080/api/runs -H "Content-Type: application/json" -d '{
  "ingest": {"plugin": "video_file", "config": {"path": "<ruta absoluta a un video>"}},
  "prompts": {"set_inline": {"id": "cr01_cr02", "classes": [
      {"id": "person", "phrasings": {"default": ["person"]}},
      {"id": "helmet", "phrasings": {"default": ["helmet"]}},
      {"id": "vest",   "phrasings": {"default": ["safety vest"]}}]},
    "active_ids": ["person", "helmet", "vest"]},
  "bus": {"enabled": true, "endpoint": "tcp://127.0.0.1:5557"},
  "run": {"name": "live_e2e", "stride": 1}
}'
```

Verificar en el `summary.json` de la corrida del control-plane:

```bash
python3 -c "
import json, sys, pathlib
p = sorted(pathlib.Path('runs').iterdir(), key=lambda d: d.stat().st_mtime)[-1]
s = json.loads((p / 'summary.json').read_text())
print({k: s[k] for k in ('control_run_id','media_run_id','source','bus_dropped_events',
                          'degraded','units_processed','alerts_count')})"
```

Expected: `source: "bus"`, `media_run_id` == el `run_id` que devolvió el POST,
`units_processed` == líneas de `detections.jsonl` del media-plane, y la corrida del
control-plane cerró sola al llegar el `run_finished` (no hubo que matarla).

**Archivar la evidencia** (`runs/` es git-ignored en ambos planos, lección de los docs
31/33): copiar los dos `summary.json` a
`docs/operacion/datos/37-<fecha>-live-e2e-{media,control}-summary.json`.

- [ ] **Step 6: Commit (sólo si el usuario lo pidió)**

```bash
git add tests/test_bus_parity.py pyproject.toml
git commit -m "test(bus): gate de paridad replay<->stream sobre ZeroMQ localhost"
```

---

## Cierre: documentación y deuda

- [ ] **Step 1: Write `docs/operacion/37-plan2-bus-y-live-resultados.md` (repo `docs`)**

Mismo formato que los docs 34/35. Debe registrar, con números medidos y no supuestos:
la corrida live E2E (run ids, unidades, alertas, `bus_dropped_events`), el gate de
paridad en verde, y el conteo final de la suite. Citar la evidencia archivada en
`docs/operacion/datos/`.

- [ ] **Step 2: Update the handoff**

Marcar en `docs/operacion/36-handoff-plan2-bus-y-live.md` §3 los ítems 2 y 3 como hechos,
y anotar en §4 la deuda que este plan **no** absorbió:

1. `track_id` sigue sin producirse (spec 42 §3) — `granularity: subject` sigue viviendo
   sólo en fixtures. Es el siguiente bloqueante para G1 y los overlays por persona.
2. Purga de `self._state` del motor: **ahora importa de verdad**, porque una corrida live
   sobre RTSP es de duración indefinida. Agendado para el servicio mínimo (ítem 4).
3. `experiment_id` no viaja aún en el `POST /api/runs` del media-plane (spec 42 §4.1):
   el summary del control-plane lo tiene desde su config, el del media-plane no. La
   cadena de reconstrucción (spec 40 §2) queda a medias hasta el ítem 5.
4. `BusSource` no expone `ts_receive_ms` por unidad (spec 41 §8.3) — insumo de
   `t_capture→alert`, que se implementa en el ítem 5.
5. El `run_finished` de `execute_run` no distingue `stalled` (el `RunManager` sí). El
   consumidor cierra igual; si alguna vez el status importa aguas abajo, hay que
   moverlo al `RunManager`.

- [ ] **Step 3: Update the two `CLAUDE.md`**

- `e-ovrt_media-plane/CLAUDE.md`: sección "Architecture" — mencionar que
  `bus.enabled: true` en la config de corrida (o `"bus"` en el `POST /api/runs`) activa el
  `BusPublishingArtifactWriter` que publica `media.detection.v1.<run_id>` y
  `run.lifecycle.v1.<run_id>` por ZeroMQ XPUB.
- `projects/CLAUDE.md`: sección `e-ovrt_media-plane` — agregar que los dos planos se
  acoplan en EBE por un bus ZeroMQ, además del acople por archivo en DBE.

- [ ] **Step 4: Verificación final antes de declarar el plan terminado**

```bash
cd /home/simonll4/projects/e-ovrt_control-plane && python3 -m pytest -q --ignore=tests/labs && python3 -m ruff check src tests
cd /home/simonll4/projects/e-ovrt_media-plane && python3 -m pytest -q && python3 -m ruff check src tests
```

No declarar nada "listo" sin haber pegado la salida de estos cuatro comandos y el
`summary.json` de la corrida live real.
