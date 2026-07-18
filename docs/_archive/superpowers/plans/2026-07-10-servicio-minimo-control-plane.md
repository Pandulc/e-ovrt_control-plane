# Servicio mínimo del control-plane (:8081)

> **EJECUTADO el 2026-07-10.** Las 5 tareas están completas. Resultados, evidencia y deuda:
> `docs/operacion/38-servicio-minimo-control-plane.md` (repo `docs`).
>
> **El código de referencia de este plan tenía defectos reales**, hallados por la revisión
> adversarial por tarea y corregidos durante la ejecución. **No lo copies verbatim**: el código
> vigente es el del working tree. Los defectos: `new_control_run_id` devolvía el `run.id` del
> usuario sin validar (path traversal: `"../../etc/evil"` escapaba de `runs_dir`); el router
> respondía 500 a config inválida del cliente (`zmq.ZMQError`, `yaml.YAMLError` fuera de la
> lista blanca); `_execute` con `except Exception` dejaba un run activo fantasma permanente
> ante un `BaseException`; `_load_config` no forzaba `outputs.base_dir` en la rama por
> referencia (artefactos fuera de `runs_dir`, invisibles para `get()`); reusar un `run.id`
> reportaba una corrida fallida como exitosa; `alerts()` moría con 500 ante una línea corrupta;
> y **el gate de la Task 5 era vacuo** (no podía fallar bajo la mutación que decía detectar —
> se reemplazó por un gate estructural sobre `subscribed`). Ver doc 38 §6.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el control-plane deje de ser solo CLI y exponga un servicio HTTP mínimo
(FastAPI, :8081) que dispare corridas `replay` o `live`, reporte estado y sirva las alertas.

**Architecture:** Mismo patrón que el media-plane: `create_app` + `RunManager` con **un run
activo por vez**. El runtime ya existe (`runtime/core.py:execute_over_source`); el servicio es
una cáscara HTTP sobre él. La única sutileza real: en `mode: live` el `BusSource` se construye
—y por lo tanto **se suscribe**— dentro del handler, **antes de devolver el 201**, porque el
orquestador dispara el media-plane recién después de esa respuesta (spec 44 §79).

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, Pydantic v2, typer, pytest + `TestClient`
(requiere `httpx`), pyzmq/msgpack (ya presentes).

## Global Constraints

- **Nunca commitear sin pedido explícito del usuario en ese turno.** Los pasos "Commit" de este
  plan se ejecutan sólo si el usuario lo pide. Si no, dejar el working tree y avisar.
- **Nunca `Co-Authored-By`.** **Nada en GitHub; todo local.**
- **Contratos SIEMPRE aditivos, sin bump de `schema_version`.**
- **Un run activo por vez.** Segundo `POST /api/runs` con un run corriendo ⇒ **409**.
- **En `mode: live` el `BusSource` se suscribe ANTES de que el handler devuelva 201**
  (spec 40 §3.2 regla 1). Si la suscripción viviera en el hilo de ejecución, se perderían los
  primeros eventos y la corrida arrancaría degradada.
- **Config por payload o por referencia** (ADR-009). **Por payload: todas las rutas absolutas**
  (regla anti-ambigüedad, spec 41 §9); por referencia, las rutas relativas se resuelven contra
  el YAML, como ya hace `load_replay_config`.
- **Explícitamente fuera** (ADR-008 / E-12): sesiones, auth, concurrencia de corridas,
  retención, gestión de modelos. **La CLI se conserva completa** (`replay`, `live`,
  `validate-config`, `evaluate-alerts`, `export-alerts-csv`): es el camino offline y el fallback
  declarado del orden de sacrificio.
- **El `run_id` se usa para construir rutas bajo `runs_dir`**: validarlo contra
  `^[A-Za-z0-9_-]+$` antes de tocar el filesystem (mismo criterio que
  `e-ovrt_media-plane/src/eovrt_media/service/run_ids.py`).
- `ruff` `line-length = 100`, `target-version = "py311"`. Comentarios y docstrings en español,
  **sin tildes dentro del código** (imitar los archivos vecinos).
- **Entorno:** `python3` del sistema NO tiene las deps. Usar siempre
  `/home/simonll4/projects/e-ovrt_control-plane/.venv/bin/python`.
- **Baseline MEDIDA (2026-07-10):** `pytest -q --ignore=tests/labs` → **89 passed**;
  `ruff check src tests` limpio. `tests/labs/` falla por `numpy` ausente (conocido): correr
  siempre con `--ignore=tests/labs`.

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `src/eovrt_control/runtime/core.py` (mod.) | `RunProgress`, `PreparedRun`, `prepare_run()`, param `progress` en `execute_over_source`. |
| `src/eovrt_control/runtime/replay.py` (mod.) | `run_replay_from_config()`; `run_replay()` delega. |
| `src/eovrt_control/runtime/live.py` (mod.) | `run_live_from_config()`; `run_live()` delega. |
| `src/eovrt_control/config.py` (mod.) | `load_replay_config_data()` (config por payload, rutas absolutas). |
| `src/eovrt_control/service/settings.py` (nuevo) | `ServiceSettings.from_env()` (`EOVRT_CONTROL_RUNS_DIR`). |
| `src/eovrt_control/service/run_ids.py` (nuevo) | Validación del `run_id` + `new_control_run_id()`. |
| `src/eovrt_control/service/run_request.py` (nuevo) | `ControlRunRequest` (payload o referencia). |
| `src/eovrt_control/service/run_manager.py` (nuevo) | Un run activo, 409, suscripción sincrónica en live, estado, alertas. |
| `src/eovrt_control/service/app.py` (nuevo) | `create_app()` + lifespan. |
| `src/eovrt_control/service/routers/{health,runs,config}.py` (nuevos) | Los 7 endpoints. |
| `src/eovrt_control/cli.py` (mod.) | Subcomando `serve`. |
| `pyproject.toml` (mod.) | deps `fastapi`, `uvicorn[standard]`; dev `httpx`. |
| `tests/test_service_api.py` (nuevo) | Endpoints, 409, 404, validación. |
| `tests/test_service_live.py` (nuevo) | **Gate**: corrida live por API contra un bus real. |

**Fuera de alcance** (agendado, no implementar): `POST /runs/{id}/stop`, WS/SSE de alertas
(spec 41 §5 lo difiere explícitamente: "Polling; WS/SSE diferido"), retención de `runs/`,
evaluadores D1, publisher `control.alert.v1.*`.

---

## Task 1: `RunProgress` y `prepare_run` — el runtime, disparable desde un objeto config

Hoy `run_replay`/`run_live` sólo aceptan un **path**. El servicio recibe config por payload, y
necesita (a) preparar la corrida y obtener el `control_run_id` **antes** de construir el
`BusSource`, y (b) contadores en vivo. Refactor aditivo, sin cambio de comportamiento.

**Files:**
- Modify: `src/eovrt_control/runtime/core.py`
- Modify: `src/eovrt_control/runtime/replay.py`
- Modify: `src/eovrt_control/runtime/live.py`
- Test: `tests/test_runtime_progress.py` (nuevo)

**Interfaces:**
- Produces:
  - `@dataclass class RunProgress` con `units_processed: int = 0`, `units_failed: int = 0`,
    `errors_count: int = 0`, `pattern_events_count: int = 0`, `alerts_count: int = 0`,
    `bus_dropped_events: int = 0`.
  - `@dataclass class PreparedRun` con `control_run_id: str`, `artifacts: RunArtifacts`,
    `active_patterns: list[PatternDefinition]`.
  - `prepare_run(config: ReplayConfig) -> PreparedRun` (valida `patterns_file`, crea el dir de
    artefactos y escribe `effective_config.yaml`).
  - `execute_over_source(*, config, source, control_run_id, artifacts, active_patterns,
    progress: RunProgress | None = None) -> RunSummary`
  - `run_replay_from_config(config: ReplayConfig, *, prepared: PreparedRun | None = None,
    progress: RunProgress | None = None) -> RunSummary`
  - `run_live_from_config(config: ReplayConfig, *, source: MediaEventSource | None = None,
    prepared: PreparedRun | None = None, progress: RunProgress | None = None) -> RunSummary`

