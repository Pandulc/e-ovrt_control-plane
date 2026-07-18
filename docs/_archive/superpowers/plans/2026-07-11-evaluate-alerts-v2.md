# `evaluate-alerts` v2 — matching episodio en ms, `re_alerts` y aplicabilidad

> **EJECUTADO el 2026-07-11.** Las 5 tareas están completas (control-plane **192 passed**, ruff
> limpio; revisión final de rama: LISTO CON RESERVAS, sin Critical/Important bloqueante).
> Resultados, evidencia y deuda: `docs/operacion/52-evaluate-alerts-v2.md` (repo `docs`).
>
> **El código de referencia de este plan tenía defectos reales**, hallados por la revisión
> adversarial por tarea y corregidos durante la ejecución. **No lo copies verbatim**: el código
> vigente es el del working tree. Los defectos: doble-conteo entre episodios de misma condición
> con ventanas solapadas (P8) — corregido con `consumed_alert_ids`; el placeholder de `evidence`
> del plan es inválido (el `PatternEvidence` real exige más campos). Reserva registrada: la
> asignación greedy por episodio puede DEFLACIONAR recall en P8 con ≥2 alertas — deuda alta,
> trackear antes de scorear clips reales. Ver doc 52 §3-§4.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Llevar `evaluate-alerts` (control-plane) a v2 (spec 41 §8 item 7): consumir el `clip_gt.v2` **real** de spec 43 (episodios escena-condición con `start_ms`/`end_ms`), matchear alerta↔episodio por **ventana en ms** `[start_ms + persistencia_min, start_ms + t_alert_max]`, contar las alertas extra del mismo episodio como **`re_alerts`** (ADR-011, estabilidad de percepción — NO falsos positivos), clasificar los `sub_threshold_events`, y emitir **estados de aplicabilidad** (ADR-006). El path v1 (`TemporalGroundTruth`, frame-based) queda intacto.

**Architecture:** `src/eovrt_control/evaluation/temporal.py` hoy tiene un solo path que aplana el "v2" (un scaffold **frame-index**, no conforme a spec 43) sobre el matcher v1. Este plan separa dos paths por `schema_version`: `control.eval.temporal.v1` (`TemporalGroundTruth`, frame-based, sin cambios) y `clip_gt.v2` (ms-based, matcher nuevo). El `clip_gt.v2` real es **ms**: cada episodio anota `start_ms`/`end_ms` de la condición física; una alerta matchea si su `condition_id` coincide y su `timestamp_ms` cae en `[start_ms + persistencia_min, start_ms + t_alert_max]`, con `(persistencia_min, t_alert_max)` **por condición** tomados de la corrida (default Tabla D.4: CR-01 3s/10s, CR-02 5s/20s; parametrizable porque el fixture sintético usa timings comprimidos). Episodio detectado si ≥1 alerta cae en su ventana; alertas extra dentro del episodio → `re_alerts`; alerta dentro de un `sub_threshold_event` → `sub_threshold` (ni match ni FP); FP = alerta fuera de todo episodio y todo sub-threshold; `missed` = episodio sin alerta. Todo con test sobre fixture sintético (sin media real), como el resto del repo.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, ruff. Repo `e-ovrt_control-plane`, rama `feature/control-service`.

## Global Constraints

- **Nunca commitear sin pedido explícito del usuario en ese turno.** Los pasos "Commit" **preparan** el commit; se ejecutan sólo si el usuario lo pide en ese turno. Si no, `git add -A` y dejar el árbol sin `git commit`. (La ejecución por `subagent-driven-development` usa snapshots `git write-tree` entre tareas; eso **no** es un commit.)
- **Nunca `Co-Authored-By`. Nada en GitHub; todo local.**
- **El path v1 NO se rompe.** `TemporalGroundTruth` (`schema_version` ausente o `control.eval.temporal.v1`) sigue evaluándose frame-based con los mismos resultados. Los tests v1 existentes deben quedar verdes sin cambios de aserción.
- **Nunca publicar un número sin sentido (ADR-006).** La salida lleva `applicability_state ∈ {computed | applicable_not_computed | not_applicable | not_interpretable}` + `cause`. Alertas sin `timestamp_ms` (fuente no temporal) ⇒ `not_applicable / non_temporal_source`.
- **`re_alerts` NO son FP (ADR-011).** El motor emite en cada confirmación; la supresión es de distribución (spec 45). Las alertas extra dentro de un episodio matcheado se cuentan como `re_alerts`, jamás como `unexpected`/FP.
- **Contratos aditivos.** Los campos nuevos del output son opcionales con default; conservar `schema_version="control.eval.temporal.v1"` del `TemporalAlertEvaluation` (no bump; el output es común a v1 y v2, los campos v2 quedan en su default para v1).
- **Sin datos reales.** Todo se prueba con fixtures sintéticos `clip_gt.v2` escritos a mano. Los números reales (P/R/F1 sobre clips) están **diferidos** (spec 43, dataset de videos con GT) — fuera de alcance.
- `ruff` `line-length = 100`, `target-version = "py311"`. Comentarios/docstrings en español, sin tildes ni ñ dentro del código.
- **Entorno:** `/home/simonll4/projects/e-ovrt_control-plane/.venv/bin/python`. **Tests:** `.venv/bin/python -m pytest -q --ignore=tests/labs`. **Lint:** `.venv/bin/python -m ruff check src tests`.
- **Baseline MEDIDA (2026-07-11, HEAD `e5415df`):** `pytest -q --ignore=tests/labs` → **177 passed**, ruff limpio.

