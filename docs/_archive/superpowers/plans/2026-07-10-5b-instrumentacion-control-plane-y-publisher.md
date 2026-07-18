# Ítem 5b — Instrumentación `t_capture→alert` (mitad control-plane), pattern set `cr01_cr02_v2` y publisher de alertas

> **EJECUTADO el 2026-07-11.** Las 9 tareas están completas (control-plane **177 passed**,
> ruff limpio; revisión final de rama: LISTO PARA MERGE, sin defectos Critical/Important).
> Resultados, evidencia y deuda: `docs/operacion/51-instrumentacion-5b-control-plane.md`
> (repo `docs`). Corrida e2e real por bus verificada (300 unidades, 2 alertas, 0 perdidas,
> join `not_interpretable/dbe_media_time` correcto para DBE video).
>
> **El código de referencia de este plan tenía defectos reales**, hallados por la revisión
> adversarial por tarea y corregidos durante la ejecución. **No lo copies verbatim**: el
> código vigente es el del working tree. Los defectos: la byte-paridad payload↔JSONL requería
> el serializador del sink (`json.dumps(model_dump(mode=json), ensure_ascii=True)`), NO
> `model_dump_json(exclude_none=True)` (que dropea claves `None`); el gate de cadena necesitó
> un test de integración del cableado real de `core.py` (los tests aislados no lo cubrían); el
> flicker test ADR-012 era vacuo con un solo frame clear. Ver doc 51 §3.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cerrar la mitad control-plane de la cadena `t_capture→alert` (spec 40 §5.2.4/§5.4, spec 41 §7–§8): estampar los insumos monotónicos del control-plane (`ts_receive_ms`, `first_evidence_ms`/`first_evidence_unit_id`/`frame_index`, `alert_registered_ms`), llevar `experiment_id` a los eventos, agregar percentiles P50/P95/P99 y la TTFA interna al summary, oficializar el pattern set `cr01_cr02_v2` alineado al informe, publicar las alertas al bus `control.alert.v1.<control_run_id>`, y demostrar el join de punta a punta con su estado de aplicabilidad por fuente.

**Architecture:** El control-plane ya consume detecciones (`BusSource`/`JsonlSource` → `runtime/core.execute_over_source` → `engine.PatternEngine` → `alerts.jsonl`/`metrics.jsonl`/`pattern_events.jsonl`). Este plan **instrumenta esa ruta** sin cambiar su forma: (1) los tres timestamps que aporta el control-plane son **instantes monotónicos del host de control** (`time.monotonic()*1000`), comparables con el `capture_monotonic_ns` del media-plane cuando corren en el **mismo host** (EBE single-host / RTSP ⇒ `computed`); (2) el join cross-plano es **por `unit_id`** y se computa en un helper nuevo (`metrics/latency.py`) que declara estado de aplicabilidad por `source_clock` (ADR-006); (3) el publisher de alertas es el **espejo productor** del `BusPublisher` del media-plane (mismo envelope `bus.envelope.v1`, persiste-primero-publica-después, `run_finished` pase lo que pase). El motor emite un `AlertEvent` en cada confirmación (**sin cooldown**, ADR-011); bajo `granularity: scene` **no hay memoria de cobertura** (ADR-012, ya cableado en `pattern_engine.py:78`).

**Tech Stack:** Python 3.11+, Pydantic v2, `pyzmq` (XPUB), `msgpack`, pytest, ruff. Repo `e-ovrt_control-plane`, rama `feature/control-service`. Espejo de referencia: `e-ovrt_media-plane` (`transport/bus.py`, `service/bus_writer.py`).

## Global Constraints

- **Nunca commitear sin pedido explícito del usuario en ese turno.** Los pasos que dicen "Commit" **preparan** el commit; se ejecutan sólo si el usuario lo pide en ese turno. Si no lo pidió, dejar el árbol de trabajo sin `git commit` y avisar. (La ejecución por `subagent-driven-development` usa snapshots `git write-tree` entre tareas para el review; eso **no** es un commit.)
- **Nunca agregar `Co-Authored-By`. Nada en GitHub; todo local.** No crear remotes ni pushear.
- **Contratos SIEMPRE aditivos, sin bump de `schema_version`.** Todo campo nuevo es opcional con default (`None` / `0` / `default_factory`). Los tres eventos (`PatternStateChanged`, `AlertEvent`, `ControlMetricSample`) y el `RunSummary` conservan su `schema_version`.
- **Instantes monotónicos, no de pared.** `ts_receive_ms`, `first_evidence_ms`, `alert_registered_ms` y la base del `join` usan `time.monotonic()*1000` (ms). Nunca `time.time()` para latencias. El `first_hit_timestamp_ms` que ya existe en `PatternRuntimeState` es **tiempo de medio** (`source.timestamp_ms`) — es otra magnitud, no reusarla.
- **Nunca publicar un número que no significa nada (ADR-006).** El join declara `status ∈ {computed | applicable_not_computed | not_applicable | not_interpretable}` + `cause`; jamás un cero o basura.
- **Sin cooldown en el motor (ADR-011).** `realert_cooldown_ms/frames` quedan sin configurar en `cr01_cr02_v2`; el motor emite en cada transición a `confirmed`.
- **Sin memoria de cobertura bajo escena (ADR-012).** `coverage_memory_*` sin configurar en `cr01_cr02_v2`.
- `ruff` `line-length = 100`, `target-version = "py311"`. Comentarios/docstrings en español, **sin tildes ni ñ dentro del código** (imitar el archivo vecino).
- **Entorno:** `/home/simonll4/projects/e-ovrt_control-plane/.venv/bin/python`. `python3` del sistema NO tiene las deps.
- **Correr tests** con `.venv/bin/python -m pytest -q --ignore=tests/labs` (los de `labs` fallan por `numpy`, falla conocida no bloqueante).
- **Baseline MEDIDA (2026-07-10):** `pytest -q --ignore=tests/labs` → **154 passed**; `ruff check src tests` → `All checks passed!`. Cada tarea afirma end = baseline + tests nuevos.
- **Fuera de alcance de este plan:** `evaluate-alerts` v2 (spec 41 §8 item 7: consume `clip_gt.v2`, evaluación a nivel episodio, `re_alerts`) — es un subsistema distinto que extiende `evaluation/temporal.py`; va en un **plan hermano** que sigue a este.

---

## Task 1: `ts_receive_ms` por unidad (BusSource → metrics.jsonl)

Estampa el instante monotónico de recepción por unidad y lo persiste en `ControlMetricSample`. `BusSource` ya calcula `time.monotonic()` en `bus.py:122` (hoy sólo para idle-timeout); hay que propagarlo hasta el item que se hace `yield` y hasta la fila de métricas. `JsonlSource` estampa en el instante de lectura (paridad replay).

**Files:**
- Modify: `src/eovrt_control/sources/base.py:18` (widen `SourceItem` a 4-tupla)
- Modify: `src/eovrt_control/sources/bus.py` (`_decode`/`_decode_frames`/`__iter__`/`_drain`/`_source_error`: incluir `ts_receive_ms`)
- Modify: `src/eovrt_control/sources/jsonl.py:47` (estampar `time.monotonic()*1000` al `yield`)
- Modify: `src/eovrt_control/sources/memory.py` (yield 4-tupla con `ts_receive_ms=None`)
- Modify: `src/eovrt_control/contracts/metrics.py:18-30` (`ControlMetricSample.ts_receive_ms`)
- Modify: `src/eovrt_control/runtime/core.py:168` (desempacar 4-tupla), `:204-217` (pasar `ts_receive_ms`)
- Test: `tests/test_sources.py`, `tests/test_bus_source.py`, `tests/test_runtime_progress.py`

