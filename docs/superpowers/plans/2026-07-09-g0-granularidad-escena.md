# G0 — Granularidad de escena en el motor de patrones

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que la clave de estado del motor sea la escena (`pattern_id`, `source_id`) en vez de un `detection_id` aliasado, de modo que las condiciones se confirmen correctamente sobre video y RTSP.

**Architecture:** Se agrega `granularity: scene | subject` a `PatternDefinition` (default `scene`). El evaluador espacial deja de emitir una evidencia por persona y pasa a emitir evidencias **por clave de estado**: bajo `scene`, a lo sumo una por patrón-fuente, agregando "≥1 sujeto sin EPP"; bajo `subject`, una por `track_id`. El bucle del `PatternEngine` no cambia de forma — sigue iterando `observed_subject_keys` y `evidence_by_subject` — porque lo que cambia es qué string es una clave. `detection_id` deja de usarse como identidad. La memoria de cobertura queda restringida a `subject` (ADR-012).

**Tech Stack:** Python 3.11, Pydantic v2, pytest, Typer. Repo `e-ovrt_control-plane`, rama `feature/control-service`.

## Global Constraints

- **Contratos siempre aditivos**: campos nuevos son opcionales con default. Sin bump de `schema_version` (spec 40 §1).
- **`detection_id` deja de usarse como identidad, siempre** (spec 41 §2.1).
- **Gate de merge del motor**: `F1 = 1.0` en ambas granularidades sobre el fixture temporal (spec 41 §2.2).
- **La memoria de cobertura no se aplica bajo `scene`**; se ignora y se declara la causa `coverage_memory_unsupported_scene` (ADR-012).
- **El cooldown permanece en el motor como capacidad no usada** (ADR-011 §3). No se elimina ni se modifica `_cooldown_ok`.
- **No commitear sin pedido explícito del usuario.** Los pasos "Commit" de este plan quedan pendientes de autorización; agrupar y pedirla al cerrar cada tarea.
- Correr tests con `.venv/bin/python -m pytest -q --ignore=tests/labs` (los de `labs` fallan por `numpy`, falla conocida no bloqueante).

---

### Task 1: Campos de contrato aditivos (`granularity`, `track_id`)

**Files:**
- Modify: `src/eovrt_control/config.py:59-70` (`PatternDefinition`)
- Modify: `src/eovrt_control/contracts/media.py:11-29` (`Detection`)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nada.
- Produces: `PatternDefinition.granularity: Literal["scene","subject"]` (default `"scene"`); `Detection.track_id: str | None` (default `None`). Todas las tareas siguientes los usan.

- [ ] **Step 1: Escribir el test que falla**

En `tests/test_config.py`, agregar:

```python
from eovrt_control.config import PatternDefinition, PatternRegionConfig
from eovrt_control.contracts.media import Detection


def _pattern(**overrides) -> PatternDefinition:
    base = dict(
        id="CR-01",
        name="person_without_helmet",
        condition_id="CR-01",
        required_absent_class="helmet",
        region=PatternRegionConfig(type="upper_body"),
    )
    base.update(overrides)
    return PatternDefinition(**base)


def test_pattern_granularity_defaults_to_scene() -> None:
    assert _pattern().granularity == "scene"


def test_pattern_granularity_accepts_subject() -> None:
    assert _pattern(granularity="subject").granularity == "subject"


def test_pattern_granularity_rejects_unknown_value() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _pattern(granularity="camera")


def test_detection_track_id_is_optional_and_defaults_to_none() -> None:
    detection = Detection(label="person", confidence=0.9, bbox_xyxy=[0, 0, 10, 20])
    assert detection.track_id is None


def test_detection_accepts_track_id() -> None:
    detection = Detection(
        label="person", confidence=0.9, bbox_xyxy=[0, 0, 10, 20], track_id="subject_001"
    )
    assert detection.track_id == "subject_001"
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: FAIL — `PatternDefinition` no acepta `granularity`; `Detection` no tiene `track_id`.

- [ ] **Step 3: Implementación mínima**

En `src/eovrt_control/config.py`, agregar el import y el campo:

```python
from typing import Literal
```

```python
class PatternDefinition(BaseModel):
    id: str
    name: str
    description: str | None = None
    enabled: bool = True
    condition_id: str
    severity: str = "medium"
    subject_class: str = "person"
    required_absent_class: str
    # G0 (ADR-002): la escena es el nucleo. `subject` exige track_id y es demostrativa.
    granularity: Literal["scene", "subject"] = "scene"
    region: PatternRegionConfig
    evidence: PatternEvidenceConfig = Field(default_factory=PatternEvidenceConfig)
    timing: PatternTimingConfig = Field(default_factory=PatternTimingConfig)
```

En `src/eovrt_control/contracts/media.py`, dentro de `Detection`, agregar tras `detection_id`:

```python
    # Aditivo (spec 40 §1): identidad de sujeto emitida por el tracker del
    # media-plane. Unica identidad valida entre frames; detection_id NO lo es.
    track_id: str | None = None
```

- [ ] **Step 4: Correr el test para verificar que pasa**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Verificar que no hubo regresión**

Run: `.venv/bin/python -m pytest -q --ignore=tests/labs`
Expected: PASS — 18 previos + 5 nuevos = 23.

- [ ] **Step 6: Commit** (pedir autorización primero)

```bash
git add src/eovrt_control/config.py src/eovrt_control/contracts/media.py tests/test_config.py
git commit -m "feat(contracts): granularity por patron y track_id opcional en Detection"
```

---

### Task 2: Clave de estado por granularidad en el evaluador espacial

**Files:**
- Modify: `src/eovrt_control/engine/evaluators/spatial_absence.py:139-196`
- Modify: `src/eovrt_control/contracts/pattern.py:15-24` (`PatternEvidence`)
- Modify: `src/eovrt_control/engine/pattern_engine.py:56` (conteo de sujetos)
- Test: `tests/test_spatial_absence.py`

**Interfaces:**
- Consumes: `PatternDefinition.granularity`, `Detection.track_id` (Task 1).
- Produces:
  - `state_key(pattern, source_id, track_id: str | None) -> str` — `"{pattern_id}:{source_id}"` bajo scene; `"{pattern_id}:{source_id}:{track_id}"` bajo subject.
  - `PatternEvaluationResult.evidences: list[PatternEvidence]` con **una evidencia por clave de estado** (bajo scene: 0 o 1). La evidencia de escena lleva a los sujetos descubiertos no-representantes en `supporting` (los necesita la evaluación por persona del BENCH, que junta por bbox).
  - `PatternEvaluationResult.observed_subject_keys: set[str]` — claves de estado observadas.
  - `PatternEvaluationResult.subjects_observed: int` — cantidad real de personas que pasaron los filtros (independiente de la granularidad; alimenta `ControlMetricSample.subjects_count`).
  - `PatternEvidence.subjects_in_evidence: int | None` — cuántos sujetos aportan evidencia en esta unidad.
  - `PatternEvaluationResult.degradation_causes: set[str]` — vacío en esta tarea; lo puebla Task 3.

- [ ] **Step 1: Escribir los tests que fallan**

En `tests/test_spatial_absence.py`, agregar (los helpers `_event`/`_detection` ya existen en el archivo; reusarlos):

```python
def test_scene_granularity_collapses_subjects_into_one_state_key() -> None:
    """Dos personas sin casco en la misma escena => una sola evidencia de escena."""
    pattern = _helmet_pattern(granularity="scene")
    event = _event(
        source_id="cam-1",
        detections=[
            _detection("person", 0.9, [0, 0, 100, 200]),
            _detection("person", 0.9, [300, 0, 400, 200]),
        ],
    )

    result = evaluate_spatial_absence(event, pattern)

    assert result.observed_subject_keys == {"CR-01:cam-1"}
    assert len(result.evidences) == 1
    assert result.evidences[0].subject_key == "CR-01:cam-1"
    assert result.evidences[0].subjects_in_evidence == 2
    # El sujeto no representante queda auditable en supporting.
    assert len(result.evidences[0].supporting) == 1
    assert result.subjects_observed == 2