- [ ] **Step 1: Write the failing test**

Crear `tests/test_runtime_progress.py`:

```python
import json
from pathlib import Path

import yaml

from eovrt_control.config import load_replay_config
from eovrt_control.runtime.core import RunProgress, prepare_run
from eovrt_control.runtime.replay import run_replay, run_replay_from_config

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATTERNS = _REPO_ROOT / "configs/patterns/cr01_cr02_v1.yaml"


def _event(unit_id: str) -> dict:
    return {
        "run_id": "media-run",
        "unit_id": unit_id,
        "source": {"source_id": "cam-1", "source_type": "video", "frame_index": 0,
                   "timestamp_ms": 0.0, "width": 640, "height": 480},
        "model": {"name": "mock", "device": "cpu"},
        "prompts": {"prompt_set_id": "cr01_cr02_v1"},
        "detections": [{"detection_id": "p1", "label": "person", "prompt_id": "person",
                        "confidence": 0.9, "bbox_xyxy": [100, 100, 220, 420]}],
    }


def _config_path(tmp_path: Path) -> Path:
    input_path = tmp_path / "detections.jsonl"
    input_path.write_text(json.dumps(_event("u1")) + "\n", encoding="utf-8")
    config_path = tmp_path / "replay.yaml"
    config_path.write_text(
        yaml.safe_dump({
            "run": {"id": "prog-run", "scenario": "DBE", "name": "prog"},
            "input": {"type": "media_jsonl", "path": str(input_path)},
            "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
            "outputs": {"base_dir": str(tmp_path / "runs")},
        }),
        encoding="utf-8",
    )
    return config_path


def test_prepare_run_writes_effective_config_and_resolves_patterns(tmp_path) -> None:
    config = load_replay_config(_config_path(tmp_path))

    prepared = prepare_run(config)

    assert prepared.control_run_id == "prog-run"
    assert prepared.artifacts.effective_config_path.exists()
    assert [p.id for p in prepared.active_patterns] == ["CR-01"]


def test_progress_is_updated_during_the_run(tmp_path) -> None:
    config = load_replay_config(_config_path(tmp_path))
    progress = RunProgress()

    summary = run_replay_from_config(config, progress=progress)

    assert progress.units_processed == summary.units_processed == 1
    assert progress.alerts_count == summary.alerts_count == 1
    assert progress.pattern_events_count == summary.pattern_events_count
    assert progress.errors_count == 0
    assert progress.bus_dropped_events == 0


def test_prepare_run_can_be_reused_so_the_run_id_is_stable(tmp_path) -> None:
    """El servicio necesita el control_run_id ANTES de correr (para suscribir el bus)."""
    config = load_replay_config(_config_path(tmp_path))
    prepared = prepare_run(config)

    summary = run_replay_from_config(config, prepared=prepared)

    assert summary.control_run_id == prepared.control_run_id


def test_run_replay_from_path_still_works(tmp_path) -> None:
    summary = run_replay(_config_path(tmp_path))
    assert summary.units_processed == 1
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/simonll4/projects/e-ovrt_control-plane
.venv/bin/python -m pytest tests/test_runtime_progress.py -q
```

Expected: FAIL — `ImportError: cannot import name 'RunProgress'`.

- [ ] **Step 3: Add `RunProgress`, `PreparedRun` and `prepare_run` to `runtime/core.py`**

Agregar los imports `from dataclasses import dataclass` y `from eovrt_control.sinks.artifacts
import RunArtifacts` (ya está), y **después** de `control_run_id()`:

```python
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
```

- [ ] **Step 4: Thread `progress` through `execute_over_source`**

En `runtime/core.py`, cambiar la firma:

```python
def execute_over_source(
    *,
    config: ReplayConfig,
    source: MediaEventSource,
    control_run_id: str,
    artifacts: RunArtifacts,
    active_patterns: list[PatternDefinition],
    progress: RunProgress | None = None,
) -> RunSummary:
```

Dentro del bucle, en la rama de error (justo después de `errors_count += 1` y del `if
error.line_number is not None: units_failed += 1`), agregar:

```python
                    if progress is not None:
                        progress.errors_count = errors_count
                        progress.units_failed = units_failed
                    continue
```

(reemplaza el `continue` que ya estaba ahí).

Y al final del cuerpo del bucle, después de `metric_sink.write(...)`:

```python
                if progress is not None:
                    progress.units_processed = units_processed
                    progress.pattern_events_count = pattern_events_count
                    progress.alerts_count = alerts_count
                    progress.bus_dropped_events = source.dropped_events
```

Y después de calcular `bus_dropped_events` (fuera del `try/finally`), agregar:

```python
    if progress is not None:
        progress.bus_dropped_events = bus_dropped_events
```

- [ ] **Step 5: Rewrite `runtime/replay.py`**

```python
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
```

- [ ] **Step 6: Rewrite `runtime/live.py`**

Conservar `build_bus_source` **tal cual está** y reemplazar `run_live` por:

```python
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
```

Ajustar los imports de `live.py` a:

```python
from eovrt_control.config import ReplayConfig, load_replay_config
from eovrt_control.contracts.metrics import RunSummary
from eovrt_control.runtime.core import PreparedRun, RunProgress, execute_over_source, prepare_run
from eovrt_control.sinks.artifacts import RunArtifacts  # (si ya no se usa, borrarlo)
from eovrt_control.sources.base import MediaEventSource
from eovrt_control.sources.bus import BusSource
```

**Gotcha:** `run_live` ya no valida `input.type` cuando recibe una `source` inyectada — igual que
antes. Esa validación pasa a ser responsabilidad del servicio (Task 3), que sí la hace. No la
agregues acá: rompería `tests/test_live.py` y `tests/test_bus_parity.py`, que inyectan la fuente.

- [ ] **Step 7: Run the new tests**

```bash
.venv/bin/python -m pytest tests/test_runtime_progress.py -q
```

Expected: PASS (4 passed).

- [ ] **Step 8: Full suite — el refactor no cambia comportamiento**

```bash
.venv/bin/python -m pytest -q --ignore=tests/labs && .venv/bin/python -m ruff check src tests
```

Expected: `93 passed` (89 + 4), `All checks passed!`. Los tests que prueban que el refactor no
rompió nada son `tests/test_replay.py`, `tests/test_live.py` y **`tests/test_bus_parity.py`** (el
gate del tramo anterior).

- [ ] **Step 9: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_control/runtime tests/test_runtime_progress.py
git commit -m "refactor(runtime): prepare_run + RunProgress; disparar corridas desde un config"
```

---

## Task 2: Config por payload, request y `run_id` seguro

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/eovrt_control/config.py`
- Create: `src/eovrt_control/service/__init__.py` (vacío)
- Create: `src/eovrt_control/service/settings.py`
- Create: `src/eovrt_control/service/run_ids.py`
- Create: `src/eovrt_control/service/run_request.py`
- Test: `tests/test_service_request.py` (nuevo)

**Interfaces:**
- Produces:
  - `config.load_replay_config_data(data: dict[str, Any]) -> ReplayConfig`
  - `service.settings.ServiceSettings` (frozen dataclass) con `runs_dir: Path`, `.from_env(env=None)`
  - `service.run_ids.RUN_ID_RE`, `is_valid_run_id(run_id) -> bool`,
    `require_valid_run_id(run_id) -> None` (levanta `HTTPException` 404),
    `new_control_run_id(config: ReplayConfig) -> str`
  - `service.run_request.ControlRunRequest` con `mode: Literal["replay","live"]`,
    `config_path: str | None`, `config: dict | None`, `experiment_id: str | None`

- [ ] **Step 1: Add the dependencies**

En `pyproject.toml`:

```toml
dependencies = [
    "pydantic",
    "pyyaml",
    "typer",
    "rich",
    "pyzmq",
    "msgpack",
    "fastapi>=0.110",
    "uvicorn[standard]",
]

[project.optional-dependencies]
dev = [
    "pytest",
    "ruff",
    "httpx",
]
```