**Interfaces:**
- Consumes: nada nuevo.
- Produces: `SourceItem = tuple[int, DetectionEvent | None, ErrorEvent | None, float | None]` (4º = `ts_receive_ms` monotónico en ms, `None` si la fuente no lo provee). `ControlMetricSample.ts_receive_ms: float | None = None`.

- [ ] **Step 1: Escribir el test que falla**

En `tests/test_bus_source.py`, agregar (usar los helpers de fixture de bus ya existentes en ese archivo para publicar un `DetectionEvent`):

```python
def test_bus_source_stamps_monotonic_ts_receive(bus_endpoint):
    # Publica una deteccion y verifica que el item trae ts_receive_ms monotonico.
    source = _make_bus_source(bus_endpoint)  # helper existente del archivo
    _publish_detection(bus_endpoint, unit_id="u-1")  # helper existente
    _publish_run_finished(bus_endpoint)
    items = [it for it in source]
    detection_items = [it for it in items if it[1] is not None]
    assert len(detection_items) == 1
    index, event, error, ts_receive_ms = detection_items[0]
    assert error is None
    assert event.unit_id == "u-1"
    assert isinstance(ts_receive_ms, float) and ts_receive_ms > 0
```

En `tests/test_sources.py`, para `JsonlSource`:

```python
def test_jsonl_source_stamps_ts_receive(tmp_path):
    path = tmp_path / "detections.jsonl"
    path.write_text(_one_detection_line(unit_id="u-9") + "\n")  # helper del archivo
    items = list(JsonlSource(path))
    assert len(items) == 1
    index, event, error, ts_receive_ms = items[0]
    assert event.unit_id == "u-9"
    assert isinstance(ts_receive_ms, float) and ts_receive_ms > 0
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `.venv/bin/python -m pytest tests/test_bus_source.py::test_bus_source_stamps_monotonic_ts_receive tests/test_sources.py::test_jsonl_source_stamps_ts_receive -v`
Expected: FAIL — `ValueError: not enough values to unpack (expected 4, got 3)`.

- [ ] **Step 3: Widen `SourceItem` y estampar en cada fuente**

En `src/eovrt_control/sources/base.py:18`:

```python
# (indice, evento, error, ts_receive_ms): exactamente uno de evento/error no es None.
# El indice es el numero de linea (JSONL) o el `seq` del envelope (bus).
# ts_receive_ms es el instante monotonico de recepcion en ms (None si la fuente no lo da).
SourceItem = tuple[int, "DetectionEvent | None", "ErrorEvent | None", float | None]
```

En `src/eovrt_control/sources/bus.py`:
- En `__iter__` (alrededor de `bus.py:120-125`), el instante ya se calcula: pasar `last_message` (que es `time.monotonic()`) al decode. Convertir a ms al construir el item.
- En `_decode` (`bus.py:307-324`), aceptar `ts_receive_monotonic: float` y devolver la 4-tupla:

```python
# _decode_frames y _decode reciben ts_receive_monotonic (segundos monotonicos)
ts_receive_ms = ts_receive_monotonic * 1000.0
...
return (seq, event, None, ts_receive_ms), False
```
- En `_source_error` (`bus.py:218`) devolver `(-1, None, ErrorEvent(...), None)`.
- En `_drain` (`bus.py:159-194`), estampar con `time.monotonic()` en el momento del `recv` drenado, misma conversion.

En `src/eovrt_control/sources/jsonl.py:47`:

```python
yield line_number, DetectionEvent.model_validate(data), None, time.monotonic() * 1000.0
```
(agregar `import time` arriba si falta.)

En `src/eovrt_control/sources/memory.py`, cada `yield` pasa a 4-tupla con `None` como `ts_receive_ms`.

En `src/eovrt_control/contracts/metrics.py`, agregar el campo (aditivo, sin bump):

```python
class ControlMetricSample(BaseModel):
    schema_version: str = "control.metric.v1"
    ...
    processing_ms: float
    ts_receive_ms: float | None = None
```

En `src/eovrt_control/runtime/core.py:168` desempacar la 4-tupla:

```python
for _, event, error, ts_receive_ms in source:
```
y en la construccion de `ControlMetricSample` (`core.py:204-217`) agregar `ts_receive_ms=ts_receive_ms,`.

- [ ] **Step 4: Correr los tests nuevos — deben pasar**

Run: `.venv/bin/python -m pytest tests/test_bus_source.py::test_bus_source_stamps_monotonic_ts_receive tests/test_sources.py::test_jsonl_source_stamps_ts_receive -v`
Expected: PASS (2).

- [ ] **Step 5: Suite completa (regresión del desempaque de 4-tupla)**

Run: `.venv/bin/python -m pytest -q --ignore=tests/labs`
Expected: 156 passed (154 + 2). Si algo falla por desempaque de 3 elementos, buscar `for _, event, error in` y `, None), ` sueltos en tests/fuentes y actualizar a 4-tupla.

- [ ] **Step 6: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_control/sources src/eovrt_control/contracts/metrics.py src/eovrt_control/runtime/core.py tests/test_sources.py tests/test_bus_source.py
git commit -m "feat(sources): ts_receive_ms monotonico por unidad en metrics.jsonl"
```

---

## Task 2: Hitos de primera evidencia por episodio (`first_evidence_*`)

El motor registra, al abrir un episodio, el instante monotónico de recepción de la unidad de **primera evidencia positiva**, su `unit_id` (clave de join obligatoria, spec 40 §5.2.4) y su `frame_index`, y los propaga a `PatternStateChanged`. Requiere pasar `ts_receive_ms` a `engine.process`.

**Files:**
- Modify: `src/eovrt_control/engine/pattern_engine.py:15-28` (`PatternRuntimeState`: 3 campos nuevos), `:57` (`process` recibe `ts_receive_ms`), `:143-150` (branch de apertura de episodio), `:378-403` (`_make_state_event`)
- Modify: `src/eovrt_control/contracts/pattern.py:31-49` (`PatternStateChanged`: 3 campos)
- Modify: `src/eovrt_control/runtime/core.py:183` (pasar `ts_receive_ms` a `engine.process`)
- Test: `tests/test_pattern_engine.py` (o el archivo de tests del motor existente)

**Interfaces:**
- Consumes: `SourceItem` 4-tupla (Task 1); `ts_receive_ms` en el loop de `core`.
- Produces: `PatternEngine.process(self, event: DetectionEvent, ts_receive_ms: float | None = None)`; `PatternRuntimeState.first_evidence_monotonic_ms/first_evidence_unit_id/first_evidence_frame_index`; `PatternStateChanged.first_evidence_ms/first_evidence_unit_id/first_evidence_frame_index` (todos opcionales).

- [ ] **Step 1: Escribir el test que falla**

En el archivo de tests del motor (usar el helper que ya construye un `PatternEngine` y `DetectionEvent` positivos; imitar los tests existentes):