---

## Task 1: Schema `clip_gt.v2` en ms (spec 43 §4)

Reemplaza el scaffold frame-index (`ClipEpisode`/`ClipGroundTruthV2`) por el schema ms real de spec 43 §4. Valida consistencia interna (episodios dentro de `duration_ms`, `negative` ⇔ sin episodios, `level` exige su clave).

**Files:**
- Modify: `src/eovrt_control/evaluation/temporal.py:52-82` (nuevos modelos ms)
- Test: `tests/test_temporal_evaluation.py`

**Interfaces:**
- Produces: `ClipEpisodeV2(id, condition_id, level, source_id, subject_key, start_ms, end_ms, subjects_in_evidence)`; `SubThresholdEvent(condition_id, start_ms, end_ms, reason)`; `ClipGroundTruthV2(schema_version: Literal["clip_gt.v2"], clip_id, source_file, block, scenario, fps_nominal, duration_ms, recording, annotation, negative, episodes, sub_threshold_events)` — todos los campos de metadata opcionales con default salvo `schema_version`/`clip_id`/`episodes`.

- [ ] **Step 1: Escribir el test que falla**

```python
def test_clip_gt_v2_ms_schema_parses_and_validates():
    from eovrt_control.evaluation.temporal import ClipGroundTruthV2
    gt = ClipGroundTruthV2.model_validate({
        "schema_version": "clip_gt.v2", "clip_id": "c1", "duration_ms": 20000.0,
        "negative": False,
        "episodes": [
            {"id": "e1", "condition_id": "CR-01", "level": "scene",
             "source_id": "s1", "start_ms": 4000.0, "end_ms": 12000.0},
        ],
        "sub_threshold_events": [
            {"condition_id": "CR-02", "start_ms": 1000.0, "end_ms": 1500.0,
             "reason": "transitorio"},
        ],
    })
    assert gt.episodes[0].start_ms == 4000.0 and gt.episodes[0].end_ms == 12000.0
    assert gt.sub_threshold_events[0].reason == "transitorio"


def test_clip_gt_v2_episode_outside_duration_fails():
    from eovrt_control.evaluation.temporal import ClipGroundTruthV2
    import pytest
    with pytest.raises(ValueError):
        ClipGroundTruthV2.model_validate({
            "schema_version": "clip_gt.v2", "clip_id": "c1", "duration_ms": 5000.0,
            "episodes": [{"id": "e1", "condition_id": "CR-01", "level": "scene",
                          "source_id": "s1", "start_ms": 4000.0, "end_ms": 9000.0}],
        })


def test_clip_gt_v2_negative_with_episodes_fails():
    from eovrt_control.evaluation.temporal import ClipGroundTruthV2
    import pytest
    with pytest.raises(ValueError):
        ClipGroundTruthV2.model_validate({
            "schema_version": "clip_gt.v2", "clip_id": "c1", "negative": True,
            "episodes": [{"id": "e1", "condition_id": "CR-01", "level": "scene",
                          "source_id": "s1", "start_ms": 1000.0, "end_ms": 2000.0}],
        })


def test_clip_gt_v2_scene_episode_requires_source_id():
    from eovrt_control.evaluation.temporal import ClipEpisodeV2
    import pytest
    with pytest.raises(ValueError):
        ClipEpisodeV2.model_validate({"id": "e1", "condition_id": "CR-01",
                                      "level": "scene", "start_ms": 1.0, "end_ms": 2.0})
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `.venv/bin/python -m pytest tests/test_temporal_evaluation.py -k "clip_gt_v2" -v`
Expected: FAIL — `ImportError`/`AttributeError` (`ClipEpisodeV2`/`SubThresholdEvent` no existen).

- [ ] **Step 3: Implementación** (reemplazar `ClipEpisode`/`ClipGroundTruthV2` en `temporal.py:52-82`)

```python
class SubThresholdEvent(BaseModel):
    """Evento real pero no alertable (spec 43 §4.1): permite distinguir un FP
    verdadero de una alerta a un evento sub-umbral."""
    condition_id: str
    start_ms: float
    end_ms: float
    reason: str | None = None