```bash
cd /home/simonll4/projects/e-ovrt_control-plane
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -c "import fastapi, uvicorn, httpx; print('ok')"
```

Expected: `ok`.

- [ ] **Step 2: Write the failing test**

Crear `tests/test_service_request.py`:

```python
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException
from pydantic import ValidationError

from eovrt_control.config import load_replay_config_data
from eovrt_control.service.run_ids import is_valid_run_id, new_control_run_id, require_valid_run_id
from eovrt_control.service.run_request import ControlRunRequest
from eovrt_control.service.settings import ServiceSettings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATTERNS = _REPO_ROOT / "configs/patterns/cr01_cr02_v1.yaml"


def _payload(tmp_path: Path, **overrides) -> dict:
    data = {
        "run": {"scenario": "DBE", "name": "por_payload"},
        "input": {"type": "media_jsonl", "path": str(tmp_path / "detections.jsonl")},
        "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
        "outputs": {"base_dir": str(tmp_path / "runs")},
    }
    data.update(overrides)
    return data


def test_load_config_data_resolves_the_patterns_file(tmp_path) -> None:
    config = load_replay_config_data(_payload(tmp_path))

    assert config.patterns_file is not None
    assert config.patterns_file.pattern_set.id == "cr01_cr02_v1"
    assert config.config_path is None


def test_config_by_payload_rejects_a_relative_patterns_path(tmp_path) -> None:
    data = _payload(tmp_path)
    data["patterns"]["file"] = "configs/patterns/cr01_cr02_v1.yaml"

    with pytest.raises(ValueError, match="ruta absoluta"):
        load_replay_config_data(data)


def test_config_by_payload_rejects_a_relative_input_path(tmp_path) -> None:
    data = _payload(tmp_path)
    data["input"]["path"] = "runs/latest/detections.jsonl"

    with pytest.raises(ValueError, match="ruta absoluta"):
        load_replay_config_data(data)


def test_request_requires_exactly_one_config_source() -> None:
    with pytest.raises(ValidationError, match="config_path"):
        ControlRunRequest(mode="replay")
    with pytest.raises(ValidationError, match="config_path"):
        ControlRunRequest(mode="replay", config_path="/tmp/a.yaml", config={"run": {}})


def test_request_accepts_either_source() -> None:
    assert ControlRunRequest(mode="replay", config_path="/tmp/a.yaml").config is None
    assert ControlRunRequest(mode="live", config={"run": {}}).config_path is None


def test_request_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ControlRunRequest(mode="replay", config_path="/tmp/a.yaml", modo="replay")


def test_run_id_validation_rejects_path_traversal() -> None:
    assert is_valid_run_id("run_2026-07-10_abc")
    assert not is_valid_run_id("../etc/passwd")
    assert not is_valid_run_id("a/b")
    with pytest.raises(HTTPException) as exc:
        require_valid_run_id("../x")
    assert exc.value.status_code == 404


def test_new_control_run_id_respects_an_explicit_id(tmp_path) -> None:
    data = _payload(tmp_path)
    data["run"]["id"] = "mi-corrida"
    config = load_replay_config_data(data)

    assert new_control_run_id(config) == "mi-corrida"


def test_new_control_run_id_is_unique_when_not_declared(tmp_path) -> None:
    config = load_replay_config_data(_payload(tmp_path))

    first, second = new_control_run_id(config), new_control_run_id(config)

    assert first != second, "dos corridas en el mismo segundo colisionarian"
    assert first.startswith("por_payload_")
    assert is_valid_run_id(first)


def test_settings_read_runs_dir_from_env(tmp_path) -> None:
    settings = ServiceSettings.from_env({"EOVRT_CONTROL_RUNS_DIR": str(tmp_path / "r")})

    assert settings.runs_dir == (tmp_path / "r").resolve()
    assert ServiceSettings.from_env({}).runs_dir == Path("runs").resolve()
```

- [ ] **Step 3: Run to verify failure**

```bash
.venv/bin/python -m pytest tests/test_service_request.py -q
```

Expected: FAIL — `ImportError: cannot import name 'load_replay_config_data'`.

- [ ] **Step 4: Add `load_replay_config_data` to `config.py`**

Al final de `config.py`:

```python
# Rutas que, si la config viene por PAYLOAD, deben ser absolutas: sin `config_path`
# no hay contra que resolver una relativa (caeria contra el CWD del servicio, que
# es ambiguo). Regla anti-ambiguedad de spec 41 SS9 / ADR-009.
_PAYLOAD_PATH_FIELDS = ("patterns.file", "input.path", "outputs.base_dir")


def load_replay_config_data(data: dict[str, Any]) -> ReplayConfig:
    """Carga una config recibida por payload (ADR-009), sin archivo de respaldo."""
    config = ReplayConfig.model_validate(data)
    raw_by_field = {
        "patterns.file": config.patterns.file,
        "input.path": config.input.path,
        "outputs.base_dir": config.outputs.base_dir,
    }
    for field in _PAYLOAD_PATH_FIELDS:
        raw = raw_by_field[field]
        if raw is not None and not Path(raw).is_absolute():
            raise ValueError(
                f"Config por payload: `{field}` debe ser una ruta absoluta, no {raw!r}"
            )
    config.patterns_file = load_patterns_file(config.patterns.file)
    return config
```

- [ ] **Step 5: Write `service/__init__.py` and `service/settings.py`**

`src/eovrt_control/service/__init__.py`: archivo vacío.

`src/eovrt_control/service/settings.py`:

```python
"""Configuracion operacional del servicio (ADR-009: NO se centraliza; vive con el servicio)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServiceSettings:
    runs_dir: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ServiceSettings":
        env = os.environ if env is None else env
        return cls(runs_dir=Path(env.get("EOVRT_CONTROL_RUNS_DIR", "runs")).resolve())
```

- [ ] **Step 6: Write `service/run_ids.py`**

```python
"""Validacion del `run_id` como segmento de path, y generacion de ids unicos.

`run_id` se usa para construir rutas bajo `runs_dir`. Se restringe a
alfanumericos, `_` y `-`: descarta `..`, `/` y demas antes de construir
cualquier ruta (defensa en profundidad, mismo criterio que el media-plane).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException

from eovrt_control.config import ReplayConfig

RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def is_valid_run_id(run_id: str) -> bool:
    return bool(RUN_ID_RE.match(run_id))


def require_valid_run_id(run_id: str) -> None:
    """Levanta HTTPException 404 ("run desconocido") si el id no es un segmento seguro."""
    if not is_valid_run_id(run_id):
        raise HTTPException(status_code=404, detail=f"Run desconocido: {run_id}")


def new_control_run_id(config: ReplayConfig) -> str:
    """Id de corrida del servicio. Sufijo unico: el default del runtime tiene
    granularidad de segundo y dos corridas seguidas colisionarian sobre `runs_dir`."""
    if config.run.id:
        return config.run.id
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{config.run.name}_{stamp}_{uuid4().hex[:6]}"
```

- [ ] **Step 7: Write `service/run_request.py`**

```python
"""Contrato del request de corrida del control-plane (spec 41 SS5, ADR-009)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


class ControlRunRequest(BaseModel):
    # extra="forbid": un campo desconocido en el body -> 422, no se ignora en silencio.
    model_config = ConfigDict(extra="forbid")

    mode: Literal["replay", "live"]
    # ADR-009: la config llega por referencia (path a un YAML) o por payload completo.
    config_path: str | None = None
    config: dict[str, Any] | None = None
    # ADR-004: pisa el `run.experiment_id` de la config si viene.
    experiment_id: str | None = None

    @model_validator(mode="after")
    def exactly_one_config_source(self) -> "ControlRunRequest":
        if (self.config_path is None) == (self.config is None):
            raise ValueError(
                "Exactamente uno de `config_path` (por referencia) o `config` (por payload)"
            )
        return self
```

- [ ] **Step 8: Run the tests**

```bash
.venv/bin/python -m pytest tests/test_service_request.py -q
```

Expected: PASS (10 passed).

- [ ] **Step 9: Full suite + lint**