```python
def test_first_evidence_milestone_captured_on_episode_open():
    engine = _engine_with_pattern("CR-01")  # helper existente
    ev = _positive_event(unit_id="u-first", frame_index=7)  # dispara evidencia CR-01
    result = engine.process(ev, ts_receive_ms=1234.5)
    state_events = [e for e in result.pattern_events]
    opened = [e for e in state_events if e.state in ("candidate", "confirmed")]
    assert opened, "deberia abrir un episodio"
    e0 = opened[0]
    assert e0.first_evidence_unit_id == "u-first"
    assert e0.first_evidence_frame_index == 7
    assert e0.first_evidence_ms == 1234.5


def test_first_evidence_persists_across_units_until_resolve():
    engine = _engine_with_pattern("CR-01")
    engine.process(_positive_event(unit_id="u-first", frame_index=1), ts_receive_ms=100.0)
    result2 = engine.process(_positive_event(unit_id="u-second", frame_index=2), ts_receive_ms=200.0)
    opened = [e for e in result2.pattern_events if e.first_evidence_unit_id is not None]
    # el hito de primera evidencia NO se reescribe con la segunda unidad
    assert all(e.first_evidence_unit_id == "u-first" for e in opened)
    assert all(e.first_evidence_ms == 100.0 for e in opened)
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `.venv/bin/python -m pytest tests/test_pattern_engine.py -k first_evidence -v`
Expected: FAIL — `TypeError: process() got an unexpected keyword argument 'ts_receive_ms'` / `AttributeError: 'PatternStateChanged' object has no attribute 'first_evidence_unit_id'`.

- [ ] **Step 3: Implementación**

`PatternRuntimeState` (`pattern_engine.py:15-28`), agregar:

```python
    first_evidence_monotonic_ms: float | None = None
    first_evidence_unit_id: str | None = None
    first_evidence_frame_index: int | None = None
```

`PatternEngine.process` (`pattern_engine.py:57`+): agregar el parametro `ts_receive_ms: float | None = None` y guardarlo en `self` para que los helpers de emision lo lean durante este `process` (p.ej. `self._current_ts_receive_ms = ts_receive_ms` al inicio de `process`).

Branch de apertura de episodio (`pattern_engine.py:143-150`, donde hoy se setea `first_hit_timestamp_ms`): registrar el hito **sólo si aun es None** (no se reescribe hasta el resolve):

```python
if runtime_state.first_evidence_monotonic_ms is None:
    runtime_state.first_evidence_monotonic_ms = self._current_ts_receive_ms
    runtime_state.first_evidence_unit_id = event.unit_id
    runtime_state.first_evidence_frame_index = event.source.frame_index
```
En los sitios de reset del episodio (resolve `:213`, expire `:296`) poner los tres a `None` junto a `first_hit_timestamp_ms`.

`_make_state_event` (`pattern_engine.py:378-403`): agregar los tres campos leyendo del `runtime_state` correspondiente (pasar el `runtime_state` al helper si no lo recibe ya; si el helper no tiene acceso, pasar los tres valores como argumentos). Resultado en el `return PatternStateChanged(...)`:

```python
        first_evidence_ms=runtime_state.first_evidence_monotonic_ms,
        first_evidence_unit_id=runtime_state.first_evidence_unit_id,
        first_evidence_frame_index=runtime_state.first_evidence_frame_index,
```

`PatternStateChanged` (`contracts/pattern.py:31-49`), agregar (aditivo):

```python
    first_evidence_ms: float | None = None
    first_evidence_unit_id: str | None = None
    first_evidence_frame_index: int | None = None
```

`core.py:183`: `result = engine.process(event, ts_receive_ms=ts_receive_ms)`.

- [ ] **Step 4: Correr los tests nuevos — deben pasar**

Run: `.venv/bin/python -m pytest tests/test_pattern_engine.py -k first_evidence -v`
Expected: PASS (2).

- [ ] **Step 5: Suite completa**

Run: `.venv/bin/python -m pytest -q --ignore=tests/labs`
Expected: 158 passed (156 + 2).

- [ ] **Step 6: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_control/engine/pattern_engine.py src/eovrt_control/contracts/pattern.py src/eovrt_control/runtime/core.py tests/test_pattern_engine.py
git commit -m "feat(engine): hito first_evidence (ms/unit_id/frame_index) por episodio"
```

---

## Task 3: `alert_registered_ms` + `first_evidence_*` en `AlertEvent`

Estampa el instante monotónico de escritura del `AlertEvent` y propaga el hito de primera evidencia al contrato de alerta (spec 40 §5.2.4: ambos en `alerts.jsonl`).

**Files:**
- Modify: `src/eovrt_control/contracts/alerts.py:10-28` (4 campos nuevos)
- Modify: `src/eovrt_control/engine/pattern_engine.py:445-469` (`_make_alert`: estampar `alert_registered_ms`, copiar `first_evidence_*`)
- Test: `tests/test_pattern_engine.py`

**Interfaces:**
- Consumes: `PatternRuntimeState.first_evidence_*` (Task 2).
- Produces: `AlertEvent.alert_registered_ms/first_evidence_ms/first_evidence_unit_id/first_evidence_frame_index` (opcionales).

- [ ] **Step 1: Escribir el test que falla**

```python
def test_alert_carries_registered_and_first_evidence(monkeypatch):
    engine = _engine_with_pattern("CR-01", confirm_after_frames=1)
    monkeypatch.setattr("eovrt_control.engine.pattern_engine.time.monotonic", lambda: 5.0)
    result = engine.process(_positive_event(unit_id="u-first", frame_index=3), ts_receive_ms=4000.0)
    assert result.alerts, "CR-01 confirma en 1 frame"
    alert = result.alerts[0]
    assert alert.alert_registered_ms == 5000.0            # 5.0 s -> 5000 ms
    assert alert.first_evidence_unit_id == "u-first"
    assert alert.first_evidence_ms == 4000.0
    assert alert.first_evidence_frame_index == 3
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `.venv/bin/python -m pytest tests/test_pattern_engine.py -k alert_carries -v`
Expected: FAIL — `AttributeError: 'AlertEvent' object has no attribute 'alert_registered_ms'`.

- [ ] **Step 3: Implementación**

`contracts/alerts.py:10-28`, agregar (aditivo):

```python
    alert_registered_ms: float | None = None
    first_evidence_ms: float | None = None
    first_evidence_unit_id: str | None = None
    first_evidence_frame_index: int | None = None
```

En `pattern_engine.py`, asegurar `import time` arriba. En `_make_alert` (`:445-469`), estampar y copiar del `runtime_state` del patron (accesible en `_maybe_alert`/`_make_alert`; si no lo recibe, pasarlo como argumento):

```python
    return AlertEvent(
        ...
        subjects_in_evidence_max=change.subjects_in_evidence_max,
        alert_registered_ms=time.monotonic() * 1000.0,
        first_evidence_ms=runtime_state.first_evidence_monotonic_ms,
        first_evidence_unit_id=runtime_state.first_evidence_unit_id,
        first_evidence_frame_index=runtime_state.first_evidence_frame_index,
    )
```

- [ ] **Step 4: Correr el test nuevo — debe pasar**

Run: `.venv/bin/python -m pytest tests/test_pattern_engine.py -k alert_carries -v`
Expected: PASS (1).

- [ ] **Step 5: Suite completa**

Run: `.venv/bin/python -m pytest -q --ignore=tests/labs`
Expected: 159 passed (158 + 1).

- [ ] **Step 6: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_control/contracts/alerts.py src/eovrt_control/engine/pattern_engine.py tests/test_pattern_engine.py
git commit -m "feat(alerts): alert_registered_ms + first_evidence en AlertEvent"
```

---

## Task 4: `experiment_id` en los eventos

Hoy `experiment_id` (ADR-004) sólo llega al `RunSummary`. Llevarlo a los tres eventos (`PatternStateChanged`, `AlertEvent`, `ControlMetricSample`) — el comentario en `config.py:18` ya dice que "viaja al summary y a los eventos".

**Files:**
- Modify: `src/eovrt_control/contracts/pattern.py`, `contracts/alerts.py`, `contracts/metrics.py` (campo `experiment_id`)
- Modify: `src/eovrt_control/engine/pattern_engine.py:57` (ctor guarda `experiment_id`), `_make_state_event`, `_make_alert`
- Modify: `src/eovrt_control/runtime/core.py:148` (pasar `experiment_id` al ctor), `:204-217` (a `ControlMetricSample`)
- Test: `tests/test_pattern_engine.py`, `tests/test_runtime_progress.py`