def test_scene_in_evidence_when_at_least_one_subject_uncovered() -> None:
    """Una persona con casco y otra sin casco => la escena esta en evidencia."""
    pattern = _helmet_pattern(granularity="scene")
    event = _event(
        source_id="cam-1",
        detections=[
            _detection("person", 0.9, [0, 0, 100, 200]),
            _detection("helmet", 0.8, [40, 10, 60, 30]),
            _detection("person", 0.9, [300, 0, 400, 200]),
        ],
    )

    result = evaluate_spatial_absence(event, pattern)

    assert len(result.evidences) == 1
    assert result.evidences[0].subjects_in_evidence == 1


def test_scene_not_in_evidence_when_every_subject_covered() -> None:
    pattern = _helmet_pattern(granularity="scene")
    event = _event(
        source_id="cam-1",
        detections=[
            _detection("person", 0.9, [0, 0, 100, 200]),
            _detection("helmet", 0.8, [40, 10, 60, 30]),
        ],
    )

    result = evaluate_spatial_absence(event, pattern)

    assert result.observed_subject_keys == {"CR-01:cam-1"}
    assert result.evidences == []


def test_scene_key_absent_when_no_subjects_observed() -> None:
    pattern = _helmet_pattern(granularity="scene")
    event = _event(source_id="cam-1", detections=[_detection("helmet", 0.8, [0, 0, 10, 10])])

    result = evaluate_spatial_absence(event, pattern)

    assert result.observed_subject_keys == set()
    assert result.evidences == []


def test_subject_granularity_keys_by_track_id() -> None:
    pattern = _helmet_pattern(granularity="subject")
    event = _event(
        source_id="cam-1",
        detections=[
            _detection("person", 0.9, [0, 0, 100, 200], track_id="subject_001"),
            _detection("person", 0.9, [300, 0, 400, 200], track_id="subject_002"),
        ],
    )

    result = evaluate_spatial_absence(event, pattern)

    assert result.observed_subject_keys == {
        "CR-01:cam-1:subject_001",
        "CR-01:cam-1:subject_002",
    }
    assert {e.subject_key for e in result.evidences} == result.observed_subject_keys
    assert all(e.subjects_in_evidence == 1 for e in result.evidences)


def test_detection_id_is_never_used_as_identity() -> None:
    """Mismo track_id, distinto detection_id => misma clave de estado."""
    pattern = _helmet_pattern(granularity="subject")
    event_a = _event(
        source_id="cam-1",
        detections=[_detection("person", 0.9, [0, 0, 100, 200], track_id="subject_001", detection_id="det_000001")],
    )
    event_b = _event(
        source_id="cam-1",
        detections=[_detection("person", 0.9, [0, 0, 100, 200], track_id="subject_001", detection_id="det_000042")],
    )

    keys_a = evaluate_spatial_absence(event_a, pattern).observed_subject_keys
    keys_b = evaluate_spatial_absence(event_b, pattern).observed_subject_keys

    assert keys_a == keys_b == {"CR-01:cam-1:subject_001"}
```

Agregar al principio del archivo el helper de patrón, si no existe:

```python
def _helmet_pattern(**overrides) -> PatternDefinition:
    base = dict(
        id="CR-01",
        name="person_without_helmet",
        condition_id="CR-01",
        required_absent_class="helmet",
        region=PatternRegionConfig(type="upper_body", y_min_ratio=0.0, y_max_ratio=0.45, x_margin_ratio=0.12),
    )
    base.update(overrides)
    return PatternDefinition(**base)
```

Y extender el helper `_detection` existente para aceptar `track_id` y `detection_id` opcionales.

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `.venv/bin/python -m pytest tests/test_spatial_absence.py -q`
Expected: FAIL — hoy `observed_subject_keys` contiene `CR-01:cam-1:det_...` o `CR-01:cam-1:<unit>:person:<i>`, y `PatternEvidence` no tiene `subjects_in_evidence`.

- [ ] **Step 3: Implementación**

En `src/eovrt_control/contracts/pattern.py`, agregar a `PatternEvidence` (aditivo):

```python
class PatternEvidence(BaseModel):
    pattern_id: str
    condition_id: str
    subject_key: str
    subject: EvidenceRef
    missing_class: str
    supporting: list[EvidenceRef] = Field(default_factory=list)
    score: float
    rationale: str
    # G0 (spec 41 §2.1): cuantos sujetos aportan evidencia en esta unidad.
    # Bajo `subject` siempre 1. Insumo del GT clip_gt.v2.
    subjects_in_evidence: int | None = None
```

En `src/eovrt_control/engine/evaluators/spatial_absence.py`, reemplazar `_subject_key` (líneas 139-141) y `evaluate_spatial_absence` (líneas 144-196) por:

```python
def state_key(pattern: PatternDefinition, source_id: str, track_id: str | None) -> str:
    """Clave de estado del motor (spec 41 §2.1).

    Bajo `scene` la identidad es la fuente. Bajo `subject` es el track_id.
    `detection_id` NO se usa como identidad, nunca.
    """
    if pattern.granularity == "subject" and track_id is not None:
        return f"{pattern.id}:{source_id}:{track_id}"
    return f"{pattern.id}:{source_id}"


def evaluate_spatial_absence(
    event: DetectionEvent,
    pattern: PatternDefinition,
) -> PatternEvaluationResult:
    """Evalua si cada persona carece de una clase EPP asociada espacialmente.

    Emite evidencias por CLAVE DE ESTADO, no por persona: bajo `scene` agrega los
    sujetos en una unica evidencia ("la escena esta en evidencia si >=1 sujeto la
    aporta", spec 41 §2.1); bajo `subject` emite una por track_id.
    """

    subjects = [
        detection
        for detection in event.detections
        if _matches_detection(detection, pattern.subject_class)
        and detection.confidence >= pattern.evidence.min_subject_confidence
        and (detection.area_px or 0.0) >= pattern.evidence.min_subject_area_px
    ]
    required_items = [
        detection
        for detection in event.detections
        if _matches_detection(detection, pattern.required_absent_class)
        and detection.confidence >= pattern.evidence.min_absent_class_confidence
    ]

    regions = [_region_bbox(subject.bbox_xyxy, pattern) for subject in subjects]
    covered = _match_epp_to_subjects(regions, required_items)

    source_id = event.source.source_id
    observed_subject_keys: set[str] = set()
    # clave de estado -> sujetos descubiertos que la respaldan
    uncovered_by_key: dict[str, list[Detection]] = {}

    for index, subject in enumerate(subjects):
        key = state_key(pattern, source_id, subject.track_id)
        observed_subject_keys.add(key)
        if index in covered:
            continue
        uncovered_by_key.setdefault(key, []).append(subject)

    evidences: list[PatternEvidence] = []
    for key, uncovered in uncovered_by_key.items():
        # Representante: el sujeto mas confiable. Bajo `subject` hay exactamente uno.
        representative = max(uncovered, key=lambda detection: detection.confidence)
        evidences.append(
            PatternEvidence(
                pattern_id=pattern.id,
                condition_id=pattern.condition_id,
                subject_key=key,
                subject=_evidence_ref(representative),
                missing_class=pattern.required_absent_class,
                # Los demas sujetos descubiertos NO se pierden al agregar a escena:
                # la evaluacion por persona del BENCH (person_gt) junta por bbox.
                supporting=[
                    _evidence_ref(subject)
                    for subject in uncovered
                    if subject is not representative
                ],
                score=representative.confidence,
                subjects_in_evidence=len(uncovered),
                rationale=(
                    f"No se encontro evidencia '{pattern.required_absent_class}' "
                    f"en region '{pattern.region.type}' de {len(uncovered)} sujeto(s)."
                ),
            )
        )

    return PatternEvaluationResult(
        evidences=evidences,
        observed_subject_keys=observed_subject_keys,
        subjects_observed=len(subjects),
        degradation_causes=set(),
    )