```bash
.venv/bin/python -m pytest -q --ignore=tests/labs && .venv/bin/python -m ruff check src tests
```

Expected: `103 passed`, `All checks passed!`

- [ ] **Step 10: Commit (sólo si el usuario lo pidió)**

```bash
git add pyproject.toml src/eovrt_control/config.py src/eovrt_control/service \
        tests/test_service_request.py
git commit -m "feat(service): config por payload, request y run_id seguro"
```

---

## Task 3: `RunManager` — un run activo, 409, y la suscripción sincrónica del live

El corazón del servicio. **La invariante que justifica este diseño**: en `mode: live` el
`BusSource` se construye dentro de `start_run`, **bajo el lock y antes de arrancar el hilo**, de
modo que cuando el handler devuelve 201 la suscripción ya existe.

**Files:**
- Modify: `src/eovrt_control/sources/base.py` (agregar `request_stop()`)
- Modify: `src/eovrt_control/sources/bus.py` (implementar `request_stop()`)
- Create: `src/eovrt_control/service/run_manager.py`
- Test: `tests/test_run_manager.py` (nuevo)

**Interfaces:**
- Consumes: `prepare_run`, `RunProgress` (Task 1); `ControlRunRequest`, `ServiceSettings`,
  `new_control_run_id` (Task 2); `run_replay_from_config`, `run_live_from_config`,
  `build_bus_source`, `load_replay_config`, `load_replay_config_data`.
- Produces:
  - `MediaEventSource.request_stop() -> None` (default no-op) y `BusSource.request_stop()`
  - `class RunBusyError(RuntimeError)` con atributo `active_run_id: str`
  - `class UnknownRunError(KeyError)`
  - `class RunManager` con `__init__(self, settings: ServiceSettings)`,
    `start_run(request: ControlRunRequest) -> str`, `get(run_id: str) -> dict`,
    `current() -> dict` (levanta `UnknownRunError` si no hay activo),
    `alerts(run_id: str, limit: int | None = None) -> list[dict]`,
    `effective_config() -> dict` (levanta `UnknownRunError` si nunca hubo corrida),
    `join_active(timeout: float) -> None`, `shutdown() -> None`

### Por qué `request_stop()` y no `source.close()` desde el servidor

Una corrida `live` bloquea su hilo dentro de `BusSource.__iter__`, en `poller.poll()` /
`recv_multipart()`. Al apagar el servicio hay que desbloquearla. **Cerrar el socket desde el hilo
del servidor es peligroso, no solo feo:** libzmq no soporta usar un socket desde dos hilos, y un
`close()` concurrente con un `recv_multipart()` en vuelo produce un **`SIGABRT` con una aserción
de `session_base.cpp`** — se observó en este mismo repo durante el plan anterior. La forma segura
es una **parada cooperativa**: el servidor levanta una bandera, y el hilo de la corrida la mira
entre polls y sale por su cuenta, cerrando el socket **desde el hilo que lo creó**.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_run_manager.py`:

```python
import json
from pathlib import Path

import pytest
import yaml

from eovrt_control.service.run_manager import RunBusyError, RunManager, UnknownRunError
from eovrt_control.service.run_request import ControlRunRequest
from eovrt_control.service.settings import ServiceSettings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATTERNS = _REPO_ROOT / "configs/patterns/cr01_cr02_v1.yaml"


def _event(unit_id: str) -> dict:
    return {
        "run_id": "media-run",
        "unit_id": unit_id,
        "source": {"source_id": "cam-1", "source_type": "video", "frame_index": 0,
                   "timestamp_ms": 0.0, "width": 640, "height": 480},
        "model": {"name": "mock", "device": "cpu"},
        "prompts": {"prompt_set_id": "cr01_cr02_v1"},
        "detections": [{"detection_id": "p1", "label": "person", "prompt_id": "person",
                        "confidence": 0.9, "bbox_xyxy": [100, 100, 220, 420]}],
    }


def _detections(tmp_path: Path) -> Path:
    path = tmp_path / "detections.jsonl"
    path.write_text(json.dumps(_event("u1")) + "\n", encoding="utf-8")
    return path


def _payload(tmp_path: Path) -> dict:
    return {
        "run": {"id": "manager-run", "scenario": "DBE", "name": "mgr"},
        "input": {"type": "media_jsonl", "path": str(_detections(tmp_path))},
        "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
    }


@pytest.fixture()
def manager(tmp_path):
    m = RunManager(ServiceSettings(runs_dir=tmp_path / "runs"))
    yield m
    m.shutdown()
    m.join_active(timeout=15.0)


@pytest.fixture()
def bus_endpoint():
    """Un XPUB bindeado que nunca publica: el `BusSource` se suscribe y se queda esperando."""
    import socket

    import zmq

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    endpoint = f"tcp://127.0.0.1:{port}"
    sock = zmq.Context.instance().socket(zmq.XPUB)
    sock.setsockopt(zmq.LINGER, 0)
    sock.bind(endpoint)
    yield endpoint
    sock.close(linger=0)


def test_replay_by_payload_runs_and_reports_succeeded(manager, tmp_path) -> None:
    run_id = manager.start_run(ControlRunRequest(mode="replay", config=_payload(tmp_path)))
    manager.join_active(timeout=30.0)

    assert run_id == "manager-run"
    state = manager.get(run_id)
    assert state["status"] == "succeeded"
    assert state["summary"]["alerts_count"] == 1
    assert state["summary"]["source"] == "jsonl"


def test_replay_by_reference_resolves_paths_against_the_yaml(manager, tmp_path) -> None:
    _detections(tmp_path)
    config_path = tmp_path / "replay.yaml"
    config_path.write_text(
        yaml.safe_dump({
            "run": {"id": "ref-run", "scenario": "DBE", "name": "ref"},
            "input": {"type": "media_jsonl", "path": "detections.jsonl"},  # relativa al YAML
            "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
        }),
        encoding="utf-8",
    )

    manager.start_run(ControlRunRequest(mode="replay", config_path=str(config_path)))
    manager.join_active(timeout=30.0)

    assert manager.get("ref-run")["summary"]["units_processed"] == 1


def test_outputs_base_dir_is_forced_to_the_service_runs_dir(manager, tmp_path) -> None:
    payload = _payload(tmp_path)
    payload["outputs"] = {"base_dir": "/tmp/deberia-ser-ignorado"}

    manager.start_run(ControlRunRequest(mode="replay", config=payload))
    manager.join_active(timeout=30.0)

    assert (tmp_path / "runs" / "manager-run" / "summary.json").exists()


def test_experiment_id_from_the_request_overrides_the_config(manager, tmp_path) -> None:
    manager.start_run(
        ControlRunRequest(mode="replay", config=_payload(tmp_path), experiment_id="exp-9")
    )
    manager.join_active(timeout=30.0)

    assert manager.get("manager-run")["summary"]["experiment_id"] == "exp-9"


def _idle_live_payload(endpoint: str, idle_timeout_s: float = 2.0) -> dict:
    """Corrida live contra un bus que nunca publica: se queda activa hasta el idle timeout.

    Es la unica forma DETERMINISTA de mantener un run activo mientras el test hace
    otra cosa. Un replay sobre un archivo grande es una carrera: el motor procesa
    decenas de miles de unidades por segundo y puede terminar antes del segundo POST.
    """
    return {
        "run": {"id": "idle-run", "scenario": "EBE", "name": "idle"},
        "input": {"type": "bus", "bus": {"endpoint": endpoint, "recv_timeout_ms": 100,
                                         "idle_timeout_s": idle_timeout_s}},
        "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
    }


def test_second_run_while_active_raises_run_busy(manager, tmp_path, bus_endpoint) -> None:
    manager.start_run(ControlRunRequest(mode="live", config=_idle_live_payload(bus_endpoint)))
    try:
        with pytest.raises(RunBusyError) as exc:
            manager.start_run(ControlRunRequest(mode="replay", config=_payload(tmp_path)))
        assert exc.value.active_run_id == "idle-run"
    finally:
        manager.shutdown()
        manager.join_active(timeout=30.0)