**Interfaces:**
- Consumes: `config.run.experiment_id` (ya en scope en `core.py:252`).
- Produces: `PatternEngine.__init__(self, control_run_id, patterns, experiment_id: str | None = None)`; `experiment_id: str | None = None` en los tres contratos.

- [ ] **Step 1: Escribir el test que falla**

```python
def test_experiment_id_threads_into_events():
    engine = _engine_with_pattern("CR-01", experiment_id="exp-42")
    result = engine.process(_positive_event(unit_id="u-1", frame_index=1), ts_receive_ms=10.0)
    assert all(e.experiment_id == "exp-42" for e in result.pattern_events)
    assert all(a.experiment_id == "exp-42" for a in result.alerts)
```
(Actualizar el helper `_engine_with_pattern` para aceptar `experiment_id` y pasarlo al ctor.)

- [ ] **Step 2: Correr para verificar que falla**

Run: `.venv/bin/python -m pytest tests/test_pattern_engine.py -k experiment_id -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'experiment_id'`.

- [ ] **Step 3: Implementación**

Agregar `experiment_id: str | None = None` a `PatternStateChanged` (`contracts/pattern.py`), `AlertEvent` (`contracts/alerts.py`) y `ControlMetricSample` (`contracts/metrics.py`).

`PatternEngine.__init__` (`pattern_engine.py:57`): `def __init__(self, control_run_id: str, patterns: list[PatternDefinition], experiment_id: str | None = None)`, guardar `self.experiment_id = experiment_id`. En `_make_state_event` y `_make_alert` agregar `experiment_id=self.experiment_id,`.

`core.py:148`: `engine = PatternEngine(control_run_id=control_run_id, patterns=active_patterns, experiment_id=config.run.experiment_id)`.
`core.py:204-217`: agregar `experiment_id=config.run.experiment_id,` al `ControlMetricSample`.

- [ ] **Step 4: Correr el test nuevo — debe pasar**

Run: `.venv/bin/python -m pytest tests/test_pattern_engine.py -k experiment_id -v`
Expected: PASS (1).

- [ ] **Step 5: Suite completa**

Run: `.venv/bin/python -m pytest -q --ignore=tests/labs`
Expected: 160 passed (159 + 1).

- [ ] **Step 6: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_control/contracts src/eovrt_control/engine/pattern_engine.py src/eovrt_control/runtime/core.py tests/test_pattern_engine.py
git commit -m "feat(events): experiment_id en pattern_state/alert/metric"
```

---

## Task 5: Percentiles `processing_ms` + TTFA interna en el summary

Reemplaza el único agregado de latencia (`avg_processing_ms`) por P50/P95/P99 de `processing_ms` y agrega la **TTFA interna** (diagnóstico): `alert_registered_ms − first_evidence_ms`, ambos monotónicos del host de control. Un helper `metrics/latency.py` centraliza el cálculo de percentiles (reusado por el join en Task 8).

**Files:**
- Create: `src/eovrt_control/metrics/__init__.py`, `src/eovrt_control/metrics/latency.py`
- Modify: `src/eovrt_control/contracts/metrics.py:33-64` (`RunSummary`: 2 dicts opcionales)
- Modify: `src/eovrt_control/runtime/core.py:154` (acumular TTFA), `:263` (agregados)
- Test: `tests/test_latency.py` (nuevo), `tests/test_runtime_progress.py`

**Interfaces:**
- Consumes: `AlertEvent.alert_registered_ms/first_evidence_ms` (Task 3); `processing_times` (ya en `core.py:154`).
- Produces: `metrics.latency.percentiles(values: list[float]) -> dict[str, float] | None` (claves `p50`/`p95`/`p99`); `RunSummary.processing_ms_percentiles: dict[str, float] | None = None`, `RunSummary.ttfa_internal_ms_percentiles: dict[str, float] | None = None`.

- [ ] **Step 1: Escribir el test que falla**

`tests/test_latency.py`:

```python
from eovrt_control.metrics.latency import percentiles


def test_percentiles_none_on_empty():
    assert percentiles([]) is None


def test_percentiles_single_value():
    assert percentiles([12.0]) == {"p50": 12.0, "p95": 12.0, "p99": 12.0}


def test_percentiles_linear_interpolation():
    p = percentiles([0.0, 10.0, 20.0, 30.0, 40.0])
    assert p["p50"] == 20.0
    assert abs(p["p95"] - 38.0) < 1e-9   # 0.95*(5-1)=3.8 -> 30 + 0.8*10
    assert abs(p["p99"] - 39.6) < 1e-9   # 0.99*(5-1)=3.96 -> 30 + 0.96*10
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `.venv/bin/python -m pytest tests/test_latency.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eovrt_control.metrics'`.

- [ ] **Step 3: Implementación**

`src/eovrt_control/metrics/__init__.py`: vacio.

`src/eovrt_control/metrics/latency.py`:

```python
"""Helpers de latencia: percentiles deterministas y join t_capture->alert."""
from __future__ import annotations


def percentiles(values: list[float]) -> dict[str, float] | None:
    """P50/P95/P99 por interpolacion lineal. None si no hay datos.

    Determinista (sin dependencias de `statistics.quantiles`, que exige n>=2
    y tiene bordes distintos por metodo). Con un solo valor devuelve ese valor
    en los tres percentiles.
    """
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)

    def _p(q: float) -> float:
        if n == 1:
            return ordered[0]
        pos = q * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        return ordered[lo] * (1.0 - frac) + ordered[hi] * frac

    return {"p50": _p(0.50), "p95": _p(0.95), "p99": _p(0.99)}
```

`RunSummary` (`contracts/metrics.py:33-64`), agregar (aditivo; conservar `avg_processing_ms`):

```python
    processing_ms_percentiles: dict[str, float] | None = None
    ttfa_internal_ms_percentiles: dict[str, float] | None = None  # diagnostico
```

`runtime/core.py`: importar `from eovrt_control.metrics.latency import percentiles`. Junto a `processing_times` (`:154`) declarar `ttfa_internal_ms: list[float] = []`. En el loop, tras escribir alertas (`:201-202`), por cada alerta con ambos hitos:

```python
for alert in result.alerts:
    alert_sink.write(alert)
    if alert.alert_registered_ms is not None and alert.first_evidence_ms is not None:
        ttfa_internal_ms.append(alert.alert_registered_ms - alert.first_evidence_ms)
```
Al construir el `RunSummary` (`:263` y alrededores), conservar `avg_processing_ms=mean(...)` y agregar:

```python
        processing_ms_percentiles=percentiles(processing_times),
        ttfa_internal_ms_percentiles=percentiles(ttfa_internal_ms),
```

- [ ] **Step 4: Correr los tests nuevos — deben pasar**

Run: `.venv/bin/python -m pytest tests/test_latency.py -v`
Expected: PASS (3).

- [ ] **Step 5: Suite completa**

Run: `.venv/bin/python -m pytest -q --ignore=tests/labs`
Expected: 163 passed (160 + 3).