```

En `src/eovrt_control/engine/pattern_engine.py:56`, cambiar el conteo de sujetos:

```python
            subjects_count += result.subjects_observed
```

Sin esto, `ControlMetricSample.subjects_count` pasaría a contar **escenas** (≤1 por
patrón) en vez de personas, rompiendo en silencio la comparabilidad con las corridas
previas (doc 33: 138 sujetos sobre el BENCH). Nota asumida y declarada: `evidences_count`
sí cambia de significado (evidencias por clave de estado, no por persona) — el conteo
por persona vive ahora en `subjects_in_evidence`.

Agregar el campo a `PatternEvaluationResult` (`spatial_absence.py:24-28`, es un `@dataclass(frozen=True)`):

```python
@dataclass(frozen=True)
class PatternEvaluationResult:
    evidences: list[PatternEvidence]
    observed_subject_keys: set[str]
    subjects_observed: int = 0
    degradation_causes: set[str] = field(default_factory=set)
```

(importar `field` de `dataclasses`; hoy el archivo solo importa `dataclass`).

- [ ] **Step 4: Correr los tests para verificar que pasan**

Run: `.venv/bin/python -m pytest tests/test_spatial_absence.py -q`
Expected: PASS. Los 5 tests preexistentes de `test_spatial_absence.py` siguen verdes: no dependían de la forma del `subject_key`, solo de si hay o no evidencia — salvo el de "EPP compartido cubre solo a la persona más cercana", que ahora observa una sola clave de escena. Si ese test asertaba dos `subject_key`, actualizarlo para asertar `subjects_in_evidence == 1` sobre la clave de escena.

- [ ] **Step 5: Correr la suite completa (se espera rojo, y es informativo)**

Run: `.venv/bin/python -m pytest -q --ignore=tests/labs`
Expected: FAIL en `tests/test_temporal_evaluation.py` (el GT del fixture espera `CR-01:sim-risk-stream:worker_a`, que ya no es una clave). Ese rojo lo cierra la Task 7. Anotarlo y seguir; **no** tocar el fixture acá.

- [ ] **Step 6: Commit** (pedir autorización primero)

```bash
git add src/eovrt_control/engine/evaluators/spatial_absence.py src/eovrt_control/contracts/pattern.py tests/test_spatial_absence.py
git commit -m "feat(engine): clave de estado por granularidad; escena agrega sujetos"
```

---

### Task 3: Fallback G1 → G0 sin `track_id`, con causa `no_track_id`

**Files:**
- Modify: `src/eovrt_control/engine/evaluators/spatial_absence.py` (`evaluate_spatial_absence`)
- Test: `tests/test_spatial_absence.py`

**Interfaces:**
- Consumes: `state_key`, `PatternEvaluationResult.degradation_causes` (Task 2).
- Produces: `degradation_causes` contiene `"no_track_id"` cuando un patrón `subject` recibe sujetos sin `track_id`. La clave cae a la de escena.

- [ ] **Step 1: Escribir el test que falla**

```python
def test_subject_granularity_without_track_id_falls_back_to_scene() -> None:
    pattern = _helmet_pattern(granularity="subject")
    event = _event(source_id="cam-1", detections=[_detection("person", 0.9, [0, 0, 100, 200])])

    result = evaluate_spatial_absence(event, pattern)

    assert result.observed_subject_keys == {"CR-01:cam-1"}
    assert result.degradation_causes == {"no_track_id"}


def test_subject_granularity_with_track_id_reports_no_degradation() -> None:
    pattern = _helmet_pattern(granularity="subject")
    event = _event(
        source_id="cam-1",
        detections=[_detection("person", 0.9, [0, 0, 100, 200], track_id="subject_001")],
    )

    result = evaluate_spatial_absence(event, pattern)

    assert result.degradation_causes == set()


def test_scene_granularity_without_track_id_is_not_a_degradation() -> None:
    pattern = _helmet_pattern(granularity="scene")
    event = _event(source_id="cam-1", detections=[_detection("person", 0.9, [0, 0, 100, 200])])

    assert evaluate_spatial_absence(event, pattern).degradation_causes == set()


def test_mixed_track_ids_degrade_whole_event_to_scene() -> None:
    """El fallback es por FUENTE (spec 41 §2.1: "el motor cae a G0 para esa
    fuente"), no por deteccion: claves de sujeto y de escena conviviendo para el
    mismo patron-fuente partirian el estado."""
    pattern = _helmet_pattern(granularity="subject")
    event = _event(
        source_id="cam-1",
        detections=[
            _detection("person", 0.9, [0, 0, 100, 200], track_id="subject_001"),
            _detection("person", 0.9, [300, 0, 400, 200]),
        ],
    )

    result = evaluate_spatial_absence(event, pattern)

    assert result.observed_subject_keys == {"CR-01:cam-1"}
    assert result.degradation_causes == {"no_track_id"}
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `.venv/bin/python -m pytest tests/test_spatial_absence.py -k track_id -q`
Expected: FAIL — `degradation_causes` siempre vacío.

- [ ] **Step 3: Implementación**

En `evaluate_spatial_absence`, reemplazar el bucle de sujetos por:

```python
    degradation_causes: set[str] = set()
    # Fallback por FUENTE (spec 41 §2.1), no por deteccion: si una sola persona
    # viene sin track_id, todo el evento de esta fuente se clavea a escena.
    fallback_to_scene = pattern.granularity == "subject" and any(
        subject.track_id is None for subject in subjects
    )
    if fallback_to_scene:
        degradation_causes.add("no_track_id")

    for index, subject in enumerate(subjects):
        track_id = None if fallback_to_scene else subject.track_id
        key = state_key(pattern, source_id, track_id)
        observed_subject_keys.add(key)
        if index in covered:
            continue
        uncovered_by_key.setdefault(key, []).append(subject)
```

y devolver `degradation_causes=degradation_causes` en el `PatternEvaluationResult`.

`state_key` ya cae a la clave de escena cuando `track_id is None` — no hay que tocarla.

**Limitación asumida:** si el tracker parpadea entre eventos (un evento con tracks, el
siguiente sin), la clave alterna entre el espacio de sujeto y el de escena, y ambos
acumulan estado por separado. Aceptado: G1 es demostrativa (ADR-002), la degradación
queda declarada por la causa, y un fallback pegajoso por corrida es refinamiento futuro
si el caso aparece en la práctica.

- [ ] **Step 4: Correr para verificar que pasa**