def test_shutdown_unblocks_a_live_run_cooperatively(manager, bus_endpoint) -> None:
    """`shutdown()` NO cierra el socket desde este hilo (SIGABRT de libzmq): levanta
    una bandera y el hilo de la corrida sale solo."""
    manager.start_run(
        ControlRunRequest(mode="live", config=_idle_live_payload(bus_endpoint, idle_timeout_s=300))
    )

    manager.shutdown()
    manager.join_active(timeout=15.0)

    assert manager.get("idle-run")["status"] == "succeeded"
    with pytest.raises(UnknownRunError):
        manager.current()


def test_mode_must_match_the_input_type(manager, tmp_path) -> None:
    with pytest.raises(ValueError, match="mode='live'"):
        manager.start_run(ControlRunRequest(mode="live", config=_payload(tmp_path)))


def test_unknown_run_raises(manager) -> None:
    with pytest.raises(UnknownRunError):
        manager.get("no-existe")
    with pytest.raises(UnknownRunError):
        manager.current()
    with pytest.raises(UnknownRunError):
        manager.effective_config()


def test_alerts_are_read_from_the_run_artifacts(manager, tmp_path) -> None:
    manager.start_run(ControlRunRequest(mode="replay", config=_payload(tmp_path)))
    manager.join_active(timeout=30.0)

    alerts = manager.alerts("manager-run")

    assert len(alerts) == 1
    assert alerts[0]["pattern_id"] == "CR-01"
    assert alerts[0]["control_run_id"] == "manager-run"
    assert manager.alerts("manager-run", limit=0) == []


def test_effective_config_is_available_after_the_run(manager, tmp_path) -> None:
    manager.start_run(ControlRunRequest(mode="replay", config=_payload(tmp_path)))
    manager.join_active(timeout=30.0)

    config = manager.effective_config()

    assert config["run"]["id"] == "manager-run"
    assert config["input"]["type"] == "media_jsonl"


def test_a_config_error_leaves_no_active_run_behind(manager, tmp_path) -> None:
    """El fallo ocurre al preparar la corrida, bajo el lock: el slot no queda tomado."""
    payload = _payload(tmp_path)
    payload["patterns"]["active_ids"] = ["NO-EXISTE"]

    with pytest.raises(ValueError):
        manager.start_run(ControlRunRequest(mode="replay", config=payload))

    with pytest.raises(UnknownRunError):
        manager.current()
    # Y el servicio sigue aceptando corridas.
    manager.start_run(ControlRunRequest(mode="replay", config=_payload(tmp_path)))
    manager.join_active(timeout=30.0)
    assert manager.get("manager-run")["status"] == "succeeded"
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/python -m pytest tests/test_run_manager.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'eovrt_control.service.run_manager'`.

- [ ] **Step 3: Add cooperative stop to `MediaEventSource` and `BusSource`**

En `src/eovrt_control/sources/base.py`, dentro de `class MediaEventSource`, después de `close()`:

```python
    def request_stop(self) -> None:
        """Pide a la fuente que termine su iteracion cuanto antes.

        Es la forma SEGURA de desbloquear una fuente que espera en red desde otro
        hilo: cerrarle el socket por debajo es un uso multi-hilo no soportado por
        libzmq. Default: nada que interrumpir (las fuentes finitas se agotan solas).
        """
        return None
```

En `src/eovrt_control/sources/bus.py`, agregar `import threading` y, en `__init__`, junto al
resto del estado:

```python
        self._stop = threading.Event()
```

Agregar el método:

```python
    def request_stop(self) -> None:
        """Parada cooperativa: el hilo que itera la ve entre polls y sale solo.

        Nunca toca el socket: cerrarlo desde otro hilo mientras `recv_multipart`
        esta en vuelo aborta el proceso (asercion de libzmq en session_base.cpp).
        """
        self._stop.set()
```

Y en `__iter__`, como **primera** sentencia del `while True`:

```python
        while True:
            if self._stop.is_set():
                logger.info("bus: parada solicitada; se cierra la corrida")
                return
```

- [ ] **Step 4: Write `service/run_manager.py`**

```python
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

            config = self._load_config(request)
            expected = _MODE_TO_INPUT_TYPE[request.mode]
            if config.input.type != expected:
                raise ValueError(
                    f"mode={request.mode!r} requiere input.type={expected!r}, "
                    f"no {config.input.type!r}"
                )

            config.run.id = new_control_run_id(config)
            prepared = prepare_run(config)

            source: MediaEventSource | None = None
            if request.mode == "live":
                # Construir el BusSource ES suscribirse (spec 40 SS3.2 regla 1).
                # Ocurre ACA, antes de que el handler devuelva 201, porque el
                # orquestador dispara el media-plane recien despues de esa respuesta.
                source = build_bus_source(config, prepared.control_run_id)

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
            for line in fh:
                if not line.strip():
                    continue
                rows.append(json.loads(line))
        if limit is not None:
            rows = rows[:limit]
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
            # El dueno del `runs_dir` es el DESPLIEGUE, no el experimento (ADR-009 SS2).
            data["outputs"] = {**data.get("outputs", {}),
                               "base_dir": str(self._settings.runs_dir)}
            config = load_replay_config_data(data)
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
        active.status = status
        active.error = error
        # ORDEN IMPORTANTE: soltar el slot ANTES de señalizar `finished`. Al reves,
        # un `get()` inmediatamente despues de `join_active()` todavia veria el run
        # como activo y devolveria el estado en vivo en vez del summary del disco.
        with self._lock:
            self._active = None
        active.finished.set()
```

**Gotcha del `get()`**, ya reflejado en el código de arriba: el `RunSummary` del control-plane
**no tiene campo `status`** (a diferencia del media-plane). Una corrida histórica con
`summary.json` es, por construcción, una corrida que llegó al final de `execute_over_source`:
eso es `succeeded`. Una corrida que explotó no deja `summary.json`, así que `get()` la reporta
como `UnknownRunError` → 404. Es una limitación conocida, registrada como deuda 4 en el cierre.
No la "arregles" inventando un `status` en el summary sin agendarlo.

- [ ] **Step 5: Run the tests**

```bash
.venv/bin/python -m pytest tests/test_run_manager.py -q
```

Expected: PASS (11 passed). Corrélo 3 veces: `test_shutdown_unblocks_a_live_run_cooperatively`
usa hilos y ZeroMQ. **Si el proceso muere con `SIGABRT` / `Aborted`**, `shutdown()` está cerrando
el socket en vez de usar `request_stop()` — es exactamente el fallo que este test existe para
detectar.

- [ ] **Step 6: Full suite + lint**

```bash
.venv/bin/python -m pytest -q --ignore=tests/labs && .venv/bin/python -m ruff check src tests
```

Expected: `114 passed`, `All checks passed!`

Prestá atención a `tests/test_bus_source.py` y `tests/test_bus_parity.py`: tocaste `sources/bus.py`
y `sources/base.py`, así que son la red de seguridad del cambio.

- [ ] **Step 7: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_control/sources src/eovrt_control/service/run_manager.py \
        tests/test_run_manager.py
git commit -m "feat(service): RunManager, suscripcion sincronica en live y parada cooperativa"
```

---

## Task 4: La app FastAPI, los 7 endpoints y `eovrt-control serve`

**Files:**
- Create: `src/eovrt_control/service/app.py`
- Create: `src/eovrt_control/service/routers/__init__.py` (vacío)
- Create: `src/eovrt_control/service/routers/health.py`
- Create: `src/eovrt_control/service/routers/runs.py`
- Create: `src/eovrt_control/service/routers/config.py`
- Modify: `src/eovrt_control/cli.py`
- Test: `tests/test_service_api.py` (nuevo)

**Interfaces:**
- Consumes: `RunManager`, `RunBusyError`, `UnknownRunError`, `ControlRunRequest`,
  `ServiceSettings`, `require_valid_run_id`.
- Produces: `service.app.create_app(settings: ServiceSettings | None = None) -> FastAPI`;
  subcomando CLI `serve(host: str = "0.0.0.0", port: int = 8081)`.

