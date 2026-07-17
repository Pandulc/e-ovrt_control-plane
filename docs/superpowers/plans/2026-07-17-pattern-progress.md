# Progreso parcial de patrones (Pieza A) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** El motor de patrones materializa el progreso parcial (`elapsed/umbral`, 0–1) de cada condición en estado `candidate`, lo persiste por-frame en `pattern_progress.jsonl` y lo expone en `GET /api/runs/{id}/pattern-progress`.

**Architecture:** Contrato nuevo `PatternProgress` (convención de `PatternStateChanged`). El motor lo emite desde `process()` como salida aditiva de `PatternEngineResult`; el runtime lo escribe con un `JsonlSink` más; el servicio lo sirve espejando `RunManager.alerts()`. Nada existente cambia de forma.

**Tech Stack:** Python 3.11, pydantic, FastAPI, pytest. Repo: `e-ovrt_control-plane`.

**Spec:** `docs/superpowers/specs/2026-07-17-pattern-progress-design.md`

## Global Constraints

- **MODO SIN COMMITS** (regla de `projects/CLAUDE.md`): todo en working tree; los pasos "Commit" son puntos de corte, no instrucciones.
- **Aditivo estricto**: `pattern_events.jsonl`, `alerts.jsonl`, `summary` y la máquina de estados quedan **byte/comportamiento-idénticos**. `PatternStateChanged` y `AlertEvent` no se tocan.
- Progreso **solo** en estado `candidate` (D3). `progress = clamp(elapsed/threshold, 0, 1)` relativo a cada condición (D2). Modo `time` si hay `confirm_after_ms` + timestamps; si no, `frames` (espeja `_confirmation_met`, `pattern_engine.py:397-410`).
- Tests: `.venv/bin/python -m pytest tests/ -q` desde la raíz del repo. Suite previa verde es prerequisito y postcondición.

---

### Task 1: Contrato `PatternProgress`

**Files:**
- Modify: `src/eovrt_control/contracts/pattern.py` (append al final)
- Test: `tests/test_pattern_engine.py` (append)

**Interfaces:**
- Produces: `PatternProgress(BaseModel)` con `schema_version="control.pattern_progress.v1"`, campos abajo. Lo consumen Tasks 2-4.

- [ ] **Step 1: Test que falla**

Append a `tests/test_pattern_engine.py`:

```python
def test_pattern_progress_contract_defaults() -> None:
    from eovrt_control.contracts.pattern import PatternProgress

    rec = PatternProgress(
        control_run_id="c", media_run_id="m", unit_id="unit-0",
        pattern_id="CR-01", condition_id="CR-01", subject_key="s",
        mode="time", elapsed_ms=500.0, threshold_ms=1000.0,
        elapsed_frames=2, progress=0.5,
    )
    assert rec.schema_version == "control.pattern_progress.v1"
    assert rec.event_type == "pattern_progress"
    assert rec.threshold_frames is None and rec.source_id is None
```

- [ ] **Step 2: Verificar que falla** — Run: `.venv/bin/python -m pytest tests/test_pattern_engine.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implementar** — append a `src/eovrt_control/contracts/pattern.py`:

```python
class PatternProgress(BaseModel):
    """Progreso parcial de un patron en estado candidate (spec pattern-progress).

    Un registro por (frame, patron, sujeto) mientras la condicion esta en curso.
    Convencion de ids identica a PatternStateChanged. Aditivo: no reemplaza nada.
    """

    schema_version: str = "control.pattern_progress.v1"
    event_type: str = "pattern_progress"
    control_run_id: str
    media_run_id: str
    unit_id: str
    source_id: str | None = None
    pattern_id: str
    condition_id: str
    subject_key: str
    frame_index: int | None = None
    timestamp_ms: float | None = None
    mode: str  # "time" | "frames" — cual umbral rige (espeja _confirmation_met)
    elapsed_ms: float | None = None      # solo mode=time
    threshold_ms: float | None = None    # solo mode=time
    elapsed_frames: int                  # = hit_count, siempre
    threshold_frames: int | None = None  # solo si el patron define confirm_after_frames
    progress: float                      # 0..1 clamp
    experiment_id: str | None = None