Run: `.venv/bin/python -m pytest tests/test_spatial_absence.py -q`
Expected: PASS.

- [ ] **Step 5: Commit** (pedir autorización primero)

```bash
git add src/eovrt_control/engine/evaluators/spatial_absence.py tests/test_spatial_absence.py
git commit -m "feat(engine): fallback G1->G0 con causa no_track_id"
```

---

### Task 4: Memoria de cobertura solo bajo `subject` (ADR-012) + test de falsación

**Files:**
- Modify: `src/eovrt_control/engine/pattern_engine.py:58-61` (`memory_enabled`), `:201-225` (`_memory_covers`)
- Test: `tests/test_pattern_engine.py`

**Interfaces:**
- Consumes: `PatternDefinition.granularity` (Task 1).
- Produces: `PatternEngine` ignora `coverage_memory_*` bajo `scene` y agrega `"coverage_memory_unsupported_scene"` a las causas de degradación de su resultado.

Este es el test que **falsa el ADR-012** si la decisión estuvo mal.

- [ ] **Step 1: Escribir los tests que fallan**

```python
def test_coverage_memory_is_ignored_under_scene_granularity() -> None:
    """ADR-012: sin identidad de sujeto no hay a que colgar la memoria."""
    pattern = _helmet_pattern(
        granularity="scene",
        timing=PatternTimingConfig(confirm_after_frames=1, coverage_memory_frames=5),
    )
    engine = PatternEngine("run-1", [pattern])

    # f0: persona cubierta -> la escena queda cubierta
    engine.process(_event(0, [_person([0, 0, 100, 200]), _helmet([40, 10, 60, 30])]))
    # f1: el casco desaparece. Con memoria activa NO alertaria; sin memoria, alerta.
    result = engine.process(_event(1, [_person([0, 0, 100, 200])]))

    assert len(result.alerts) == 1
    assert "coverage_memory_unsupported_scene" in result.degradation_causes


def test_coverage_memory_still_applies_under_subject_granularity() -> None:
    pattern = _helmet_pattern(
        granularity="subject",
        timing=PatternTimingConfig(confirm_after_frames=1, coverage_memory_frames=5),
    )
    engine = PatternEngine("run-1", [pattern])

    engine.process(_event(0, [_person([0, 0, 100, 200], track_id="subject_001"), _helmet([40, 10, 60, 30])]))
    result = engine.process(_event(1, [_person([0, 0, 100, 200], track_id="subject_001")]))

    assert result.alerts == []
    assert result.degradation_causes == set()


def test_hysteresis_absorbs_epp_flicker_without_coverage_memory() -> None:
    """FALSACION DE ADR-012.

    Un EPP que parpadea (desaparece 1 frame, 100 ms) dentro de la ventana de
    resolve NO debe resolver la condicion ni re-alertar. Si esto falla, la
    apuesta del ADR-012 (la histeresis subsume la memoria) es incorrecta y hay
    que revisar la decision — no el test.
    """
    pattern = _helmet_pattern(
        granularity="scene",
        timing=PatternTimingConfig(confirm_after_ms=0.0, resolve_after_ms=2000.0),
    )
    engine = PatternEngine("run-1", [pattern])

    # t=0: persona sin casco -> confirma y alerta
    first = engine.process(_event(0, [_person([0, 0, 100, 200])], timestamp_ms=0.0))
    assert len(first.alerts) == 1

    # t=100ms: aparece un casco (parpadeo del detector) -> empieza a limpiar
    engine.process(
        _event(1, [_person([0, 0, 100, 200]), _helmet([40, 10, 60, 30])], timestamp_ms=100.0)
    )
    # t=200ms: el casco vuelve a desaparecer, muy dentro de resolve_after_ms
    second = engine.process(_event(2, [_person([0, 0, 100, 200])], timestamp_ms=200.0))

    # No resolvio (nunca paso a `resolved`) => no hay nueva transicion a
    # `confirmed` => no hay re-alerta. OJO: el hit sobre estado `confirmed` SI
    # emite la transicion confirmed->sustained (pattern_engine.py:145-146); eso
    # es correcto y NO invalida la decision. Lo que falsaria el ADR-012 es una
    # transicion a `resolved` (la histeresis no absorbio el parpadeo) o una
    # re-alerta.
    assert second.alerts == []
    assert all(e.state not in {"confirmed", "resolved"} for e in second.pattern_events)
```

**Helpers.** `tests/test_pattern_engine.py` ya trae `_time_pattern()` (`:36`), `_event_at(timestamp_ms, frame_index, has_helmet=False)` (`:60`), `_empty_event(timestamp_ms, frame_index)` (`:122`) y `_lifecycle_pattern(**timing_overrides)` (`:140`). Los tests de arriba usan `_person`/`_helmet`/`_event`/`_helmet_pattern`, que **no existen**. Antes de escribirlos, agregar al archivo:

```python
def _person(bbox: list[float], track_id: str | None = None) -> Detection:
    return Detection(
        label="person", prompt_id="person", confidence=0.9,
        bbox_xyxy=bbox, area_px=(bbox[2] - bbox[0]) * (bbox[3] - bbox[1]),
        track_id=track_id,
    )


def _helmet(bbox: list[float]) -> Detection:
    return Detection(label="helmet", prompt_id="helmet", confidence=0.8, bbox_xyxy=bbox)


def _event(frame_index: int, detections: list[Detection], timestamp_ms: float | None = None) -> DetectionEvent:
    return DetectionEvent.model_validate(
        {
            "run_id": "media-run-1",
            "unit_id": f"img_{frame_index:06d}",
            "source": {
                "source_id": "cam-1", "source_type": "video_frame",
                "frame_index": frame_index, "timestamp_ms": timestamp_ms,
                "width": 640, "height": 480,
            },
            "model": {"name": "mock", "model_id": "mock", "device": "cpu"},
            "prompts": {"prompt_set_id": "test"},
            "detections": [d.model_dump() for d in detections],
            "timing": {},
        }
    )


def _helmet_pattern(**overrides) -> PatternDefinition:
    base = {
        "id": "CR-01", "name": "person_without_helmet", "condition_id": "CR-01",
        "required_absent_class": "helmet",
        "region": {"type": "upper_body", "y_min_ratio": 0.0, "y_max_ratio": 0.45, "x_margin_ratio": 0.12},
    }
    base.update({k: (v.model_dump() if hasattr(v, "model_dump") else v) for k, v in overrides.items()})
    return PatternDefinition.model_validate(base)
```

Los `_person` de los tests no pasan `detection_id`: es deliberado, G0 no lo usa como identidad.

- [ ] **Step 2: Correr para verificar que fallan**

Run: `.venv/bin/python -m pytest tests/test_pattern_engine.py -k "coverage_memory or flicker" -q`
Expected: FAIL — hoy la memoria se aplica sin mirar `granularity`, y `PatternEngineResult` no tiene `degradation_causes`.

- [ ] **Step 3: Implementación**

En `src/eovrt_control/engine/pattern_engine.py`:

1. Agregar el campo al resultado:

```python
@dataclass(frozen=True)
class PatternEngineResult:
    pattern_events: list[PatternStateChanged]
    alerts: list[AlertEvent]
    evidences_count: int
    subjects_count: int
    degradation_causes: set[str] = field(default_factory=set)
```

(importar `field` desde `dataclasses`).

2. En `process`, reemplazar el cálculo de `memory_enabled` (líneas 58-61) por:

```python
            memory_configured = (
                pattern.timing.coverage_memory_ms is not None
                or pattern.timing.coverage_memory_frames is not None
            )
            # ADR-012: bajo escena no hay identidad de sujeto; la memoria de
            # cobertura no es aplicable. Se ignora y se declara la causa.
            memory_enabled = memory_configured and pattern.granularity == "subject"
            if memory_configured and pattern.granularity == "scene":
                degradation_causes.add("coverage_memory_unsupported_scene")
```

3. Inicializar `degradation_causes: set[str] = set()` al comienzo de `process`, unir las del evaluador (`degradation_causes |= result.degradation_causes`) y pasarlo al `PatternEngineResult`.

4. En `_memory_covers`, cortar de entrada bajo escena:

```python
        # ADR-012
        if pattern.granularity != "subject":
            return False
```

- [ ] **Step 4: Correr para verificar que pasan**

Run: `.venv/bin/python -m pytest tests/test_pattern_engine.py -q`
Expected: PASS. Los 7 tests previos siguen verdes; los dos de memoria de cobertura (`memoria puentea gaps`, `memoria no enmascara sujeto nunca cubierto`) deben declarar `granularity="subject"` y `track_id` en sus detecciones, porque esa es ahora la única granularidad donde la memoria vive. Actualizarlos.

- [ ] **Step 5: Si `test_hysteresis_absorbs_epp_flicker_without_coverage_memory` falla, PARAR**

No "arreglar" el test. Es la falsación declarada del ADR-012. Reportar al usuario con la evidencia y proponer la opción viva del ADR §Fundamento (memoria a nivel escena, asumiendo el falso negativo de la alternancia de personas).

- [ ] **Step 6: Commit** (pedir autorización primero)

```bash
git add src/eovrt_control/engine/pattern_engine.py tests/test_pattern_engine.py
git commit -m "feat(engine): memoria de cobertura restringida a G1 (ADR-012)"
```

---

### Task 5: Expiración de sujetos reinterpretada a escena

**Files:**
- Modify: `src/eovrt_control/engine/pattern_engine.py:227-280` (`_expire_absent_subjects`)
- Test: `tests/test_pattern_engine.py`

**Interfaces:**
- Consumes: claves de estado de Task 2.
- Produces: bajo `scene`, la condición resuelve cuando no se observa **ningún** sujeto de la clase durante la ventana `subject_absent_timeout_*`.

La mecánica actual de `_expire_absent_subjects` ya opera sobre claves de estado genéricas, así que **no requiere cambios de código** — requiere un test que fije la semántica de escena (ADR-012 §3) para que no se rompa después.

- [ ] **Step 1: Escribir el test**

```python
def test_scene_condition_resolves_when_no_subject_observed_within_timeout() -> None:
    pattern = _helmet_pattern(
        granularity="scene",
        timing=PatternTimingConfig(
            confirm_after_frames=1, resolve_after_frames=1, subject_absent_timeout_frames=2
        ),
    )
    engine = PatternEngine("run-1", [pattern])

    confirmed = engine.process(_event(0, [_person([0, 0, 100, 200])]))
    assert len(confirmed.alerts) == 1

    # La escena queda vacia de personas.
    engine.process(_event(1, []))
    expired = engine.process(_event(2, []))

    assert [e.state for e in expired.pattern_events] == ["resolved"]
    assert expired.pattern_events[0].subject_key == "CR-01:cam-1"
```

- [ ] **Step 2: Correr para verificar**

Run: `.venv/bin/python -m pytest tests/test_pattern_engine.py::test_scene_condition_resolves_when_no_subject_observed_within_timeout -q`
Expected: PASS sin cambios de implementación. **Si falla**, la mecánica de expiración depende de la forma vieja de la clave: corregir `_expire_absent_subjects` para que no asuma nada sobre el string.

- [ ] **Step 3: Commit** (pedir autorización primero)

```bash
git add tests/test_pattern_engine.py
git commit -m "test(engine): expiracion a nivel escena (ADR-012)"
```

---

### Task 6: `degradation_causes` propagadas al `RunSummary`

**Files:**
- Modify: `src/eovrt_control/runtime/replay.py:83-185`
- Modify: `src/eovrt_control/contracts/metrics.py:37` (`RunSummary`)
- Test: `tests/test_replay.py`

**Interfaces:**
- Consumes: `PatternEngineResult.degradation_causes` (Task 4).
- Produces: `RunSummary.degraded: bool` y `RunSummary.degradation_causes: list[str]` (ordenada), campos aditivos. Requeridos por spec 41 §4.

- [ ] **Step 1: Escribir el test que falla**

```python
def test_replay_reports_degradation_when_scene_pattern_configures_coverage_memory(tmp_path) -> None:
    # Config con granularity: scene y coverage_memory_frames: 5 sobre el fixture existente.
    summary = run_replay(_write_config(tmp_path, granularity="scene", coverage_memory_frames=5))

    assert summary.degraded is True
    assert "coverage_memory_unsupported_scene" in summary.degradation_causes


def test_replay_without_degradation_reports_clean_summary(tmp_path) -> None:
    summary = run_replay(_write_config(tmp_path, granularity="scene"))

    assert summary.degraded is False
    assert summary.degradation_causes == []
```

(`_write_config` es un helper local que escribe un `ReplayConfig` YAML apuntando al fixture; seguir el estilo del `tests/test_replay.py` actual.)

- [ ] **Step 2: Correr para verificar que falla**

Run: `.venv/bin/python -m pytest tests/test_replay.py -q`
Expected: FAIL — `RunSummary` no tiene `degraded` ni `degradation_causes`.

- [ ] **Step 3: Implementación**

En `src/eovrt_control/contracts/metrics.py`, agregar a `RunSummary` (aditivo, junto a `warnings`):

```python
    degraded: bool = False
    degradation_causes: list[str] = Field(default_factory=list)
```

En `src/eovrt_control/runtime/replay.py`:

- Inicializar `degradation_causes: set[str] = set()` junto a los otros acumuladores (cerca de la línea 84).
- Dentro del bucle, tras `result = engine.process(event)`, acumular: `degradation_causes |= result.degradation_causes`.
- En la construcción de `RunSummary` (línea 159), agregar:

```python
        degraded=bool(degradation_causes),
        degradation_causes=sorted(degradation_causes),
```

**Retirar el warning de `det_NNN`** (`replay.py:148-157`) junto con los helpers
`_is_stable_subject_id`, `_count_person_ids` y los contadores
`persons_seen`/`persons_with_stable_id`.

> **CONSERVAR `_requires_temporal_persistence` (`replay.py:31-32`).** Deja de usarse en
> esta tarea, pero la Task 9 lo reusa para detectar persistencia inalcanzable sobre
> fuentes no temporales (ADR-013). No borrarlo. Si el linter se queja de símbolo sin uso
> entre la Task 6 y la Task 9, dejarlo y anotarlo en el reporte.

Tras G0 ese mensaje es **falso** para
el caso principal: un patrón `scene` con `confirm_after_ms` sobre video sin tracker (que
es exactamente `cr01_cr02_v2`) dispararía "el motor no podrá confirmar condiciones",
cuando la clave de escena acumula entre frames sin importar `det_NNN`. La spec 41 §2.1
dice explícitamente que el fallback semántico **reemplaza** ese warning, y el reemplazo
ya existe: la causa `no_track_id` que llega por `degradation_causes`.