**Tabla de endpoints (spec 41 §5):**

| Método | Ruta | Éxito | Errores |
|---|---|---|---|
| `POST` | `/api/runs` | 201 `{"control_run_id": ...}` | 409 run activo; 422 config inválida |
| `GET` | `/api/runs/current` | 200 estado + `progress` | 404 sin run activo |
| `GET` | `/api/runs/{id}` | 200 estado (+ `summary` si terminó) | 404 desconocido |
| `GET` | `/api/runs/{id}/alerts` | 200 lista de `AlertEvent` | 404 desconocido |
| `GET` | `/api/config` | 200 `effective_config` | 404 sin corridas |
| `GET` | `/healthz` | 200 `{"status": "ok"}` | — |
| `GET` | `/readyz` | 200 `{"status": "ready"}` | — |

**`/readyz` siempre responde `ready`**: a diferencia del media-plane, este plano no carga ningún
modelo, así que no hay nada que esperar. Se conserva por simetría de plataforma (lo usa el
compose/healthcheck).

- [ ] **Step 1: Write the failing test**

Crear `tests/test_service_api.py`:

```python
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eovrt_control.service.app import create_app
from eovrt_control.service.settings import ServiceSettings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATTERNS = _REPO_ROOT / "configs/patterns/cr01_cr02_v1.yaml"


def _event(unit_id: str) -> dict:
    return {
        "run_id": "media-run",
        "unit_id": unit_id,
        "source": {"source_id": "cam-1", "source_type": "video", "frame_index": 0,
                   "timestamp_ms": 0.0, "width": 640, "height": 480},
        "model": {"name": "mock", "device": "cpu"},
        "prompts": {"prompt_set_id": "cr01_cr02_v1"},
        "detections": [{"detection_id": "p1", "label": "person", "prompt_id": "person",
                        "confidence": 0.9, "bbox_xyxy": [100, 100, 220, 420]}],
    }


def _payload(tmp_path: Path, run_id: str = "api-run", count: int = 1) -> dict:
    path = tmp_path / f"{run_id}.jsonl"
    path.write_text(
        "\n".join(json.dumps(_event(f"u{i}")) for i in range(count)) + "\n", encoding="utf-8"
    )
    return {
        "run": {"id": run_id, "scenario": "DBE", "name": "api"},
        "input": {"type": "media_jsonl", "path": str(path)},
        "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
    }


@pytest.fixture()
def client(tmp_path):
    app = create_app(ServiceSettings(runs_dir=tmp_path / "runs"))
    with TestClient(app) as c:
        yield c


def test_healthz_and_readyz(client) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


def test_post_run_returns_201_and_the_run_completes(client, tmp_path) -> None:
    response = client.post("/api/runs", json={"mode": "replay", "config": _payload(tmp_path)})

    assert response.status_code == 201
    run_id = response.json()["control_run_id"]
    assert run_id == "api-run"

    client.app.state.manager.join_active(timeout=30.0)
    state = client.get(f"/api/runs/{run_id}")
    assert state.status_code == 200
    assert state.json()["status"] == "succeeded"
    assert state.json()["summary"]["alerts_count"] == 1


def _idle_live_payload(endpoint: str) -> dict:
    """Corrida live contra un bus que nunca publica: queda activa de forma DETERMINISTA.

    Un replay sobre un archivo grande seria una carrera (el motor procesa decenas de
    miles de unidades por segundo y puede terminar antes del segundo request).
    """
    return {
        "run": {"id": "idle-run", "scenario": "EBE", "name": "idle"},
        "input": {"type": "bus", "bus": {"endpoint": endpoint, "recv_timeout_ms": 100,
                                         "idle_timeout_s": 30}},
        "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
    }


@pytest.fixture()
def bus_endpoint():
    import socket

    import zmq

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    endpoint = f"tcp://127.0.0.1:{port}"
    sock = zmq.Context.instance().socket(zmq.XPUB)
    sock.setsockopt(zmq.LINGER, 0)
    sock.bind(endpoint)
    yield endpoint
    sock.close(linger=0)


def test_second_run_while_active_returns_409(client, tmp_path, bus_endpoint) -> None:
    client.post("/api/runs", json={"mode": "live", "config": _idle_live_payload(bus_endpoint)})
    try:
        conflict = client.post("/api/runs", json={"mode": "replay", "config": _payload(tmp_path)})
        assert conflict.status_code == 409
        assert conflict.json()["active_run_id"] == "idle-run"
    finally:
        client.app.state.manager.shutdown()
        client.app.state.manager.join_active(timeout=30.0)


def test_invalid_config_returns_422(client, tmp_path) -> None:
    payload = _payload(tmp_path)
    payload["patterns"]["file"] = "configs/relativa.yaml"  # por payload debe ser absoluta

    response = client.post("/api/runs", json={"mode": "replay", "config": payload})

    assert response.status_code == 422
    assert "absoluta" in response.json()["detail"]


def test_mode_live_with_a_jsonl_config_returns_422(client, tmp_path) -> None:
    response = client.post("/api/runs", json={"mode": "live", "config": _payload(tmp_path)})

    assert response.status_code == 422
    assert "mode='live'" in response.json()["detail"]


def test_unknown_field_in_the_body_returns_422(client, tmp_path) -> None:
    response = client.post("/api/runs", json={"mode": "replay", "configuracion": {}})
    assert response.status_code == 422


def test_current_returns_404_when_no_run_is_active(client) -> None:
    assert client.get("/api/runs/current").status_code == 404


def test_current_reports_the_active_run_and_its_progress(client, bus_endpoint) -> None:
    client.post("/api/runs", json={"mode": "live", "config": _idle_live_payload(bus_endpoint)})
    try:
        current = client.get("/api/runs/current")
        assert current.status_code == 200
        body = current.json()
        assert body["control_run_id"] == "idle-run"
        assert body["status"] == "running"
        assert body["mode"] == "live"
        assert set(body["progress"]) == {
            "units_processed", "units_failed", "errors_count",
            "pattern_events_count", "alerts_count", "bus_dropped_events",
        }
        assert body["progress"]["units_processed"] == 0
    finally:
        client.app.state.manager.shutdown()
        client.app.state.manager.join_active(timeout=30.0)


def test_unknown_run_returns_404(client) -> None:
    assert client.get("/api/runs/no-existe").status_code == 404
    assert client.get("/api/runs/no-existe/alerts").status_code == 404


def test_run_id_with_path_traversal_returns_404(client) -> None:
    assert client.get("/api/runs/..%2F..%2Fetc/alerts").status_code == 404


def test_alerts_endpoint_serves_the_alert_events(client, tmp_path) -> None:
    client.post("/api/runs", json={"mode": "replay", "config": _payload(tmp_path)})
    client.app.state.manager.join_active(timeout=30.0)

    response = client.get("/api/runs/api-run/alerts")

    assert response.status_code == 200
    alerts = response.json()
    assert len(alerts) == 1
    assert alerts[0]["pattern_id"] == "CR-01"
    assert alerts[0]["schema_version"] == "control.alert.v1"
    assert client.get("/api/runs/api-run/alerts?limit=0").json() == []


def test_config_endpoint_returns_404_before_any_run_and_the_config_after(client, tmp_path) -> None:
    assert client.get("/api/config").status_code == 404

    client.post("/api/runs", json={"mode": "replay", "config": _payload(tmp_path)})
    client.app.state.manager.join_active(timeout=30.0)

    config = client.get("/api/config")
    assert config.status_code == 200
    assert config.json()["run"]["id"] == "api-run"
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/python -m pytest tests/test_service_api.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'eovrt_control.service.app'`.

- [ ] **Step 3: Write `service/routers/health.py`**

```python
"""Liveness/readiness. El control-plane no carga modelo: siempre esta listo."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
def readyz() -> dict:
    # A diferencia del media-plane no hay modelo que cargar; se conserva el
    # endpoint por simetria de plataforma (healthcheck del compose).
    return {"status": "ready"}
```

- [ ] **Step 4: Write `service/routers/runs.py`**