```

- [ ] **Step 4: Verificar que pasa** — mismo comando → PASS.
- [ ] **Step 5: Punto de corte (sin commit).**

---

### Task 2: Emisión en el motor

**Files:**
- Modify: `src/eovrt_control/engine/pattern_engine.py`
- Test: `tests/test_pattern_engine.py` (append)

**Interfaces:**
- Consumes: `PatternProgress` (Task 1); helpers de test existentes `_time_pattern()` (confirm_after_ms=1000, confirm_after_frames=99) y `_event_at(timestamp_ms, frame_index, has_helmet=...)`.
- Produces: `PatternEngineResult.progress: list[PatternProgress]` (campo nuevo, default vacío) y método privado `_progress_record(...)`.

- [ ] **Step 1: Tests que fallan** — append a `tests/test_pattern_engine.py`:

```python
def test_progress_ratio_and_clamp_in_time_mode() -> None:
    engine = PatternEngine(control_run_id="control-run", patterns=[_time_pattern()])
    r0 = engine.process(_event_at(0.0, 0))       # entra a candidate, elapsed=0
    r1 = engine.process(_event_at(500.0, 1))     # mitad del umbral (1000ms)
    r2 = engine.process(_event_at(1000.0, 2))    # cruza umbral -> confirmed

    assert [p.progress for p in r0.progress] == [0.0]
    assert [p.progress for p in r1.progress] == [0.5]
    p = r1.progress[0]
    assert (p.mode, p.elapsed_ms, p.threshold_ms) == ("time", 500.0, 1000.0)
    assert p.elapsed_frames == 2 and p.threshold_frames == 99
    # D3: al confirmarse deja de emitir progreso
    assert r2.progress == [] and [e.state for e in r2.pattern_events] == ["confirmed"]


def test_progress_resets_on_new_episode() -> None:
    engine = PatternEngine(control_run_id="control-run", patterns=[_time_pattern()])
    engine.process(_event_at(0.0, 0))
    engine.process(_event_at(400.0, 1))                      # 0.4
    engine.process(_event_at(600.0, 2, has_helmet=True))     # cubierto
    engine.process(_event_at(1200.0, 3, has_helmet=True))    # resolve (500ms clear)
    r = engine.process(_event_at(2000.0, 4))                 # episodio nuevo
    assert [p.progress for p in r.progress] == [0.0]


def test_progress_frames_mode_without_clock() -> None:
    pattern = _time_pattern()
    pattern.timing.confirm_after_ms = None   # sin umbral temporal -> rige frames (99)
    engine = PatternEngine(control_run_id="control-run", patterns=[pattern])
    engine.process(_event_at(0.0, 0))
    r = engine.process(_event_at(0.0, 1))
    p = r.progress[0]
    assert p.mode == "frames" and p.elapsed_ms is None and p.threshold_ms is None
    assert p.progress == 2 / 99 and p.elapsed_frames == 2


def test_progress_does_not_alter_state_machine_or_alerts() -> None:
    engine = PatternEngine(control_run_id="control-run", patterns=[_time_pattern()])
    engine.process(_event_at(0.0, 0))
    r = engine.process(_event_at(1000.0, 1))
    assert len(r.alerts) == 1  # el comportamiento previo de confirmacion no cambia
```

- [ ] **Step 2: Verificar que fallan** — `.venv/bin/python -m pytest tests/test_pattern_engine.py -q` → FAIL (`progress` no existe).

- [ ] **Step 3: Implementar.**

(a) En `PatternEngineResult` (línea 52) agregar:

```python
    progress: list["PatternProgress"] = field(default_factory=list)
```

e importar `PatternProgress` junto a `PatternStateChanged` (línea 12).

(b) En `process()`: inicializar `progress_records: list[PatternProgress] = []` junto a `pattern_events`; **dentro del loop de evidencia**, inmediatamente después de `change = self._advance_hit(event, pattern, subject_key, evidence)` y **antes** del `if change is None: continue` (líneas 132-134 — el `continue` se tragaría la emisión):

```python
                runtime_state = self._state[(pattern.id, subject_key)]
                prog = self._progress_record(event, pattern, subject_key, runtime_state)
                if prog is not None:
                    progress_records.append(prog)