El test existente de `tests/test_replay.py` que asertaba ese warning ("warn cuando la
persistencia necesita ids estables") se **reemplaza** por la aserción de la degradación:

```python
def test_replay_reports_no_track_id_for_subject_pattern_without_tracks(tmp_path) -> None:
    summary = run_replay(_write_config(tmp_path, granularity="subject"))

    assert summary.degraded is True
    assert "no_track_id" in summary.degradation_causes
```

(el fixture trae `detection_id` estables pero ningún `track_id`, así que un patrón
`subject` degrada — que es el comportamiento definido).

- [ ] **Step 4: Correr para verificar que pasa**

Run: `.venv/bin/python -m pytest tests/test_replay.py -q`
Expected: PASS.

- [ ] **Step 5: Commit** (pedir autorización primero)

```bash
git add src/eovrt_control/contracts/metrics.py src/eovrt_control/runtime/replay.py tests/test_replay.py
git commit -m "feat(runtime): degraded + degradation_causes en RunSummary"
```

---

### Task 7: Fixture `clip_gt.v2` a nivel escena-condición y gate F1 = 1.0

**Files:**
- Create: `fixtures/simulated_media/cr01_cr02_temporal/ground_truth_v2.json`
- Create: `fixtures/simulated_media/cr01_cr02_temporal_tracked/detections.jsonl`
- Create: `fixtures/simulated_media/cr01_cr02_temporal_tracked/ground_truth_v2.json`
- Modify: `src/eovrt_control/evaluation/temporal.py`
- Modify: `tests/test_temporal_evaluation.py`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: `evaluate_temporal_alerts(alerts, gt_path)` acepta `schema_version: "clip_gt.v2"` (episodios escena-condición) **y** el `control.eval.temporal.v1` actual. El gate `F1 = 1.0` corre en ambas granularidades.

- [ ] **Step 1: Escribir el GT v2 de escena**

`fixtures/simulated_media/cr01_cr02_temporal/ground_truth_v2.json`:

```json
{
  "schema_version": "clip_gt.v2",
  "clip_id": "cr01_cr02_temporal",
  "source_file": "detections.jsonl",
  "negative": false,
  "episodes": [
    {
      "id": "ep_cr01",
      "condition_id": "CR-01",
      "level": "scene",
      "source_id": "sim-risk-stream",
      "first_evidence_frame_index": 3,
      "expected_alert_frame_index": 5,
      "max_alert_frame_index": 5,
      "first_evidence_timestamp_ms": 1500.0,
      "subjects_in_evidence": 1
    },
    {
      "id": "ep_cr02",
      "condition_id": "CR-02",
      "level": "scene",
      "source_id": "sim-risk-stream",
      "first_evidence_frame_index": 5,
      "expected_alert_frame_index": 7,
      "max_alert_frame_index": 7,
      "first_evidence_timestamp_ms": 2500.0,
      "subjects_in_evidence": 1
    }
  ]
}
```

**Antes de fijar estos números**, correr el replay sobre el fixture con `granularity: scene` y leer `alerts.jsonl`: los `frame_index` de las alertas reales son la verdad. El escenario que se conserva es el del fixture actual (CR-01 persistente, CR-02 persistente, riesgo transitorio no alertable); si al colapsar a escena el riesgo transitorio pasa a ser alertable, **eso es un hallazgo, no un error de fixture** — documentarlo y consultarlo, no ajustar el GT para taparlo.

- [ ] **Step 2: Escribir el fixture con `track_id`**

Copiar `fixtures/simulated_media/cr01_cr02_temporal/detections.jsonl` a `fixtures/simulated_media/cr01_cr02_temporal_tracked/detections.jsonl`, agregando `"track_id": "worker_a"` / `"worker_b"` / `"worker_c"` a cada detección `person` según el `detection_id` que hoy ya llevan (el fixture usa ids semánticos, no `det_NNN`).

Su `ground_truth_v2.json` usa `"level": "subject"` y `"subject_key": "CR-01:sim-risk-stream:worker_a"` (etc.), con los mismos frames esperados que el GT v1 actual.

- [ ] **Step 3: Escribir el test que falla**

```python
def test_scene_granularity_matches_expected_alerts_with_f1_one(tmp_path) -> None:
    summary = run_replay(_scene_config(tmp_path))
    evaluation = evaluate_temporal_alerts(
        alerts_path=summary.output_files["alerts"],
        ground_truth_path="fixtures/simulated_media/cr01_cr02_temporal/ground_truth_v2.json",
    )
    assert evaluation.precision == 1.0
    assert evaluation.recall == 1.0
    assert evaluation.f1 == 1.0


def test_subject_granularity_matches_expected_alerts_with_f1_one(tmp_path) -> None:
    summary = run_replay(_subject_config(tmp_path))
    evaluation = evaluate_temporal_alerts(
        alerts_path=summary.output_files["alerts"],
        ground_truth_path="fixtures/simulated_media/cr01_cr02_temporal_tracked/ground_truth_v2.json",
    )
    assert evaluation.f1 == 1.0


def test_v1_ground_truth_still_evaluates(tmp_path) -> None:
    """Gate del item 7 del orden de trabajo: v1 no se rompe."""
    gt = TemporalGroundTruth.model_validate_json(
        Path("fixtures/simulated_media/cr01_cr02_temporal/ground_truth.json").read_text()
    )
    assert gt.scenario_id == "cr01_cr02_temporal"
```

- [ ] **Step 4: Correr para verificar que falla**

Run: `.venv/bin/python -m pytest tests/test_temporal_evaluation.py -q`
Expected: FAIL — el evaluador no reconoce `clip_gt.v2`.

- [ ] **Step 5: Implementación — soporte `clip_gt.v2` en el evaluador**

En `src/eovrt_control/evaluation/temporal.py`, agregar el modelo de episodio y el dispatch por `schema_version`:

**Por qué el episodio de escena NO se proyecta a un `subject_key` sintético**
(`f"{condition_id}:{source_id}"`): la clave de estado del motor usa **`pattern.id`**, no
`condition_id`. En este fixture coinciden (`CR-01`/`CR-01`) y los tests pasarían — pero en
`cr01_cr02_v2` divergen (patrón **PR-01**, condición CR-01) y el string sintético no
matchearía nunca: F1 = 0 silencioso semanas después. Los episodios de escena se matchean
por **campos estructurados** del `AlertEvent`, que ya existen (`condition_id`, `source_id`).

```python
class ClipEpisode(BaseModel):
    id: str
    condition_id: str
    level: Literal["scene", "subject"] = "scene"
    source_id: str | None = None
    subject_key: str | None = None
    first_evidence_frame_index: int
    expected_alert_frame_index: int
    max_alert_frame_index: int | None = None
    first_evidence_timestamp_ms: float | None = None
    subjects_in_evidence: int | None = None


class ClipGroundTruthV2(BaseModel):
    schema_version: Literal["clip_gt.v2"]
    clip_id: str
    episodes: list[ClipEpisode] = Field(default_factory=list)


def _load_ground_truth(path: Path) -> TemporalGroundTruth:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") == "clip_gt.v2":
        clip = ClipGroundTruthV2.model_validate(raw)
        return TemporalGroundTruth(
            scenario_id=clip.clip_id,
            expected_alerts=[
                ExpectedAlert(
                    id=episode.id,
                    condition_id=episode.condition_id,
                    subject_key=episode.subject_key,
                    source_id=episode.source_id,
                    first_evidence_frame_index=episode.first_evidence_frame_index,
                    expected_alert_frame_index=episode.expected_alert_frame_index,
                    max_alert_frame_index=episode.max_alert_frame_index,
                    first_evidence_timestamp_ms=episode.first_evidence_timestamp_ms,
                )
                for episode in clip.episodes
            ],
        )
    return TemporalGroundTruth.model_validate(raw)
```

Cambios en `ExpectedAlert` (aditivos; los GT v1 siempre traen `subject_key`):

```python
    subject_key: str | None = None
    source_id: str | None = None
```

Nuevo helper de matching, y en el filtro de candidatos de `evaluate_temporal_alerts`
(`temporal.py:132-139`) reemplazar `alert.subject_key == expected.subject_key` por
`_expected_key_matches(alert, expected)` (la condición de `condition_id` ya está):

```python
def _expected_key_matches(alert: AlertEvent, expected: ExpectedAlert) -> bool:
    if expected.subject_key is not None:
        return alert.subject_key == expected.subject_key
    # Episodio de escena: matching estructurado, inmune a pattern_id != condition_id.
    return alert.source_id == expected.source_id
```

En `MissedAlert`, donde `subject_key` es requerido, construirlo con
`expected.subject_key or f"{expected.condition_id}@{expected.source_id}"`.

Finalmente, hacer que `evaluate_temporal_alerts` cargue el GT con `_load_ground_truth`
en vez de validar directo.

**Nota (ADR-011):** el matcher cuenta como `unexpected` toda alerta fuera de un episodio. Una re-alerta dentro del mismo episodio es `re_alert`, no FP. Si el fixture de escena produce re-alertas, este test las descubrirá; su tratamiento formal es el ítem 7 del orden de trabajo (`evaluate-alerts v2`). Si aparecen acá, **contarlas y reportarlas**, no penalizarlas.

- [ ] **Step 6: Correr el gate**

Run: `.venv/bin/python -m pytest tests/test_temporal_evaluation.py -q`
Expected: PASS — `f1 == 1.0` en ambas granularidades.

- [ ] **Step 7: Correr la suite completa**

Run: `.venv/bin/python -m pytest -q --ignore=tests/labs`
Expected: PASS, todo verde. Este es el **gate de merge del motor** (spec 41 §2.2).

- [ ] **Step 8: Commit** (pedir autorización primero)

```bash
git add fixtures/ src/eovrt_control/evaluation/temporal.py tests/test_temporal_evaluation.py
git commit -m "feat(eval): GT clip_gt.v2 escena-condicion; gate F1=1.0 en ambas granularidades"
```

---

### Task 8: `subjects_in_evidence_max` del episodio

**Files:**
- Modify: `src/eovrt_control/engine/pattern_engine.py` (`PatternRuntimeState`, `_advance_hit`, `_make_state_event`, `_make_alert`)
- Modify: `src/eovrt_control/contracts/pattern.py` (`PatternStateChanged`), `src/eovrt_control/contracts/alerts.py` (`AlertEvent`)
- Test: `tests/test_pattern_engine.py`

**Interfaces:**
- Consumes: `PatternEvidence.subjects_in_evidence` (Task 2).
- Produces: `PatternStateChanged.subjects_in_evidence_max: int | None` y `AlertEvent.subjects_in_evidence_max: int | None` (aditivos) — el máximo de sujetos en evidencia visto en el episodio hasta ese instante. Spec 41 §2.1 lo exige ("máximo y por unidad") como insumo del GT `clip_gt.v2` y de la limitación declarada del doc 07 D2.2.

- [ ] **Step 1: Escribir el test que falla**

```python
def test_alert_carries_episode_max_subjects_in_evidence() -> None:
    pattern = _helmet_pattern(
        granularity="scene", timing=PatternTimingConfig(confirm_after_frames=2)
    )
    engine = PatternEngine("run-1", [pattern])

    # f0: dos personas sin casco (candidate; max episodico = 2)
    engine.process(_event(0, [_person([0, 0, 100, 200]), _person([300, 0, 400, 200])]))
    # f1: queda una sin casco -> confirma
    result = engine.process(_event(1, [_person([0, 0, 100, 200])]))

    assert len(result.alerts) == 1
    assert result.alerts[0].subjects_in_evidence_max == 2


def test_max_subjects_resets_on_new_episode() -> None:
    pattern = _helmet_pattern(
        granularity="scene",
        timing=PatternTimingConfig(confirm_after_frames=1, resolve_after_frames=1),
    )
    engine = PatternEngine("run-1", [pattern])

    engine.process(_event(0, [_person([0, 0, 100, 200]), _person([300, 0, 400, 200])]))
    # f1: todos cubiertos -> resuelve (fin del episodio 1)
    engine.process(
        _event(1, [_person([0, 0, 100, 200]), _helmet([40, 10, 60, 30])])
    )
    # f2: una persona sin casco -> episodio 2
    result = engine.process(_event(2, [_person([0, 0, 100, 200])]))

    assert result.alerts[0].subjects_in_evidence_max == 1
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `.venv/bin/python -m pytest tests/test_pattern_engine.py -k subjects_in_evidence -q`
Expected: FAIL — el campo no existe.

- [ ] **Step 3: Implementación**

1. `PatternRuntimeState` gana `max_subjects_in_evidence: int = 0`.
2. En `_advance_hit`, junto al reset de `hit_count` cuando el estado era
   `inactive`/`resolved` (nuevo episodio), resetear también
   `runtime_state.max_subjects_in_evidence = 0`. Tras el reset, siempre:

```python
        runtime_state.max_subjects_in_evidence = max(
            runtime_state.max_subjects_in_evidence, evidence.subjects_in_evidence or 1
        )
```

3. `PatternStateChanged` y `AlertEvent` ganan `subjects_in_evidence_max: int | None = None`
   (aditivo, sin bump de `schema_version`).
4. `_make_state_event` recibe el valor y lo puebla; `_advance_hit`, `_advance_clear` y
   `_expire_absent_subjects` lo pasan desde su `runtime_state` (en los dos últimos, el
   máximo del episodio que se cierra). `_make_alert` lo copia del `change`.

- [ ] **Step 4: Correr para verificar que pasa**

Run: `.venv/bin/python -m pytest -q --ignore=tests/labs`
Expected: PASS, suite completa.

- [ ] **Step 5: Commit** (pedir autorización primero)

```bash
git add src/eovrt_control/engine/pattern_engine.py src/eovrt_control/contracts/ tests/test_pattern_engine.py
git commit -m "feat(engine): maximo episodico de subjects_in_evidence (spec 41 §2.1)"
```

---

### Task 9: Detección automática de fuente no temporal (ADR-013)

**Files:**
- Modify: `src/eovrt_control/contracts/metrics.py` (`RunSummary`)
- Modify: `src/eovrt_control/runtime/replay.py`
- Test: `tests/test_replay.py`

**Interfaces:**
- Consumes: `DetectionEventSource.source_type` (ya existe en el contrato, ya poblado).
- Produces: `RunSummary.pattern_evaluation: ApplicabilityState | None` — campo aditivo con `{state, cause}`, vocabulario de ADR-006.

La detección **no se configura**: se lee de los eventos. Verificado sobre `detections.jsonl` real del BENCH: `source_type: "image"`, `frame_index: None`, `timestamp_ms: None`.

- [ ] **Step 1: Escribir el test que falla**

```python
def test_image_source_marks_pattern_evaluation_not_applicable(tmp_path) -> None:
    """ADR-013: sobre imagenes la evaluacion de patrones no aplica."""
    summary = run_replay(_write_config(tmp_path, input_path=_IMAGE_FIXTURE))

    assert summary.pattern_evaluation.state == "not_applicable"
    assert summary.pattern_evaluation.cause == "non_temporal_source"


def test_image_source_with_persistence_flags_unreachable_alerts(tmp_path) -> None:
    """Un patron con persistencia sobre imagenes no puede alertar NUNCA."""
    summary = run_replay(
        _write_config(tmp_path, input_path=_IMAGE_FIXTURE, confirm_after_frames=3)
    )

    assert summary.alerts_count == 0
    assert "persistence_unreachable_on_non_temporal_source" in summary.pattern_evaluation.cause


def test_video_source_marks_pattern_evaluation_computed(tmp_path) -> None:
    summary = run_replay(_write_config(tmp_path))  # fixture temporal (video_frame)

    assert summary.pattern_evaluation.state == "computed"
    assert summary.pattern_evaluation.cause is None
```

`_IMAGE_FIXTURE`: un `detections.jsonl` de 2 líneas con `source_type: "image"`,
`timestamp_ms: null`, `frame_index: null` y `source_id` distinto por línea (que es la
forma real que emite `ImageFolderSource`). Crearlo en
`fixtures/simulated_media/image_folder_smoke/detections.jsonl`.

- [ ] **Step 2: Correr para verificar que falla**

Run: `.venv/bin/python -m pytest tests/test_replay.py -k pattern_evaluation -q`
Expected: FAIL — `RunSummary` no tiene `pattern_evaluation`.

- [ ] **Step 3: Implementación**

En `src/eovrt_control/contracts/metrics.py`:

```python
class ApplicabilityState(BaseModel):
    """Vocabulario de ADR-006."""

    state: Literal["computed", "applicable_not_computed", "not_applicable", "not_interpretable"]
    cause: str | None = None
```

y en `RunSummary`, aditivo:

```python
    pattern_evaluation: ApplicabilityState | None = None
```

En `src/eovrt_control/runtime/replay.py`:

- Acumular en el bucle: `source_types.add(event.source.source_type)`.
- Tras el bucle, antes de construir el `RunSummary`:

```python
def _pattern_evaluation_state(
    source_types: set[str], active_patterns: list[PatternDefinition]
) -> ApplicabilityState:
    """ADR-013: la temporalidad se detecta, no se configura."""
    if source_types and source_types <= {"image"}:
        causes = ["non_temporal_source"]
        # Sobre imagenes cada unidad es su propia escena (source_id = archivo):
        # hit_count nunca supera 1, asi que la persistencia no se alcanza jamas.
        if any(_requires_temporal_persistence(pattern) for pattern in active_patterns):
            causes.append("persistence_unreachable_on_non_temporal_source")
        return ApplicabilityState(state="not_applicable", cause="+".join(causes))
    return ApplicabilityState(state="computed")
```

**Conservar `_requires_temporal_persistence`** (`replay.py:31-32`) — la Task 6 retira el
warning de `det_NNN` y sus otros helpers, pero este predicado se reusa acá. Ampliarlo para
cubrir también `resolve_after_ms` y `subject_absent_timeout_*`, que la spec 41 §2.3 lista.

- [ ] **Step 4: Correr para verificar que pasa**

Run: `.venv/bin/python -m pytest -q --ignore=tests/labs`
Expected: PASS, suite completa.

- [ ] **Step 5: Verificación end-to-end contra el BENCH real**

```bash
.venv/bin/eovrt-control replay configs/fase0_dbe_gdino_bench_rerun.yaml
python3 -c "import json; s=json.load(open('runs/fase0_dbe_gdino_bench_mati_20260709/summary.json')); print(s['pattern_evaluation'])"
```
Expected: `{'state': 'not_applicable', 'cause': 'non_temporal_source'}` — el BENCH es
`image_folder`, y `cr01_cr02_v1` no configura persistencia.

Y contra el probe de persistencia (que sí la configura):

```bash
.venv/bin/eovrt-control replay configs/fase0_dbe_gdino_bench_persistence_probe.yaml
```
Expected: `cause` incluye `persistence_unreachable_on_non_temporal_source`, y
`alerts_count: 0` — que es lo que el doc 33 §4 midió a ciegas y ahora la plataforma declara.

- [ ] **Step 6: Commit** (pedir autorización primero)

```bash
git add src/eovrt_control/contracts/metrics.py src/eovrt_control/runtime/replay.py fixtures/simulated_media/image_folder_smoke/ tests/test_replay.py
git commit -m "feat(runtime): deteccion de fuente no temporal y aplicabilidad (ADR-013)"
```

---

## Alineación con los dos escenarios (DBE / EBE)

Este plan toca solo el motor, que es común a ambos. Lo que cambia por escenario, declarado:

- **DBE sobre imágenes** (BENCH): `source_id` = nombre de archivo → **cada imagen es su
  propia escena**; G0 no acumula entre unidades, que es lo correcto para imágenes
  independientes. Además `timestamp_ms` es `None` → los umbrales `confirm_after_ms` de
  `cr01_cr02_v2` quedan inertes y el motor cae a `confirm_after_frames` (default 1):
  **confirmación instantánea por imagen**. Comportamiento correcto pero silencioso —
  cuando se cree `cr01_cr02_v2` (fuera de este plan) hay que documentarlo en el pattern set.
  **Regla de uso (ADR-013):** los datasets de imágenes NO evalúan patrones — evalúan
  percepción y asociación espacial por frame (mAP, AP por clase, recall por persona vía
  `person_gt`, para lo cual `supporting` conserva los bboxes). Sobre imágenes, el plano de
  control se corre solo como smoke de contrato o diagnóstico del evaluador espacial. La
  evaluación de patrones (episodios, persistencia, re-alertas) pertenece al video con GT
  temporal (clip bench, spec 43) y a fuentes vivas. **La plataforma lo detecta sola**
  (Task 9): `source_type: "image"` ⇒ `pattern_evaluation: not_applicable /
  non_temporal_source`, y si el pattern set trae persistencia, la causa adicional avisa que
  esa corrida no puede alertar.
- **DBE sobre video** (archivo): `source_id` constante, `timestamp_ms` = tiempo de medio
  → la escena acumula y las ventanas en ms operan. Es el primer escenario donde G0 paga.
- **EBE (RTSP)**: idéntico al video para el motor, con `timestamp_ms` wall-clock. G0
  funciona **sin tracker** (la clave no depende de ids de detección) — este es el valor
  central del cambio: hoy el estado sobre `det_NNN` no confirma nada en vivo (doc 33 §4).
- **Consecuencia medible sobre el BENCH:** después de G0, una imagen con 2 personas
  descubiertas emite **una** alerta CR-01 (con `subjects_in_evidence = 2`), no dos. El
  baseline 137 del doc 33 **va a bajar por diseño** — no es una regresión; la comparación
  motor-viejo-vs-nuevo del doc 33 fue pre-G0 y no se re-litiga con estos números.

## Qué queda fuera de este plan

- `MediaEventSource` / `BusSource` / runtime live (spec 41 §3–4) → plan 2.
- Servicio mínimo del control-plane (spec 41 §5) → plan 3.
- Publisher del media-plane, `seq`, `track_id` emitido por el tracker portado, instrumentación `t_capture→alert` (spec 42, spec 40 §5.2.4) → planes 2 y 4.
- Pattern set `cr01_cr02_v2` (spec 41 §7): depende de este plan (usa `granularity: scene`) pero su calibración es del tramo de evaluación.