**Cuidado con el orden de las rutas:** `/runs/current` debe declararse **antes** que
`/runs/{run_id}`, o FastAPI matchea `current` como un `run_id`.

```python
"""API de corridas del control-plane (spec 41 SS5)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from eovrt_control.service.run_ids import require_valid_run_id
from eovrt_control.service.run_manager import RunBusyError, RunManager, UnknownRunError
from eovrt_control.service.run_request import ControlRunRequest

router = APIRouter(prefix="/api")


def _manager(request: Request) -> RunManager:
    return request.app.state.manager


@router.post("/runs", status_code=201)
def create_run(body: ControlRunRequest, request: Request):
    try:
        control_run_id = _manager(request).start_run(body)
    except RunBusyError as exc:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "active_run_id": exc.active_run_id},
        )
    except (ValueError, FileNotFoundError) as exc:
        # Config invalida, ruta relativa por payload, mode que no matchea input.type.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"control_run_id": control_run_id}


# ANTES de /runs/{run_id}: si no, `current` se matchea como un run_id.
@router.get("/runs/current")
def get_current_run(request: Request):
    try:
        return _manager(request).current()
    except UnknownRunError as exc:
        raise HTTPException(status_code=404, detail="No hay run activo") from exc


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request):
    require_valid_run_id(run_id)
    try:
        return _manager(request).get(run_id)
    except UnknownRunError as exc:
        raise HTTPException(status_code=404, detail=f"Run desconocido: {run_id}") from exc


@router.get("/runs/{run_id}/alerts")
def get_run_alerts(run_id: str, request: Request, limit: int | None = Query(default=None, ge=0)):
    """Fuente de la vista de alertas de la webconsole (spec 44). Polling; WS/SSE diferido."""
    require_valid_run_id(run_id)
    try:
        return _manager(request).alerts(run_id, limit=limit)
    except UnknownRunError as exc:
        raise HTTPException(status_code=404, detail=f"Run desconocido: {run_id}") from exc
```

- [ ] **Step 5: Write `service/routers/config.py`**

```python
"""Config efectiva de la corrida activa (o de la ultima)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from eovrt_control.service.run_manager import UnknownRunError

router = APIRouter(prefix="/api")


@router.get("/config")
def get_effective_config(request: Request):
    try:
        return request.app.state.manager.effective_config()
    except UnknownRunError as exc:
        raise HTTPException(status_code=404, detail="Todavia no hubo ninguna corrida") from exc
```

- [ ] **Step 6: Write `service/app.py`**

```python
"""Factory de la app FastAPI del servicio control-plane (ADR-008)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from eovrt_control.service.routers import config as config_router
from eovrt_control.service.routers import health, runs
from eovrt_control.service.run_manager import RunManager
from eovrt_control.service.settings import ServiceSettings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings: ServiceSettings = app.state.settings
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    app.state.manager = RunManager(settings)
    logger.info("control-plane listo (runs_dir=%s)", settings.runs_dir)
    yield
    # SIGTERM/shutdown: cerrar el bus desbloquea el hilo de una corrida live.
    app.state.manager.shutdown()
    app.state.manager.join_active(timeout=10.0)


def create_app(settings: ServiceSettings | None = None) -> FastAPI:
    settings = settings or ServiceSettings.from_env()
    app = FastAPI(title="eovrt-control-plane", lifespan=_lifespan)
    app.state.settings = settings
    app.include_router(health.router)
    app.include_router(runs.router)
    app.include_router(config_router.router)
    return app
```

`src/eovrt_control/service/routers/__init__.py`: archivo vacío.

- [ ] **Step 7: Add the `serve` subcommand to `cli.py`**

Agregar al final de `cli.py`:

```python
@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Interfaz de escucha."),
    port: int = typer.Option(8081, help="Puerto (el media-plane usa 8080)."),
) -> None:
    """Levanta el servicio HTTP del plano de control (ADR-008)."""
    import uvicorn

    uvicorn.run(
        "eovrt_control.service.app:create_app", host=host, port=port, factory=True
    )
```

- [ ] **Step 8: Run the tests**

```bash
.venv/bin/python -m pytest tests/test_service_api.py -q
```

Expected: PASS (12 passed). Corrélo 3 veces: los tests de `409` y `current` levantan una corrida
live contra un XPUB real.

- [ ] **Step 9: Smoke real del servicio (gate de ADR-008)**

Terminal 1:

```bash
cd /home/simonll4/projects/e-ovrt_control-plane
EOVRT_CONTROL_RUNS_DIR=/tmp/cp-smoke/runs .venv/bin/python -m eovrt_control.cli serve --port 8081
```

Terminal 2 (`curl` puede estar bloqueado en este entorno; usar `urllib`):

```bash
cd /home/simonll4/projects/e-ovrt_control-plane
.venv/bin/python - <<'PY'
import json, urllib.request
def get(path):
    with urllib.request.urlopen(f"http://localhost:8081{path}", timeout=5) as r:
        return r.status, json.loads(r.read())
print("healthz:", get("/healthz"))
print("readyz :", get("/readyz"))
try:
    get("/api/runs/current")
except urllib.error.HTTPError as e:
    print("current sin run activo:", e.code)   # 404
PY
```

Expected: `healthz: (200, {'status': 'ok'})`, `readyz: (200, {'status': 'ready'})`,
`current sin run activo: 404`.

- [ ] **Step 10: Full suite + lint**

```bash
.venv/bin/python -m pytest -q --ignore=tests/labs && .venv/bin/python -m ruff check src tests
```

Expected: `126 passed`, `All checks passed!`

- [ ] **Step 11: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_control/service src/eovrt_control/cli.py tests/test_service_api.py
git commit -m "feat(service): app FastAPI, 7 endpoints y eovrt-control serve"
```

---

## Task 5: **Gate** — corrida `live` disparada por la API contra un bus real

Prueba la invariante que da sentido al servicio: **cuando `POST /api/runs` devuelve 201, el
`BusSource` ya está suscripto**. Si la suscripción viviera en el hilo de ejecución, el publicador
—que arranca después del 201— perdería los primeros eventos y el test lo vería como
`bus_dropped_events > 0` o como unidades faltantes.

**Files:**
- Test: `tests/test_service_live.py` (nuevo)

- [ ] **Step 1: Write the test**

```python
"""Gate del servicio: corrida live disparada por la API, contra un bus ZeroMQ real."""

import json
import socket
from pathlib import Path

import msgpack
import pytest
import zmq
from fastapi.testclient import TestClient

from eovrt_control.service.app import create_app
from eovrt_control.service.settings import ServiceSettings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATTERNS = _REPO_ROOT / "configs/patterns/cr01_cr02_v1.yaml"
_MEDIA_RUN_ID = "media-run-live"

# Un BusSource con los dos prefijos por default emite DOS notificaciones de
# suscripcion; drenar una sola devuelve antes de tiempo (slow joiner).
_EXPECTED_SUBSCRIPTIONS = 2


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _event(unit_id: str, frame_index: int) -> dict:
    return {
        "run_id": _MEDIA_RUN_ID,
        "unit_id": unit_id,
        "source": {"source_id": "cam-1", "source_type": "video", "frame_index": frame_index,
                   "timestamp_ms": frame_index * 100.0, "width": 640, "height": 480},
        "model": {"name": "mock", "device": "cpu"},
        "prompts": {"prompt_set_id": "cr01_cr02_v1"},
        "detections": [{"detection_id": "p1", "label": "person", "prompt_id": "person",
                        "confidence": 0.9, "bbox_xyxy": [100, 100, 220, 420]}],
    }