```

y en el `return` final agregar `progress=progress_records`.

(c) Método nuevo (después de `_advance_hit`):

```python
    def _progress_record(
        self,
        event: DetectionEvent,
        pattern: PatternDefinition,
        subject_key: str,
        runtime_state: PatternRuntimeState,
    ) -> PatternProgress | None:
        """Progreso parcial del episodio en curso. Solo en candidate (spec D3).

        Espeja la seleccion de modo de _confirmation_met: time si hay umbral
        temporal + timestamps; si no, frames. Derivado puro: no muta estado.
        """
        if runtime_state.state != "candidate":
            return None
        timing = pattern.timing
        if (
            timing.confirm_after_ms is not None
            and event.source.timestamp_ms is not None
            and runtime_state.first_hit_timestamp_ms is not None
        ):
            elapsed_ms = event.source.timestamp_ms - runtime_state.first_hit_timestamp_ms
            mode, threshold_ms = "time", timing.confirm_after_ms
            ratio = elapsed_ms / threshold_ms if threshold_ms > 0 else 0.0
        elif timing.confirm_after_frames:
            elapsed_ms, threshold_ms = None, None
            mode = "frames"
            ratio = runtime_state.hit_count / timing.confirm_after_frames
        else:
            return None
        return PatternProgress(
            control_run_id=self.control_run_id,
            media_run_id=event.run_id,
            unit_id=event.unit_id,
            source_id=event.source.source_id,
            pattern_id=pattern.id,
            condition_id=pattern.condition_id,
            subject_key=subject_key,
            frame_index=event.source.frame_index,
            timestamp_ms=event.source.timestamp_ms,
            mode=mode,
            elapsed_ms=elapsed_ms,
            threshold_ms=threshold_ms,
            elapsed_frames=runtime_state.hit_count,
            threshold_frames=timing.confirm_after_frames,
            progress=max(0.0, min(ratio, 1.0)),
            experiment_id=self.experiment_id,
        )
```

Nota: si `event.source.source_id` no tipa (`str` vs `str | None`), el contrato ya lo admite opcional — no forzar.

- [ ] **Step 4: Verificar que pasan** — `.venv/bin/python -m pytest tests/test_pattern_engine.py -q` → PASS, **incluidos todos los tests preexistentes del archivo sin tocarlos** (invariante de no-regresión de la máquina de estados).
- [ ] **Step 5: Punto de corte (sin commit).**

---

### Task 3: Persistencia en el runtime

**Files:**
- Modify: `src/eovrt_control/sinks/artifacts.py`, `src/eovrt_control/runtime/core.py`
- Test: `tests/test_replay.py` (append, siguiendo el estilo/fixtures del propio archivo)

**Interfaces:**
- Consumes: `PatternEngineResult.progress` (Task 2), `JsonlSink` (`sinks/jsonl.py`), `ArtifactPaths` (`sinks/artifacts.py`, que ya define `pattern_events_path`/`alerts_path`).
- Produces: `runs/<id>/pattern_progress.jsonl`; property `ArtifactPaths.pattern_progress_path`.

- [ ] **Step 1: Test que falla** — append a `tests/test_replay.py`, reusando el fixture de replay existente en ese archivo que produce una corrida con una condición que llega a `candidate` (el mismo que hoy assertea sobre `pattern_events.jsonl`; copiar su setup tal cual):

```python
def test_replay_writes_pattern_progress_jsonl(tmp_path) -> None:
    # Setup: identico al del test de pattern_events de este archivo (mismo
    # config/eventos de entrada); cambia solo lo asserteado.
    run_dir = _run_replay_case(tmp_path)  # usar el helper/fixture REAL del archivo
    progress_path = run_dir / "pattern_progress.jsonl"
    assert progress_path.exists()
    rows = [json.loads(l) for l in progress_path.read_text().splitlines() if l.strip()]
    assert rows, "al menos un frame en candidate debe emitir progreso"
    assert all(r["schema_version"] == "control.pattern_progress.v1" for r in rows)
    assert all(0.0 <= r["progress"] <= 1.0 for r in rows)
```

**Nota al implementador**: `_run_replay_case` es el nombre ilustrativo — usá el helper real con el que los tests vecinos de `test_replay.py` obtienen el `run_dir`; si ninguno expone el dir, replicá el setup del test de `pattern_events.jsonl` del archivo.

- [ ] **Step 2: Verificar que falla** — `.venv/bin/python -m pytest tests/test_replay.py -q` → FAIL (archivo no existe).

- [ ] **Step 3: Implementar.**

(a) `sinks/artifacts.py` — junto a `pattern_events_path` (línea 21):

```python
    @property
    def pattern_progress_path(self) -> Path:
        return self.run_dir / "pattern_progress.jsonl"
```

(b) `runtime/core.py` — en el `with` de sinks (líneas 189-192) agregar:

```python
            JsonlSink(artifacts.pattern_progress_path) as progress_sink,
```

y junto al write de pattern_events (línea 232):

```python
                for progress_record in result.progress:
                    progress_sink.write(progress_record)