- [ ] **Step 6: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_control/metrics src/eovrt_control/contracts/metrics.py src/eovrt_control/runtime/core.py tests/test_latency.py
git commit -m "feat(summary): percentiles processing_ms + TTFA interna diagnostica"
```

---

## Task 6: Pattern set oficial `cr01_cr02_v2`

Oficializa el pattern set alineado al informe (spec 41 §7). El archivo `cr01_cr02_v2_probe.yaml` ya tiene los valores correctos; este task crea el **oficial** `cr01_cr02_v2.yaml` (severidades y timings del informe), lo carga vía una run config, y verifica la apuesta falsable de ADR-012 (flicker bajo escena). El mecanismo `coverage_memory_unsupported_scene` ya está cableado (`pattern_engine.py:78`) — **no** se toca el motor.

**Valores del informe (spec 41 §7, verbatim):**

| Patrón | condition_id | severity | confirm_after_ms | resolve_after_ms | granularity | cooldown | coverage_memory |
|---|---|---|---|---|---|---|---|
| CR-01 | CR-01 | **high** | **4000** | 2000 | scene | sin configurar (ADR-011) | sin configurar (ADR-012) |
| CR-02 | CR-02 | **medium** | **7000** | 3000 | scene | sin configurar | sin configurar |

**Files:**
- Create: `configs/patterns/cr01_cr02_v2.yaml`
- Create: `configs/replay_cr01_cr02_v2.yaml` (run config que apunta al set)
- Test: `tests/test_pattern_engine.py` (flicker) y `tests/test_config.py` (carga del set)

**Interfaces:**
- Consumes: `load_patterns_file` / `PatternsFile` (config.py:172), `PatternDefinition.timing.*`.
- Produces: `configs/patterns/cr01_cr02_v2.yaml` con `pattern_set.id: cr01_cr02_v2`.

- [ ] **Step 1: Escribir el test que falla**

En `tests/test_config.py` (o el archivo de config existente):

```python
def test_cr01_cr02_v2_pattern_set_matches_informe():
    pf = load_patterns_file("configs/patterns/cr01_cr02_v2.yaml")
    assert pf.pattern_set.id == "cr01_cr02_v2"
    by_id = {p.condition_id: p for p in pf.pattern_set.patterns}
    cr01, cr02 = by_id["CR-01"], by_id["CR-02"]
    assert cr01.severity == "high" and cr01.timing.confirm_after_ms == 4000.0
    assert cr01.timing.resolve_after_ms == 2000.0 and cr01.granularity == "scene"
    assert cr02.severity == "medium" and cr02.timing.confirm_after_ms == 7000.0
    assert cr02.timing.resolve_after_ms == 3000.0 and cr02.granularity == "scene"
    # ADR-011 / ADR-012: sin cooldown ni memoria de cobertura
    for p in (cr01, cr02):
        assert p.timing.realert_cooldown_ms is None and p.timing.realert_cooldown_frames is None
        assert p.timing.coverage_memory_ms is None and p.timing.coverage_memory_frames is None
```

En el archivo de tests del motor, el test falsable de ADR-012 (flicker): un EPP ausente desaparece/reaparece dentro de una ventana menor que `resolve_after_ms` — el episodio **no** debe resolver ni re-alertar. Usar timestamps de medio (`source.timestamp_ms`) para la histeresis:

```python
def test_scene_flicker_within_resolve_window_does_not_realert():
    # confirm rapido, resolve 2000ms; el hueco de ausencia dura 500ms (< 2000)
    engine = _engine_with_pattern("CR-01", confirm_after_ms=0.0, resolve_after_ms=2000.0,
                                  granularity="scene")
    engine.process(_positive_event(unit_id="u1", ts_ms=0.0), ts_receive_ms=0.0)      # confirma
    engine.process(_negative_event(unit_id="u2", ts_ms=500.0), ts_receive_ms=500.0)  # EPP reaparece: sin evidencia
    r = engine.process(_positive_event(unit_id="u3", ts_ms=700.0), ts_receive_ms=700.0)  # vuelve a faltar
    # dentro de la ventana de resolve NO hay transicion a resolved ni nueva alerta
    assert not any(e.state == "resolved" for e in r.pattern_events)
    assert not r.alerts, "no debe re-alertar dentro de la ventana de resolve (ADR-012)"
```
(Ajustar los nombres de helpers `_negative_event`/`ts_ms` a los que existan en el archivo; si no hay un helper de evento sin evidencia, construir un `DetectionEvent` sin la clase ausente en la region.)

- [ ] **Step 2: Correr para verificar que falla**

Run: `.venv/bin/python -m pytest tests/test_config.py -k cr01_cr02_v2 tests/test_pattern_engine.py -k flicker -v`
Expected: FAIL — el de config por `FileNotFoundError`; el flicker corre pero valida la conducta (si ya pasa, deja el gate de ADR-012 documentado).

- [ ] **Step 3: Crear el pattern set y la run config**

`configs/patterns/cr01_cr02_v2.yaml` — partir de `cr01_cr02_v2_probe.yaml` (misma forma de region/evidence), con estos valores del informe:

```yaml
pattern_set:
  id: cr01_cr02_v2
  description: >
    Pattern set oficial de plataforma para CR-01/CR-02 (spec 41 §7).
    Alineado al informe (Tabla 24/D.4): CR-01 severidad high confirm 4000ms,
    CR-02 severidad medium confirm 7000ms; granularity scene.
    Sin cooldown (ADR-011): el motor emite en cada confirmacion; la supresion
    es politica del tramo de distribucion (spec 45).
    Sin memoria de cobertura (ADR-012): inaplicable bajo escena.
  patterns:
    - id: CR-01
      name: person_without_helmet
      description: "Persona observada sin evidencia asociada de casco."
      enabled: true
      condition_id: CR-01
      severity: high
      subject_class: person
      required_absent_class: helmet
      granularity: scene
      region:
        type: upper_body
        y_min_ratio: 0.0
        y_max_ratio: 0.45
        x_margin_ratio: 0.12
      evidence:
        min_subject_confidence: 0.35
        min_absent_class_confidence: 0.25
        min_subject_area_px: 400.0
      timing:
        confirm_after_frames: 1
        resolve_after_frames: 1
        confirm_after_ms: 4000.0
        resolve_after_ms: 2000.0

    - id: CR-02
      name: person_without_vest
      description: "Persona observada sin evidencia asociada de chaleco reflectante."
      enabled: true
      condition_id: CR-02
      severity: medium
      subject_class: person
      required_absent_class: vest
      granularity: scene
      region:
        type: torso
        y_min_ratio: 0.25
        y_max_ratio: 0.85
        x_margin_ratio: 0.08
      evidence:
        min_subject_confidence: 0.35
        min_absent_class_confidence: 0.25
        min_subject_area_px: 400.0
      timing:
        confirm_after_frames: 1
        resolve_after_frames: 1
        confirm_after_ms: 7000.0
        resolve_after_ms: 3000.0
```

`configs/replay_cr01_cr02_v2.yaml` — imitar una run config de replay existente (`configs/replay_*.yaml`), con:

```yaml
run:
  scenario: DBE
  name: control_replay_cr01_cr02_v2
input:
  type: media_jsonl
  path: runs/latest/detections.jsonl
patterns:
  file: configs/patterns/cr01_cr02_v2.yaml
outputs:
  save_alerts_jsonl: true