class _Publisher:
    """Publicador de prueba: pinea el wire format del media-plane sin importarlo."""

    def __init__(self, endpoint: str) -> None:
        self._sock = zmq.Context.instance().socket(zmq.XPUB)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.setsockopt(zmq.XPUB_VERBOSE, 1)
        self._sock.bind(endpoint)
        self._seq = 0

    def wait_for_subscriber(self, expected: int = _EXPECTED_SUBSCRIPTIONS, timeout_ms: int = 5000):
        poller = zmq.Poller()
        poller.register(self._sock, zmq.POLLIN)
        seen = 0
        while seen < expected:
            if not dict(poller.poll(timeout=timeout_ms)):
                raise AssertionError(f"solo llegaron {seen}/{expected} suscripciones")
            self._sock.recv()
            seen += 1

    def _send(self, topic: str, key: str, payload: bytes) -> None:
        envelope = msgpack.packb(
            {"schema_version": "bus.envelope.v1", "topic": topic, "key": key,
             "seq": self._seq, "ts_publish_ms": 0.0, "payload": payload},
            use_bin_type=True,
        )
        self._seq += 1
        self._sock.send_multipart([topic.encode("utf-8"), envelope])

    def publish_events(self, count: int) -> None:
        for i in range(count):
            payload = json.dumps(_event(f"frame_{i:04d}", i)).encode("utf-8")
            self._send(f"media.detection.v1.{_MEDIA_RUN_ID}", "cam-1", payload)

    def finish(self) -> None:
        payload = json.dumps({"schema_version": "run.lifecycle.v1", "event": "run_finished",
                              "media_run_id": _MEDIA_RUN_ID, "status": "succeeded"}).encode("utf-8")
        self._send(f"run.lifecycle.v1.{_MEDIA_RUN_ID}", _MEDIA_RUN_ID, payload)

    def close(self) -> None:
        self._sock.close(linger=0)


def _live_payload(endpoint: str) -> dict:
    return {
        "run": {"id": "live-api-run", "scenario": "EBE", "name": "live_api"},
        "input": {"type": "bus", "bus": {"endpoint": endpoint, "recv_timeout_ms": 200,
                                         "idle_timeout_s": 30}},
        "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
    }


@pytest.mark.integration
def test_live_run_by_api_subscribes_before_returning_and_loses_nothing(tmp_path) -> None:
    endpoint = f"tcp://127.0.0.1:{_free_port()}"
    publisher = _Publisher(endpoint)
    app = create_app(ServiceSettings(runs_dir=tmp_path / "runs"))

    try:
        with TestClient(app) as client:
            # El POST devuelve 201 recien cuando el BusSource ya esta suscripto.
            response = client.post(
                "/api/runs",
                json={"mode": "live", "config": _live_payload(endpoint),
                      "experiment_id": "exp-live"},
            )
            assert response.status_code == 201
            assert response.json()["control_run_id"] == "live-api-run"

            # Si el 201 no garantizara la suscripcion, esto colgaria o llegaria tarde.
            publisher.wait_for_subscriber()
            publisher.publish_events(10)
            publisher.finish()

            app.state.manager.join_active(timeout=60.0)

            state = client.get("/api/runs/live-api-run").json()
            assert state["status"] == "succeeded"
            summary = state["summary"]
            assert summary["source"] == "bus"
            assert summary["media_run_id"] == _MEDIA_RUN_ID
            assert summary["experiment_id"] == "exp-live"
            assert summary["units_processed"] == 10, "se perdieron eventos: la suscripcion llego tarde"
            assert summary["bus_dropped_events"] == 0
            assert summary["degraded"] is False

            alerts = client.get("/api/runs/live-api-run/alerts").json()
            assert len(alerts) >= 1
            assert alerts[0]["media_run_id"] == _MEDIA_RUN_ID
    finally:
        publisher.close()


@pytest.mark.integration
def test_live_run_reports_dropped_events_when_the_publisher_skips_a_seq(tmp_path) -> None:
    """Un hueco de `seq` degrada la corrida. Nunca se silencia (ADR-003)."""
    endpoint = f"tcp://127.0.0.1:{_free_port()}"
    publisher = _Publisher(endpoint)
    app = create_app(ServiceSettings(runs_dir=tmp_path / "runs"))

    try:
        with TestClient(app) as client:
            client.post("/api/runs", json={"mode": "live", "config": _live_payload(endpoint)})
            publisher.wait_for_subscriber()
            publisher.publish_events(3)
            publisher._seq += 5  # 5 envelopes "perdidos" por HWM del publicador
            publisher.publish_events(2)
            publisher.finish()

            app.state.manager.join_active(timeout=60.0)

            summary = client.get("/api/runs/live-api-run").json()["summary"]
            assert summary["bus_dropped_events"] == 5
            assert summary["degraded"] is True
            assert "bus_dropped_events" in summary["degradation_causes"]
    finally:
        publisher.close()
```

- [ ] **Step 2: Run the gate**

```bash
.venv/bin/python -m pytest tests/test_service_live.py -q -v
```

Expected: PASS (2 passed). Corrélo **5 veces seguidas**: usa ZeroMQ sobre localhost y un hilo.
Si `units_processed` da menos de 10, la suscripción está llegando tarde — es exactamente el
defecto que este gate existe para detectar; revisá que `build_bus_source` corra dentro de
`start_run` y **no** dentro de `_execute`.

- [ ] **Step 3: Verificar que el gate es significativo (mutación)**

Mové temporalmente la construcción del `BusSource` de `start_run` a `_execute` (es decir,
suscribirse **después** de devolver el 201) y confirmá que el primer test **falla** con
`units_processed` < 10 o con `bus_dropped_events > 0`. Revertí. Reportá el resultado: un gate que
no puede fallar no es un gate.

- [ ] **Step 4: Full suite + lint**

```bash
.venv/bin/python -m pytest -q --ignore=tests/labs && .venv/bin/python -m ruff check src tests
```

Expected: `128 passed`, `All checks passed!`

- [ ] **Step 5: Commit (sólo si el usuario lo pidió)**

```bash
git add tests/test_service_live.py
git commit -m "test(service): gate de corrida live por API con suscripcion previa al 201"
```

---

## Cierre: documentación

- [ ] **Step 1: Actualizar `e-ovrt_control-plane/README.md` y `docs/architecture.md`**

Documentar: `eovrt-control serve` (:8081), la tabla de los 7 endpoints, `EOVRT_CONTROL_RUNS_DIR`,
y la regla de orden para una corrida live (`POST` al control-plane **primero** con `mode: live`,
que deja el bus suscripto; después `POST` al media-plane con `bus.enabled: true`).

- [ ] **Step 2: Escribir `docs/operacion/38-servicio-minimo-control-plane.md` (repo `docs`)**

Mismo formato que los docs 34/35/37, con números medidos: suites antes/después, salida del smoke,
el resultado de la mutación del gate de la Task 5, y la deuda que este plan no absorbió.

- [ ] **Step 3: Actualizar `docs/operacion/36` §3 (ítem 4 hecho) y el `CLAUDE.md` del workspace**

En `projects/CLAUDE.md`, en la sección de acople entre planos, agregar que el control-plane ahora
expone un servicio en `:8081` y que la corrida live se dispara **primero** en él.

- [ ] **Step 4: Registrar la deuda nueva**

1. **No hay `POST /api/runs/{id}/stop`.** Una corrida live sin `run_finished` sólo termina por
   `idle_timeout_s` (default 300 s) o por el fallback de polling. Mientras tanto el servicio
   rechaza corridas nuevas con 409. `shutdown()` cierra el bus y desbloquea el hilo.
2. **`GET /api/runs` (listado) no existe** — spec 41 §5 no lo pide; la webconsole navega por
   `experiment_id`. Si el listado hace falta, es aditivo.
3. **Sin retención de `runs/`** (E-12). El directorio crece sin límite.
4. **`get()` reporta `succeeded` para toda corrida con `summary.json`.** El `RunSummary` del
   control-plane no tiene campo `status`: una corrida que explotó no escribe summary y se ve como
   404. Si se quiere distinguir `failed` histórico, hay que estampar `status` en el summary
   (aditivo).

- [ ] **Step 5: Verificación final antes de declarar terminado**

```bash
cd /home/simonll4/projects/e-ovrt_control-plane
.venv/bin/python -m pytest -q --ignore=tests/labs
.venv/bin/python -m ruff check src tests
```

No declarar nada listo sin pegar la salida de esos dos comandos, la del smoke de la Task 4
Step 9, y el resultado de la mutación de la Task 5 Step 3.