class ClipEpisodeV2(BaseModel):
    """Episodio escena-condicion (o subject) del schema clip_gt.v2 (spec 43 §4), en ms."""
    id: str
    condition_id: str
    level: Literal["scene", "subject"] = "scene"
    source_id: str | None = None
    subject_key: str | None = None
    start_ms: float  # inicio anotado de la condicion (t0 oficial, spec 43 §4.1)
    end_ms: float
    subjects_in_evidence: int | None = None

    @model_validator(mode="after")
    def _require_key_for_level(self) -> ClipEpisodeV2:
        if self.level == "subject" and self.subject_key is None:
            raise ValueError(f"Episodio {self.id!r}: level='subject' exige `subject_key`.")
        if self.level == "scene" and self.source_id is None:
            raise ValueError(f"Episodio {self.id!r}: level='scene' exige `source_id`.")
        if self.end_ms < self.start_ms:
            raise ValueError(f"Episodio {self.id!r}: end_ms < start_ms.")
        return self


class ClipGroundTruthV2(BaseModel):
    schema_version: Literal["clip_gt.v2"]
    clip_id: str
    source_file: str | None = None
    block: str | None = None
    scenario: str | None = None
    fps_nominal: float | None = None
    duration_ms: float | None = None
    recording: dict[str, Any] | None = None
    annotation: dict[str, Any] | None = None
    negative: bool = False
    episodes: list[ClipEpisodeV2] = Field(default_factory=list)
    sub_threshold_events: list[SubThresholdEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _internal_consistency(self) -> ClipGroundTruthV2:
        if self.negative and self.episodes:
            raise ValueError(f"Clip {self.clip_id!r}: negative=True pero trae episodios.")
        if self.duration_ms is not None:
            for ep in self.episodes:
                if ep.end_ms > self.duration_ms:
                    raise ValueError(
                        f"Clip {self.clip_id!r}: episodio {ep.id!r} excede duration_ms."
                    )
        return self
```
(El viejo `ClipEpisode` frame-based se elimina; su único consumidor era `_load_ground_truth`, que se reescribe en Task 3.)

- [ ] **Step 4: Correr los tests nuevos — deben pasar**

Run: `.venv/bin/python -m pytest tests/test_temporal_evaluation.py -k "clip_gt_v2" -v`
Expected: PASS (4).

- [ ] **Step 5: Suite parcial (el resto de temporal aún puede fallar hasta Task 3)**

Run: `.venv/bin/python -m pytest tests/test_temporal_evaluation.py -k "clip_gt_v2 or v1" -v`
Expected: los 4 nuevos + los v1 pasan. (Los tests de la vieja `ClipEpisode` frame-based se migran en Task 4; si alguno referencia `ClipEpisode`, se ajusta ahí.)

- [ ] **Step 6: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_control/evaluation/temporal.py tests/test_temporal_evaluation.py
git commit -m "feat(eval): schema clip_gt.v2 en ms (spec 43 §4)"
```

---

## Task 2: Ventanas de matching en ms (Tabla D.4, parametrizable)

El matcher alerta↔episodio (spec 43 §4.1). Ventana por condición, default Tabla D.4, override para fixtures/pattern sets.

**Files:**
- Modify: `src/eovrt_control/evaluation/temporal.py` (helpers de matching)
- Test: `tests/test_temporal_evaluation.py`

**Interfaces:**
- Produces: `MatchingWindow(persistencia_min_ms: float, t_alert_max_ms: float)`; `DEFAULT_MATCHING_WINDOWS: dict[str, MatchingWindow]` = `{"CR-01": MatchingWindow(3000, 10000), "CR-02": MatchingWindow(5000, 20000)}`; `_alert_in_episode_window(alert: AlertEvent, episode: ClipEpisodeV2, window: MatchingWindow) -> bool`; `_episode_key_matches(alert, episode) -> bool`.

- [ ] **Step 1: Escribir el test que falla**

```python
def test_matching_window_default_bands():
    from eovrt_control.evaluation.temporal import DEFAULT_MATCHING_WINDOWS
    assert DEFAULT_MATCHING_WINDOWS["CR-01"].persistencia_min_ms == 3000.0
    assert DEFAULT_MATCHING_WINDOWS["CR-01"].t_alert_max_ms == 10000.0
    assert DEFAULT_MATCHING_WINDOWS["CR-02"].persistencia_min_ms == 5000.0
    assert DEFAULT_MATCHING_WINDOWS["CR-02"].t_alert_max_ms == 20000.0


def test_alert_in_episode_window_ms():
    from eovrt_control.evaluation.temporal import (
        ClipEpisodeV2, MatchingWindow, _alert_in_episode_window)
    from eovrt_control.contracts.alerts import AlertEvent
    ep = ClipEpisodeV2(id="e", condition_id="CR-01", level="scene",
                       source_id="s1", start_ms=1000.0, end_ms=8000.0)
    w = MatchingWindow(persistencia_min_ms=3000.0, t_alert_max_ms=10000.0)  # [4000, 11000]
    def _a(ts):
        return AlertEvent(control_run_id="r", media_run_id="m", unit_id="u",
                          source_id="s1", alert_id="a", pattern_id="CR-01",
                          condition_id="CR-01", subject_key="CR-01:s1", severity="high",
                          evidence={"subject": {}, "supporting": []}, timestamp_ms=ts)
    assert _alert_in_episode_window(_a(4000.0), ep, w) is True    # borde inferior
    assert _alert_in_episode_window(_a(11000.0), ep, w) is True   # borde superior
    assert _alert_in_episode_window(_a(3999.0), ep, w) is False   # antes de persistencia
    assert _alert_in_episode_window(_a(11001.0), ep, w) is False  # demasiado tarde
    assert _alert_in_episode_window(_a(None), ep, w) is False     # sin timestamp
```
(Ajustar el `evidence` al shape real de `PatternEvidence` si el constructor lo exige — inspeccionar `contracts/pattern.py`.)

- [ ] **Step 2: Correr para verificar que falla**

Run: `.venv/bin/python -m pytest tests/test_temporal_evaluation.py -k "matching_window or in_episode_window" -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implementación** (agregar a `temporal.py`)

```python
class MatchingWindow(BaseModel):
    """Ventana de matching alerta<->episodio (spec 43 §4.1). La alerta legitima
    cae en [start_ms + persistencia_min, start_ms + t_alert_max]."""
    persistencia_min_ms: float
    t_alert_max_ms: float


# Tabla D.4 vigente (spec 43 §4.1 / §10). Parametrizable: el fixture sintetico usa
# timings comprimidos y el evaluador acepta un override por corrida/pattern set.
DEFAULT_MATCHING_WINDOWS: dict[str, MatchingWindow] = {
    "CR-01": MatchingWindow(persistencia_min_ms=3000.0, t_alert_max_ms=10000.0),
    "CR-02": MatchingWindow(persistencia_min_ms=5000.0, t_alert_max_ms=20000.0),
}


def _episode_key_matches(alert: AlertEvent, episode: ClipEpisodeV2) -> bool:
    if episode.level == "subject":
        return alert.subject_key == episode.subject_key
    return alert.source_id == episode.source_id


def _alert_in_episode_window(
    alert: AlertEvent, episode: ClipEpisodeV2, window: MatchingWindow
) -> bool:
    if alert.timestamp_ms is None:
        return False
    lo = episode.start_ms + window.persistencia_min_ms
    hi = episode.start_ms + window.t_alert_max_ms
    return lo <= alert.timestamp_ms <= hi
```

- [ ] **Step 4: Correr los tests nuevos — deben pasar**

Run: `.venv/bin/python -m pytest tests/test_temporal_evaluation.py -k "matching_window or in_episode_window" -v`
Expected: PASS (2).

- [ ] **Step 5: Suite parcial**

Run: `.venv/bin/python -m pytest tests/test_temporal_evaluation.py -k "clip_gt_v2 or matching or window or v1" -v`
Expected: verde.

- [ ] **Step 6: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_control/evaluation/temporal.py tests/test_temporal_evaluation.py
git commit -m "feat(eval): ventanas de matching en ms (Tabla D.4, parametrizable)"
```

---

## Task 3: Núcleo de evaluación v2 (episodio, `re_alerts`, sub-threshold, aplicabilidad)

Separa el dispatch por `schema_version`, deja v1 intacto, e implementa la evaluación a nivel episodio (ADR-011) con `re_alerts`, `sub_threshold`, FP, `missed`, latencia en ms, y estado de aplicabilidad (ADR-006).

**Files:**
- Modify: `src/eovrt_control/evaluation/temporal.py` (dispatch + `_evaluate_v1`/`_evaluate_v2`; nuevos campos en `TemporalAlertEvaluation`)
- Test: `tests/test_temporal_evaluation.py`

**Interfaces:**
- Consumes: Task 1 (`ClipGroundTruthV2`), Task 2 (`DEFAULT_MATCHING_WINDOWS`, `_alert_in_episode_window`, `_episode_key_matches`).
- Produces: `evaluate_temporal_alerts(alerts_path, ground_truth_path, output_path=None, matching_windows: dict[str, MatchingWindow] | None = None)`; `TemporalAlertEvaluation` gana `re_alerts_count: int = 0`, `sub_threshold_count: int = 0`, `applicability_state: str | None = None`, `applicability_cause: str | None = None`, `avg_latency_ms_from_episode_start: float | None = None`.

- [ ] **Step 1: Escribir el test que falla** (semántica episodio + re_alerts + FP + sub_threshold + aplicabilidad)

```python
def _mk_alert(alert_id, condition_id, source_id, ts, **kw):
    from eovrt_control.contracts.alerts import AlertEvent
    return AlertEvent(control_run_id="r", media_run_id="m",
                      unit_id=kw.get("unit_id", "u"), source_id=source_id,
                      alert_id=alert_id, pattern_id=condition_id, condition_id=condition_id,
                      subject_key=f"{condition_id}:{source_id}", severity="high",
                      evidence={"subject": {}, "supporting": []}, timestamp_ms=ts)


def test_v2_episode_detected_extra_alert_is_re_alert(tmp_path):
    from eovrt_control.evaluation.temporal import (
        evaluate_temporal_alerts, MatchingWindow)
    import json
    gt = {"schema_version": "clip_gt.v2", "clip_id": "c1", "duration_ms": 20000.0,
          "episodes": [{"id": "e1", "condition_id": "CR-01", "level": "scene",
                        "source_id": "s1", "start_ms": 1000.0, "end_ms": 12000.0}]}
    (tmp_path / "gt.json").write_text(json.dumps(gt))
    alerts = [_mk_alert("a1", "CR-01", "s1", 5000.0),   # dentro de ventana -> match
              _mk_alert("a2", "CR-01", "s1", 6000.0)]   # extra en el episodio -> re_alert
    with (tmp_path / "alerts.jsonl").open("w") as fh:
        for a in alerts:
            fh.write(a.model_dump_json() + "\n")
    windows = {"CR-01": MatchingWindow(persistencia_min_ms=3000.0, t_alert_max_ms=10000.0)}
    ev = evaluate_temporal_alerts(tmp_path / "alerts.jsonl", tmp_path / "gt.json",
                                  matching_windows=windows)
    assert ev.matched_alerts_count == 1        # 1 episodio detectado
    assert ev.re_alerts_count == 1             # la extra NO es FP (ADR-011)
    assert ev.unexpected_alerts_count == 0
    assert ev.missed_alerts_count == 0
    assert ev.applicability_state == "computed"


def test_v2_alert_outside_all_episodes_is_fp(tmp_path):
    from eovrt_control.evaluation.temporal import evaluate_temporal_alerts, MatchingWindow
    import json
    gt = {"schema_version": "clip_gt.v2", "clip_id": "c1", "duration_ms": 20000.0,
          "episodes": [{"id": "e1", "condition_id": "CR-01", "level": "scene",
                        "source_id": "s1", "start_ms": 1000.0, "end_ms": 12000.0}]}
    (tmp_path / "gt.json").write_text(json.dumps(gt))
    with (tmp_path / "alerts.jsonl").open("w") as fh:
        fh.write(_mk_alert("a1", "CR-01", "s1", 5000.0).model_dump_json() + "\n")  # match
        fh.write(_mk_alert("a2", "CR-01", "s1", 18000.0).model_dump_json() + "\n") # fuera -> FP
    windows = {"CR-01": MatchingWindow(persistencia_min_ms=3000.0, t_alert_max_ms=10000.0)}
    ev = evaluate_temporal_alerts(tmp_path / "alerts.jsonl", tmp_path / "gt.json",
                                  matching_windows=windows)
    assert ev.matched_alerts_count == 1 and ev.unexpected_alerts_count == 1
    assert ev.re_alerts_count == 0


def test_v2_alert_in_sub_threshold_is_not_fp(tmp_path):
    from eovrt_control.evaluation.temporal import evaluate_temporal_alerts, MatchingWindow
    import json
    gt = {"schema_version": "clip_gt.v2", "clip_id": "c1", "duration_ms": 20000.0,
          "episodes": [],  # sin episodios alertables
          "negative": False,
          "sub_threshold_events": [{"condition_id": "CR-01", "start_ms": 4000.0,
                                    "end_ms": 7000.0, "reason": "transitorio"}]}
    (tmp_path / "gt.json").write_text(json.dumps(gt))
    with (tmp_path / "alerts.jsonl").open("w") as fh:
        fh.write(_mk_alert("a1", "CR-01", "s1", 5000.0).model_dump_json() + "\n")  # sub-threshold
    ev = evaluate_temporal_alerts(tmp_path / "alerts.jsonl", tmp_path / "gt.json",
                                  matching_windows={"CR-01": MatchingWindow(3000.0, 10000.0)})
    assert ev.sub_threshold_count == 1
    assert ev.unexpected_alerts_count == 0   # sub-threshold NO es FP verdadero


def test_v2_non_temporal_alerts_are_not_applicable(tmp_path):
    from eovrt_control.evaluation.temporal import evaluate_temporal_alerts
    from eovrt_control.contracts.alerts import AlertEvent
    import json
    gt = {"schema_version": "clip_gt.v2", "clip_id": "c1", "duration_ms": 20000.0,
          "episodes": [{"id": "e1", "condition_id": "CR-01", "level": "scene",
                        "source_id": "s1", "start_ms": 1000.0, "end_ms": 12000.0}]}
    (tmp_path / "gt.json").write_text(json.dumps(gt))
    a = AlertEvent(control_run_id="r", media_run_id="m", unit_id="u", source_id="s1",
                   alert_id="a", pattern_id="CR-01", condition_id="CR-01",
                   subject_key="CR-01:s1", severity="high",
                   evidence={"subject": {}, "supporting": []}, timestamp_ms=None)
    (tmp_path / "alerts.jsonl").write_text(a.model_dump_json() + "\n")
    ev = evaluate_temporal_alerts(tmp_path / "alerts.jsonl", tmp_path / "gt.json")
    assert ev.applicability_state == "not_applicable"
    assert ev.applicability_cause == "non_temporal_source"
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `.venv/bin/python -m pytest tests/test_temporal_evaluation.py -k "v2_" -v`
Expected: FAIL — `evaluate_temporal_alerts() got an unexpected keyword argument 'matching_windows'` / faltan campos `re_alerts_count`.

- [ ] **Step 3: Implementación**

En `TemporalAlertEvaluation` agregar (aditivo, conservar `schema_version`):
```python
    re_alerts_count: int = 0
    sub_threshold_count: int = 0
    applicability_state: str | None = None
    applicability_cause: str | None = None
    avg_latency_ms_from_episode_start: float | None = None
```
Renombrar el cuerpo actual de `evaluate_temporal_alerts` a `_evaluate_v1(alerts, ground_truth, alerts_path, ground_truth_path) -> TemporalAlertEvaluation` (mismo código, ahora recibe los objetos ya cargados; setea `applicability_state="computed"`, `applicability_cause=None`). Reescribir `_load_ground_truth` para NO aplanar: devolver `TemporalGroundTruth | ClipGroundTruthV2` según `schema_version`. Nuevo dispatch:

```python
def evaluate_temporal_alerts(alerts_path, ground_truth_path, output_path=None,
                             matching_windows=None):
    alerts_path = Path(alerts_path)
    ground_truth_path = Path(ground_truth_path)
    ground_truth = _load_ground_truth(ground_truth_path)
    alerts = _load_alerts(alerts_path)
    if isinstance(ground_truth, ClipGroundTruthV2):
        evaluation = _evaluate_v2(alerts, ground_truth, alerts_path, ground_truth_path,
                                  matching_windows or DEFAULT_MATCHING_WINDOWS)
    else:
        evaluation = _evaluate_v1(alerts, ground_truth, alerts_path, ground_truth_path)
    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(evaluation.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return evaluation
```

`_evaluate_v2` (nuevo): aplicabilidad primero (si TODAS las alertas tienen `timestamp_ms is None` y hay alertas ⇒ `not_applicable/non_temporal_source`, contar 0 matches). Luego, por episodio: candidatos = alertas con `condition_id` igual, `_episode_key_matches`, y `_alert_in_episode_window` (ventana de `matching_windows[condition_id]`; si falta la condición, usar `DEFAULT_MATCHING_WINDOWS` o marcar warning). El primer candidato (por `timestamp_ms`) es el match; los demás dentro de la ventana del MISMO episodio son `re_alerts`. Latencia = `alert.timestamp_ms - episode.start_ms`. Alertas no consumidas: si caen en algún `sub_threshold_event` (condición + ventana `[start_ms, end_ms]`) ⇒ `sub_threshold` (no FP); si no ⇒ `unexpected` (FP). `missed` = episodio sin ningún candidato. `precision = matched / (matched + unexpected)` (los re_alerts y sub_threshold NO entran al denominador de precisión como FP); `recall = matched / len(episodes)`; F1 estándar. `applicability_state="computed"`.

- [ ] **Step 4: Correr los tests nuevos — deben pasar**

Run: `.venv/bin/python -m pytest tests/test_temporal_evaluation.py -k "v2_" -v`
Expected: PASS (4).

- [ ] **Step 5: Suite completa de temporal (v1 debe seguir verde)**

Run: `.venv/bin/python -m pytest tests/test_temporal_evaluation.py -v`
Expected: los v1 verdes + los v2 nuevos. Si algún test viejo referenciaba `ClipEpisode` frame-based, se migra en Task 4 (aún puede fallar aquí — anotarlo).

- [ ] **Step 6: Commit (sólo si el usuario lo pidió)**

```bash
git add src/eovrt_control/evaluation/temporal.py tests/test_temporal_evaluation.py
git commit -m "feat(eval): evaluacion v2 por episodio con re_alerts y aplicabilidad"
```

---

## Task 4: Migrar fixtures a ms + fixture de bandas default; v1 verde

Migra los dos `ground_truth_v2.json` al schema ms y actualiza los tests que los usan (preservando el intento: el motor detecta los episodios correctos ⇒ recall 1.0). Agrega un fixture nuevo que ejercita las bandas default + re_alerts + sub_threshold + FP.

**Files:**
- Modify: `fixtures/simulated_media/cr01_cr02_temporal/ground_truth_v2.json`, `fixtures/simulated_media/cr01_cr02_temporal_tracked/ground_truth_v2.json`
- Create: `fixtures/simulated_media/cr01_cr02_temporal/ground_truth_v2_bands.json` (opcional, para bandas default)
- Modify: `tests/test_temporal_evaluation.py` (tests que consumían la `ClipEpisode` frame-based)

**Interfaces:**
- Consumes: Task 3 (`evaluate_temporal_alerts` con `matching_windows`).

- [ ] **Step 1: Migrar los fixtures al schema ms**

`cr01_cr02_temporal/ground_truth_v2.json` (12 units, 500 ms/frame; CR-01 primera evidencia frame 2 = 1000 ms, alerta real frame 4 = 2000 ms; CR-02 primera evidencia frame 2 = 1000 ms, alerta frame 5 = 2500 ms):

```json
{
  "schema_version": "clip_gt.v2",
  "clip_id": "cr01_cr02_temporal",
  "source_file": "detections.jsonl",
  "duration_ms": 6000.0,
  "negative": false,
  "description": "Episodios escena-condicion (G0), tiempos en ms. Sinteticos: ventanas comprimidas respecto de Tabla D.4 (ver test).",
  "episodes": [
    {"id": "ep_cr01", "condition_id": "CR-01", "level": "scene", "source_id": "sim-risk-stream",
     "start_ms": 1000.0, "end_ms": 5500.0, "subjects_in_evidence": 1},
    {"id": "ep_cr02", "condition_id": "CR-02", "level": "scene", "source_id": "sim-risk-stream",
     "start_ms": 1000.0, "end_ms": 5500.0, "subjects_in_evidence": 1}
  ],
  "sub_threshold_events": []
}
```
Análogo para `cr01_cr02_temporal_tracked/ground_truth_v2.json` (subject level; `start_ms` = primera evidencia de cada worker: worker_a 1500 ms, worker_c 2500 ms; `subject_key` como en el original).

- [ ] **Step 2: Actualizar los tests que usaban la forma frame-based**

Los tests `test_scene_granularity_matches_expected_alerts_with_f1_one` y `test_subject_granularity_matches_expected_alerts_with_f1_one` corren replay real → evaluate. Actualizar la aserción para pasar `matching_windows` sintéticos y verificar detección por episodio. Como las alertas del replay caen ~2000/2500 ms y `start_ms=1000/2500`, usar ventanas que las contengan:

```python
def test_scene_granularity_matches_expected_episodes_ms(tmp_path):
    # ... corre el replay como antes, obtiene alerts_path ...
    from eovrt_control.evaluation.temporal import MatchingWindow
    windows = {"CR-01": MatchingWindow(500.0, 3000.0), "CR-02": MatchingWindow(500.0, 3000.0)}
    ev = evaluate_temporal_alerts(alerts_path, gt_v2_path, matching_windows=windows)
    assert ev.matched_alerts_count == 2      # ambos episodios detectados
    assert ev.missed_alerts_count == 0
    assert ev.unexpected_alerts_count == 0
    assert ev.recall == 1.0
    assert ev.applicability_state == "computed"
```
(Preservar los tests v1 y de validación de granularidad tal cual; sólo migran los dos que consumían la `ClipEpisode` frame-based.)

- [ ] **Step 3: Fixture de bandas default (nuevo) + su test**

`ground_truth_v2_bands.json`: un episodio CR-01 con `start_ms` tal que una alerta sintética a `start_ms + 4000` caiga dentro de `[start_ms+3000, start_ms+10000]`, más una `re_alert`, una FP fuera de ventana y un `sub_threshold_event`. El test construye el `alerts.jsonl` a mano (no requiere replay) y evalúa con los defaults (`matching_windows=None`), verificando `matched=1, re_alerts=1, unexpected=1, sub_threshold=1`.

- [ ] **Step 4: Suite completa de temporal**

Run: `.venv/bin/python -m pytest tests/test_temporal_evaluation.py -v`
Expected: TODO verde (v1 + v2 + migrados + bandas).

- [ ] **Step 5: Suite completa del repo**

Run: `.venv/bin/python -m pytest -q --ignore=tests/labs`
Expected: 177 + (tests nuevos netos) passed, 0 fallos. Reportar el número exacto.

- [ ] **Step 6: Commit (sólo si el usuario lo pidió)**

```bash
git add fixtures/simulated_media tests/test_temporal_evaluation.py
git commit -m "test(eval): fixtures clip_gt.v2 en ms + bandas default"
```

---

## Task 5: **Gate** — v1 verde + v2 escena-condición + verificación por mutación

El gate de fase (spec 41 §8 item 7 / §10 gate 7): "fixture v1 sigue en verde + fixture v2 escena-condición en verde". Verificado significativo por mutación.

**Files:**
- Test: `tests/test_evaluate_alerts_v2_gate.py` (nuevo)

- [ ] **Step 1: Escribir el gate**

Un test que corre AMBOS: (a) una evaluación v1 (`TemporalGroundTruth` / `control.eval.temporal.v1`) que debe dar el mismo resultado que antes (F1 = 1.0 sobre su fixture); (b) una evaluación v2 escena-condición (`clip_gt.v2` ms) que detecta sus episodios (recall 1.0) y cuenta correctamente `re_alerts`/FP. Aserciones concretas sobre ambos.

- [ ] **Step 2: Correr el gate**

Run: `.venv/bin/python -m pytest tests/test_evaluate_alerts_v2_gate.py -v`
Expected: PASS.

- [ ] **Step 3: Verificar que el gate es significativo (mutación) — obligatorio**

Un gate que no puede fallar no es un gate. Dos mutaciones, una por vez, reportar la salida literal:
1. En `_evaluate_v2`, contar las alertas extra del mismo episodio como `unexpected` (FP) en vez de `re_alerts`. Esperado: **falla** la aserción `re_alerts_count`/`unexpected_alerts_count` del gate v2 (ADR-011 roto). **Revertí.**
2. En `_alert_in_episode_window`, sacar el borde inferior (`return alert.timestamp_ms <= hi`). Esperado: una alerta pre-persistencia matchearía y **falla** el gate (o un test v2 de Task 3). **Revertí.**
Si alguna mutación NO hace fallar el gate, es vacuo: arreglalo antes de seguir.

- [ ] **Step 4: Suite completa + lint (final)**

Run: `.venv/bin/python -m pytest -q --ignore=tests/labs && .venv/bin/python -m ruff check src tests`
Expected: verde total; `All checks passed!`.

- [ ] **Step 5: Commit (sólo si el usuario lo pidió)**

```bash
git add tests/test_evaluate_alerts_v2_gate.py
git commit -m "test(gate): evaluate-alerts v1+v2 verificado por mutacion"
```

---

## Cierre

- [ ] **Registrar la deuda:** los números REALES (P/R/F1, TTFD, SDR, `t_alert-system`) siguen **diferidos** — necesitan el dataset de clips con GT (`clip_gt.v2` real, spec 43, bloqueado por grabación+anotación+consentimiento). `evaluate-alerts` v2 queda **listo para su GT** (spec 41 §8 item 7). El `validate_clip_gt.py` (validador de GT) es un deliverable del repo `e-ovrt_datasets`, no de acá.
- [ ] **Escribir el doc de resultados** en `docs/operacion/` (siguiente número de la serie 50-) con: qué se construyó, la semántica episodio/re_alerts/sub_threshold/aplicabilidad, los defectos que atrapó la revisión, y la deuda. Banner **EJECUTADO** en este plan apuntando al doc.
- [ ] **Verificación final:** pegar la salida de `pytest -q --ignore=tests/labs` y `ruff check`, y la salida literal de las dos mutaciones del gate.

## Alineación con el informe (self-review de cobertura)

| Requisito spec 41 §8 item 7 / spec 43 §4.1 | Task |
|---|---|
| Consume `clip_gt.v2` (episodios escena-condición) | 1 |
| Matching por ventana en ms `[start_ms + persistencia_min, start_ms + t_alert_max]` | 2 |
| Episodio detectado si ≥1 alerta; `re_alerts` extra NO son FP (ADR-011) | 3 |
| `sub_threshold_events` distinguidos de FP verdadero | 3, 4 |
| FP = alerta fuera de todo episodio | 3 |
| Estados de aplicabilidad + causa (ADR-006) | 3 |
| Preservar el path v1 (fixture v1 en verde) | 3, 5 |
| Gate: v1 verde + v2 escena-condición verde | 5 |
| Números reales sobre clips | **diferido** (dataset con GT, spec 43) |