```
(Copiar las secciones `outputs`/`logging` que traigan las otras run configs para no romper el schema.)

- [ ] **Step 4: Correr los tests nuevos — deben pasar**

Run: `.venv/bin/python -m pytest tests/test_config.py -k cr01_cr02_v2 tests/test_pattern_engine.py -k flicker -v`
Expected: PASS.

- [ ] **Step 5: Suite completa**

Run: `.venv/bin/python -m pytest -q --ignore=tests/labs`
Expected: 165 passed (163 + 2).

- [ ] **Step 6: Commit (sólo si el usuario lo pidió)**

```bash
git add configs/patterns/cr01_cr02_v2.yaml configs/replay_cr01_cr02_v2.yaml tests/test_config.py tests/test_pattern_engine.py
git commit -m "feat(patterns): pattern set oficial cr01_cr02_v2 alineado al informe"
```

---

## Task 7: Publisher de alertas al bus `control.alert.v1.<control_run_id>`

Espejo productor del `BusPublisher` del media-plane (spec 41 §8 item 6). Mismo envelope `bus.envelope.v1`, mismo XPUB, `seq` monótono consumido aunque se descarte, **persiste-primero-publica-después**, `run_finished` pase lo que pase. Apagado por default; el JSONL sigue siendo la verdad.

**Files:**
- Create: `src/eovrt_control/transport/__init__.py`, `src/eovrt_control/transport/alert_bus.py`
- Modify: `src/eovrt_control/config.py:147-161` (`AlertBusSection` en `ReplayConfig`)
- Modify: `src/eovrt_control/runtime/core.py` (construir publisher; publish-after en `:201-202`; `run_finished` en el `finally` `:224`)
- Test: `tests/test_alert_bus.py` (nuevo, gate de paridad payload↔JSONL)

**Interfaces:**
- Consumes: `AlertEvent.model_dump(mode="json")` (mismo dump que `JsonlSink`); envelope `bus.envelope.v1` (constantes en `sources/bus.py:32-34`, reusar).
- Produces: `transport.alert_bus.AlertBusPublisher(endpoint, *, hwm=1000, wait_for_subscriber_ms=0)` con `.publish(topic, key, payload: bytes) -> int`, `.publish_run_finished(control_run_id, status)`, `.close()`; `config.AlertBusSection(enabled=False, endpoint, hwm, wait_for_subscriber_ms)`; `ReplayConfig.alert_bus`.

- [ ] **Step 1: Escribir el test que falla**

`tests/test_alert_bus.py` — **gate de byte-paridad**: el `payload` publicado debe ser byte-idéntico a la línea del `alerts.jsonl` (imitar el test de paridad del media-plane; usar un SUB de prueba o inyectar un socket falso que capture `send_multipart`):

```python
import json
import msgpack
from eovrt_control.transport.alert_bus import AlertBusPublisher, encode_envelope
from eovrt_control.contracts.alerts import AlertEvent


class _FakeSock:
    def __init__(self):
        self.sent = []
    def setsockopt(self, *a, **k): pass
    def bind(self, *a, **k): pass
    def send_multipart(self, frames, flags=0): self.sent.append(frames)
    def close(self, *a, **k): pass


def test_alert_payload_is_byte_identical_to_jsonl(monkeypatch, _example_alert):
    fake = _FakeSock()
    pub = AlertBusPublisher.__new__(AlertBusPublisher)
    pub._sock = fake; pub._seq = 0; pub._closed = False; pub.send_failures = 0
    payload = _example_alert.model_dump_json(exclude_none=True).encode("utf-8")
    jsonl_line = json.dumps(_example_alert.model_dump(mode="json"), ensure_ascii=True)
    pub.publish("control.alert.v1.run-1", _example_alert.source_id, payload)
    topic_frame, envelope_frame = fake.sent[0]
    env = msgpack.unpackb(envelope_frame, raw=False)
    assert env["schema_version"] == "bus.envelope.v1"
    assert env["seq"] == 0
    # el payload del envelope decodifica al MISMO dict que la linea del JSONL
    assert json.loads(env["payload"]) == json.loads(jsonl_line)


def test_seq_is_consumed_even_when_send_is_dropped(monkeypatch, _example_alert):
    import zmq
    fake = _FakeSock()
    def _raise(frames, flags=0):
        raise zmq.Again()
    fake.send_multipart = _raise
    pub = AlertBusPublisher.__new__(AlertBusPublisher)
    pub._sock = fake; pub._seq = 0; pub._closed = False; pub.send_failures = 0
    s0 = pub.publish("t", "k", b"x")
    s1 = pub.publish("t", "k", b"y")
    assert (s0, s1) == (0, 1) and pub.send_failures == 2
```
(`_example_alert` fixture = un `AlertEvent(...)` con todos los campos; `_make_alert_event()` si ya existe en tests.)

- [ ] **Step 2: Correr para verificar que falla**

Run: `.venv/bin/python -m pytest tests/test_alert_bus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eovrt_control.transport'`.

- [ ] **Step 3: Implementación**

`src/eovrt_control/transport/__init__.py`: vacio.

`src/eovrt_control/transport/alert_bus.py` — copiar la estructura de `e-ovrt_media-plane/src/eovrt_media/transport/bus.py` (`encode_envelope` + `BusPublisher`), renombrando a `AlertBusPublisher` y con estas constantes:

```python
"""Publisher de alertas al bus (espejo del BusPublisher del media-plane).

Mismo envelope bus.envelope.v1 y las mismas garantias: seq monotono consumido
aunque el envio se descarte; nunca bloquea ni propaga excepciones al runtime;
el JSONL es la verdad, el bus solo transporta (ADR-003).
"""
from __future__ import annotations
import logging
import time
import msgpack
import zmq

logger = logging.getLogger(__name__)

ENVELOPE_SCHEMA_VERSION = "bus.envelope.v1"
ALERT_TOPIC_PREFIX = "control.alert.v1."
LIFECYCLE_TOPIC_PREFIX = "run.lifecycle.v1."
LIFECYCLE_SCHEMA_VERSION = "run.lifecycle.v1"


def encode_envelope(*, topic, key, seq, payload, ts_publish_ms):
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
```
El resto de `AlertBusPublisher` (ctor con XPUB, `SNDHWM/RCVHWM/LINGER=0/XPUB_VERBOSE=1`, `bind`, `wait_for_subscriber`, `publish` con `seq` pre-incrementado y `flags=zmq.NOBLOCK`, `close` idempotente) es **copia** de `BusPublisher`. Agregar `publish_run_finished`:

```python
def publish_run_finished(self, control_run_id: str, status: str) -> None:
    import json
    payload = json.dumps(
        {
            "schema_version": LIFECYCLE_SCHEMA_VERSION,
            "event": "run_finished",
            "control_run_id": control_run_id,
            "status": status,
        }
    ).encode("utf-8")
    self.publish(f"{LIFECYCLE_TOPIC_PREFIX}{control_run_id}", control_run_id, payload)
```
> **No copiar verbatim sin leer los §6 de docs 37/38/39**: el `BusPublisher` del media-plane ya trae las correcciones (fuga de socket en `__init__`, `close` protegido, `send_failures` que "miente por diseño" documentado). Copiar el archivo **vigente** del working tree, no el del plan del media-plane.

`config.py`: nueva seccion (imitar `BusConfig` del media-plane), montada en `ReplayConfig` (`:147-161`):

```python
class AlertBusSection(BaseModel):
    """Publisher de alertas control->distribucion (spec 41 §8). Apagado por default."""
    enabled: bool = False
    endpoint: str = "tcp://0.0.0.0:5558"
    hwm: int = Field(default=1000, gt=0)
    wait_for_subscriber_ms: int = Field(default=0, ge=0)
```
```python
class ReplayConfig(BaseModel):
    ...
    alert_bus: AlertBusSection = Field(default_factory=AlertBusSection)
```

`runtime/core.py`: construir el publisher (protegido, la corrida sigue sin bus si falla), publicar **después** de escribir cada alerta, y emitir `run_finished` en el `finally`:

```python
alert_publisher = None
if config.alert_bus.enabled:
    try:
        alert_publisher = AlertBusPublisher(
            config.alert_bus.endpoint,
            hwm=config.alert_bus.hwm,
            wait_for_subscriber_ms=config.alert_bus.wait_for_subscriber_ms,
        )
    except Exception:  # noqa: BLE001 - la corrida continua sin bus
        logger.warning("alert_bus: no se pudo iniciar el publisher; corrida sin bus")
        alert_publisher = None
...
for alert in result.alerts:
    alert_sink.write(alert)                       # persiste PRIMERO
    if alert_publisher is not None:
        payload = alert.model_dump_json(exclude_none=True).encode("utf-8")
        alert_publisher.publish(f"control.alert.v1.{control_run_id}", alert.source_id, payload)
    ...
```
En el `finally` (`core.py:224`, junto a `source.close()`):

```python
finally:
    if alert_publisher is not None:
        try:
            alert_publisher.publish_run_finished(control_run_id, run_status)
        finally:
            alert_publisher.close()
    source.close()
```
(`run_status` = el estado con que cierra la corrida; si no existe una variable equivalente, usar `"succeeded"` por default y `"failed"`/`"stopped"` en los caminos correspondientes, igual que el media-plane `pipeline.py:577-590`.)

- [ ] **Step 4: Correr los tests nuevos — deben pasar**

Run: `.venv/bin/python -m pytest tests/test_alert_bus.py -v`
Expected: PASS (2).

- [ ] **Step 5: Suite completa + lint**

Run: `.venv/bin/python -m pytest -q --ignore=tests/labs && .venv/bin/python -m ruff check src tests`
Expected: 167 passed (165 + 2); `All checks passed!`.

- [ ] **Step 6: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_control/transport src/eovrt_control/config.py src/eovrt_control/runtime/core.py tests/test_alert_bus.py
git commit -m "feat(transport): publisher de alertas control.alert.v1 al bus"
```

---

## Task 8: Join `t_capture→alert` con estado de aplicabilidad por fuente

El helper que une la alerta (por `first_evidence_unit_id`) con la fila de métricas del media-plane que la produjo, y declara el estado de aplicabilidad según `source_clock` (spec 40 §5.2.3, ADR-006). Es el insumo del reporte consolidado (ítem 6) y lo que ejercita el gate (Task 9).

**Reglas de aplicabilidad (spec 40 §5.2.3, verbatim):**
- `source_clock == "wallclock"` (RTSP/EBE single-host): `computed`, valor = `alert_registered_ms − capture_monotonic_ms(first_evidence_unit)`.
- `source_clock == "media"` (DBE video): `not_interpretable` / `dbe_media_time`.
- `source_clock == "none"` (dataset de imágenes): `not_applicable` / `non_temporal_source`.
- `two_node=True` (EBE dos hosts, sin sync declarada): `not_interpretable` / `clock_skew`.

**Files:**
- Modify: `src/eovrt_control/metrics/latency.py` (agregar `join_capture_to_alert`)
- Test: `tests/test_latency.py`

**Interfaces:**
- Consumes: lista de `AlertEvent` (dicts o modelos), dict `capture_monotonic_ns` por `unit_id` (leído del `metrics.jsonl` del media-plane), `source_clock: str`, `two_node: bool`.
- Produces: `join_capture_to_alert(alerts, capture_ns_by_unit, *, source_clock, two_node=False) -> list[dict]` con `{alert_id, first_evidence_unit_id, status, cause, t_capture_to_alert_ms}` (`cause`/valor `None` cuando corresponde).

- [ ] **Step 1: Escribir el test que falla**

En `tests/test_latency.py`:

```python
from eovrt_control.metrics.latency import join_capture_to_alert


def _alert(alert_id, unit_id, alert_registered_ms):
    return {"alert_id": alert_id, "first_evidence_unit_id": unit_id,
            "alert_registered_ms": alert_registered_ms}


def test_join_computed_on_wallclock_single_host():
    alerts = [_alert("a1", "u1", 5000.0)]
    cap = {"u1": 4_000_000_000}  # 4000 ms en ns
    out = join_capture_to_alert(alerts, cap, source_clock="wallclock")
    assert out[0]["status"] == "computed" and out[0]["cause"] is None
    assert abs(out[0]["t_capture_to_alert_ms"] - 1000.0) < 1e-6


def test_join_not_interpretable_on_media():
    out = join_capture_to_alert([_alert("a1", "u1", 5000.0)], {"u1": 1}, source_clock="media")
    assert out[0]["status"] == "not_interpretable" and out[0]["cause"] == "dbe_media_time"
    assert out[0]["t_capture_to_alert_ms"] is None


def test_join_not_applicable_on_images():
    out = join_capture_to_alert([_alert("a1", "u1", 5000.0)], {}, source_clock="none")
    assert out[0]["status"] == "not_applicable" and out[0]["cause"] == "non_temporal_source"


def test_join_two_node_is_clock_skew():
    out = join_capture_to_alert([_alert("a1", "u1", 5000.0)], {"u1": 1},
                                source_clock="wallclock", two_node=True)
    assert out[0]["status"] == "not_interpretable" and out[0]["cause"] == "clock_skew"


def test_join_missing_capture_row_is_applicable_not_computed():
    out = join_capture_to_alert([_alert("a1", "u-missing", 5000.0)], {}, source_clock="wallclock")
    assert out[0]["status"] == "applicable_not_computed"
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `.venv/bin/python -m pytest tests/test_latency.py -k join -v`
Expected: FAIL — `ImportError: cannot import name 'join_capture_to_alert'`.

- [ ] **Step 3: Implementación** (agregar a `metrics/latency.py`)

```python
def join_capture_to_alert(alerts, capture_ns_by_unit, *, source_clock, two_node=False):
    """Une cada alerta (por first_evidence_unit_id) con la captura del media-plane
    y declara el estado de aplicabilidad de t_capture->alert (ADR-006, spec 40 §5.2.3).

    - wallclock single-host: computed (valor = alert_registered_ms - capture_ms).
    - media (DBE video): not_interpretable / dbe_media_time.
    - none (imagenes): not_applicable / non_temporal_source.
    - two_node sin sync: not_interpretable / clock_skew.
    - captura ausente para el unit_id: applicable_not_computed.
    """
    results = []
    for a in alerts:
        alert_id = a["alert_id"] if isinstance(a, dict) else a.alert_id
        unit_id = a["first_evidence_unit_id"] if isinstance(a, dict) else a.first_evidence_unit_id
        registered = a["alert_registered_ms"] if isinstance(a, dict) else a.alert_registered_ms
        row = {"alert_id": alert_id, "first_evidence_unit_id": unit_id,
               "status": None, "cause": None, "t_capture_to_alert_ms": None}
        if source_clock == "none":
            row["status"], row["cause"] = "not_applicable", "non_temporal_source"
        elif source_clock == "media":
            row["status"], row["cause"] = "not_interpretable", "dbe_media_time"
        elif two_node:
            row["status"], row["cause"] = "not_interpretable", "clock_skew"
        else:  # wallclock single-host
            cap_ns = capture_ns_by_unit.get(unit_id)
            if cap_ns is None or registered is None:
                row["status"] = "applicable_not_computed"
            else:
                row["status"] = "computed"
                row["t_capture_to_alert_ms"] = registered - (cap_ns / 1e6)
        results.append(row)
    return results
```

- [ ] **Step 4: Correr los tests nuevos — deben pasar**

Run: `.venv/bin/python -m pytest tests/test_latency.py -k join -v`
Expected: PASS (5).

- [ ] **Step 5: Suite completa**

Run: `.venv/bin/python -m pytest -q --ignore=tests/labs`
Expected: 172 passed (167 + 5).