```

- [ ] **Step 4: Verificar que pasa** — `.venv/bin/python -m pytest tests/test_replay.py -q` → PASS, incluidos los preexistentes (los artefactos viejos no cambian).
- [ ] **Step 5: Punto de corte (sin commit).**

---

### Task 4: Endpoint del servicio

**Files:**
- Modify: `src/eovrt_control/service/run_manager.py`, `src/eovrt_control/service/routers/runs.py`
- Test: `tests/test_service_api.py` (append)

**Interfaces:**
- Consumes: patrón exacto de `RunManager.alerts()` (`run_manager.py:179-203`) y su router (`routers/runs.py:52-57`); fixtures `client`/`tmp_path` existentes en `tests/test_service_api.py` (mirar cómo `test_alerts_endpoint_serves_the_alert_events` arma el run_dir y escribí el jsonl igual).
- Produces: `GET /api/runs/{run_id}/pattern-progress?limit=`.

- [ ] **Step 1: Tests que fallan** — append a `tests/test_service_api.py`, replicando el armado de run_dir del test de alertas del mismo archivo:

```python
def test_pattern_progress_endpoint_serves_rows(client, tmp_path) -> None:
    # armar run_dir + pattern_progress.jsonl igual que el test de alerts arma alerts.jsonl
    ...  # (copiar el setup real del archivo)
    response = client.get(f"/api/runs/{run_id}/pattern-progress")
    assert response.status_code == 200
    rows = response.json()
    assert [r["progress"] for r in rows] == [0.25, 0.5]

    limited = client.get(f"/api/runs/{run_id}/pattern-progress?limit=1")
    assert [r["progress"] for r in limited.json()] == [0.25]


def test_pattern_progress_endpoint_unknown_run_404(client) -> None:
    assert client.get("/api/runs/no-existe/pattern-progress").status_code == 404


def test_pattern_progress_endpoint_missing_file_returns_empty(client, tmp_path) -> None:
    # run_dir valido SIN pattern_progress.jsonl -> 200 [] (spec §6)
    ...  # (mismo armado de run_dir, sin escribir el archivo)
    response = client.get(f"/api/runs/{run_id}/pattern-progress")
    assert response.status_code == 200 and response.json() == []
```

Los `...` son el armado de fixtures que DEBE copiarse del test de alertas vecino (no inventarlo): mismo mecanismo de `runs_dir`, mismo formato de líneas jsonl (dicts con `progress`: 0.25 y 0.5).

- [ ] **Step 2: Verificar que fallan** — `.venv/bin/python -m pytest tests/test_service_api.py -q` → FAIL (404 en la ruta nueva).

- [ ] **Step 3: Implementar.**

(a) `run_manager.py` — método nuevo debajo de `alerts()`, misma forma (incluida la tolerancia a línea corrupta):

```python
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
                    logger.warning(
                        "progreso ilegible en %s linea %d: %s", run_id, line_no, exc
                    )
        if limit is not None:
            rows = rows[: max(limit, 0)]
        return rows
```

(b) `routers/runs.py` — debajo del endpoint de alerts (líneas 52-57), espejo exacto (mismo manejo de `UnknownRunError`→404 que use el de alerts):

```python
@router.get("/runs/{run_id}/pattern-progress")
def get_run_pattern_progress(
    run_id: str, request: Request, limit: int | None = Query(default=None, ge=0)
):
    return _manager(request).pattern_progress(run_id, limit=limit)
```

(ajustar el cuerpo al wrapper try/except que el endpoint de alerts ya use en el archivo — copiarlo, no reinventarlo).

- [ ] **Step 4: Verificar que pasan** — `.venv/bin/python -m pytest tests/test_service_api.py -q` → PASS.
- [ ] **Step 5: Punto de corte (sin commit).**

---

### Task 5: Gate final

**Files:** ninguno nuevo — verificación.

- [ ] **Step 1: Suite completa** — `.venv/bin/python -m pytest tests/ -q` → PASS total (baseline previa + los nuevos).
- [ ] **Step 2: No-regresión de artefactos** — `git diff src/eovrt_control/contracts/alerts.py src/eovrt_control/sinks/jsonl.py` → vacío; en `pattern_engine.py` el diff no toca `_advance_hit`/`_advance_clear`/`_confirmation_met`/`_maybe_alert` salvo el punto de emisión declarado en Task 2.
- [ ] **Step 3: Smoke real** — con el servicio levantado (`eovrt-control serve`), correr un replay que produzca `candidate` y `curl :8081/api/runs/<id>/pattern-progress` → filas con `progress` creciente 0→<1 y ninguna tras la confirmación (criterios 1-2 del spec).
- [ ] **Step 4: Punto de corte (sin commit).**