- [ ] **Step 6: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_control/metrics/latency.py tests/test_latency.py
git commit -m "feat(metrics): join t_capture->alert con estados de aplicabilidad"
```

---

## Task 9: **Gate** — corrida live real de punta a punta + verificación por mutación

Demuestra el join `t_capture→alert` con datos reales (spec 41 §8 item 8): dispara los dos servicios en el **orden correcto** (control-plane primero, su 201 implica suscripto), corre por bus, y computa `t_capture→alert` uniendo el `alerts.jsonl` del control-plane con el `metrics.jsonl` del media-plane por `unit_id`. Verifica que el gate es significativo por mutación. Archiva la evidencia.

**Files:**
- Create: `tests/test_capture_to_alert_gate.py` (gate estructural sobre artefactos)
- Evidencia: `docs/operacion/datos/` (repo `docs`) — `summary.json` del control-plane y el join computado.

**Interfaces:**
- Consumes: todo lo anterior (`ts_receive_ms`, `first_evidence_*`, `alert_registered_ms`, `AlertBusPublisher`, `join_capture_to_alert`, `cr01_cr02_v2`).
- Produces: prueba de que en la corrida live (single-host, `source_clock: media`→`not_interpretable` para video, o `wallclock`→`computed` si RTSP) los hitos existen y el join declara el estado correcto.

- [ ] **Step 1: Escribir el gate estructural**

`tests/test_capture_to_alert_gate.py` — corre el pipeline live in-proc (o sobre artefactos de una corrida) y asegura los invariantes de la cadena. Imitar `test_bus_parity.py`/`test_service_live.py`:

```python
def test_every_alert_has_first_evidence_and_registered(live_run_artifacts):
    # live_run_artifacts: fixture que corre una live corta por bus y devuelve
    # (alerts, media_metrics_by_unit, source_clock) leidos de los JSONL reales.
    alerts, cap_by_unit, source_clock = live_run_artifacts
    assert alerts, "la corrida debe producir >=1 alerta"
    for a in alerts:
        assert a["first_evidence_unit_id"] is not None       # clave de join obligatoria
        assert a["first_evidence_ms"] is not None
        assert a["alert_registered_ms"] is not None
        assert a["alert_registered_ms"] >= a["first_evidence_ms"]  # registro >= primera evidencia


def test_join_declares_expected_applicability(live_run_artifacts):
    from eovrt_control.metrics.latency import join_capture_to_alert
    alerts, cap_by_unit, source_clock = live_run_artifacts
    out = join_capture_to_alert(alerts, cap_by_unit, source_clock=source_clock)
    if source_clock == "media":
        assert all(r["status"] == "not_interpretable" and r["cause"] == "dbe_media_time" for r in out)
    elif source_clock == "wallclock":
        assert all(r["status"] == "computed" for r in out)
```

- [ ] **Step 2: Correr el gate para verificar que pasa**

Run: `.venv/bin/python -m pytest tests/test_capture_to_alert_gate.py -v`
Expected: PASS (2).

- [ ] **Step 3: Verificar que el gate es significativo (mutación) — obligatorio**

Un gate que no puede fallar no es un gate. Hacé **dos** mutaciones, una por vez, y reportá la salida literal de cada una:

1. En `pattern_engine.py`, en el branch de apertura de episodio (Task 2), comentá la asignación de `runtime_state.first_evidence_unit_id = event.unit_id` (dejarlo `None`). Esperado: **falla** `test_every_alert_has_first_evidence_and_registered` (`first_evidence_unit_id is None`). **Revertí.**
2. En `metrics/latency.py`, en `join_capture_to_alert`, cambiá la rama `media` para devolver `status="computed"`. Esperado: **falla** `test_join_declares_expected_applicability` sobre una corrida de video. **Revertí.**

Si alguna mutación **no** hace fallar el gate, el gate es vacuo: arreglalo antes de seguir y explicá qué cambiaste.

- [ ] **Step 4: Corrida live real de los dos servicios (urllib, no curl)**

Reproducir el orden del spec 44 (control-plane primero). Levantar el control-plane (`eovrt-control serve` en :8081) y el media-plane (mock, en :8080) en background; disparar con `.venv/bin/python - <<'PY' ... urllib.request.urlopen(...) ... PY`:
1. `POST :8081/api/runs` con `mode: live`, `input.type: bus`, `patterns.file` → `cr01_cr02_v2`, `alert_bus.enabled: true` → 201 con `subscribed: true`.
2. `POST :8080/api/runs` con `bus.enabled: true` sobre un video real (o fixture) → dispara.
3. `GET :8081/api/runs/{id}` hasta cierre por `run_finished`; leer `alerts.jsonl` y el `metrics.jsonl` del media-plane.
4. Computar el join con `join_capture_to_alert` y verificar el estado de aplicabilidad esperado por `source_clock`.
`pkill` de ambos uvicorn al terminar. **curl está bloqueado en este entorno: usar `urllib`.**

- [ ] **Step 5: Archivar evidencia**

Copiar el `summary.json` del control-plane y el join computado (como `.json`) a `docs/operacion/datos/` (repo `docs`), con prefijo de esta corrida (p.ej. `5b-*`). `runs/` se poda — la evidencia que un doc cite vive en `operacion/datos/`.

- [ ] **Step 6: Suite completa + lint (final)**

Run: `.venv/bin/python -m pytest -q --ignore=tests/labs && .venv/bin/python -m ruff check src tests`
Expected: 174 passed (172 + 2); `All checks passed!`.

- [ ] **Step 7: Commit (sólo si el usuario lo pidió)**

```bash
git add tests/test_capture_to_alert_gate.py
git commit -m "test(gate): cadena t_capture->alert e2e + verificacion por mutacion"
```

---

## Cierre

- [ ] **Actualizar `CLAUDE.md`** del control-plane (`e-ovrt_control-plane/CLAUDE.md`): documentar `cr01_cr02_v2` como pattern set oficial, el publisher `control.alert.v1`, y los nuevos campos de instrumentación (`ts_receive_ms`, `first_evidence_*`, `alert_registered_ms`, percentiles/TTFA en el summary).
- [ ] **Registrar la deuda** que surja (candidatos ya conocidos de doc 50 §8.3): #2 (`experiment_id` no viaja en el `POST /api/runs` del media-plane — el runner del ítem 6 lo necesita); si el join en two-node queda declarado `not_interpretable/clock_skew` sin sync, es la deuda #8. Anotar en el doc `operacion/` de resultados.
- [ ] **Escribir el doc de resultados** en `docs/operacion/` (siguiente número libre de la serie 50-), con: qué se construyó, números medidos de la corrida live, defectos que la revisión adversarial atrapó (§6 del doc), y la deuda. Agregar a este plan el banner **EJECUTADO** apuntando a ese doc.
- [ ] **Verificación final:** pegar la salida de `pytest -q --ignore=tests/labs` (174 passed), `ruff check src tests` (limpio), y la salida literal de las dos mutaciones del gate.

## Alineación con el informe (self-review de cobertura)

| Sub-ítem §8.1 | Task |
|---|---|
| 1. `ts_receive_ms` en `BusSource` → `metrics.jsonl` | 1 |
| 2. `first_evidence_ms/frame_index/unit_id` en transición y alerta | 2, 3 |
| 3. `alert_registered_ms` | 3 |
| 4. `experiment_id` en los eventos | 4 |
| 5. Percentiles P50/P95/P99 de `processing_ms` + TTFA interna | 5 |
| 6. Pattern set `cr01_cr02_v2` oficial | 6 |
| 7. Publisher de alertas al bus | 7 |
| 8. Join `t_capture→alert` con aplicabilidad + e2e real | 8, 9 |
| `evaluate-alerts` v2 | **plan hermano** (fuera de alcance, ver Global Constraints) |
