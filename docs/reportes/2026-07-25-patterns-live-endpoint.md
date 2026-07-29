# Informe: patrones activos en vivo por HTTP

## Qué se hizo

Feature de solo lectura para exponer, en `GET /api/runs/current`, qué patrones de
riesgo están actualmente `confirmed` o `sustained`. Tres cambios:

1. **`PatternEngine.snapshot_active()`** (`src/eovrt_control/engine/pattern_engine.py`)
   — nuevo método, sin efectos. Copia `list(self._state.items())` antes de iterar
   (documentado: otro hilo puede estar mutando `_state` durante `process()`), filtra
   por `state in {"confirmed", "sustained"}` y arma un dict por entrada con
   `pattern_id`, `condition_id`, `severity`, `subject_key`, `state`,
   `since_timestamp_ms`, `subjects_in_evidence`.

   - `since_timestamp_ms` = `PatternRuntimeState.first_hit_timestamp_ms` — verificado
     leyendo `_advance_hit`: es el timestamp del evento que abrió el episodio actual
     (se resetea a `None` en cada transición a `resolved`/nuevo episodio), exactamente
     "el instante de la primera evidencia del episodio" que pedía la tarea. Ojo: hay
     un campo hermano `first_evidence_monotonic_ms` que es el reloj monotónico de
     *recepción* (spec 40 §5.2.4, usado para TTFA interno) — no es el mismo concepto,
     así que no lo usé.
   - `subjects_in_evidence` = `PatternRuntimeState.max_subjects_in_evidence` — es el
     único contador de sujetos-en-evidencia que el motor mantiene por episodio (no hay
     un "current count", solo el máximo episódico). Lo expongo tal cual porque es "el
     mejor disponible", como pedía la consigna ("si está disponible").
   - Necesité agregar `self._patterns_by_id: dict[str, PatternDefinition]` en
     `PatternEngine.__init__` para resolver `condition_id`/`severity` desde el
     `pattern_id` de la clave de estado (antes no había ese índice).

2. **Publicar el engine vía `RunProgress`** (`src/eovrt_control/runtime/core.py`) —
   agregué el campo `engine: PatternEngine | None = None` a `RunProgress`. Elegí
   *campo*, no callback: `RunProgress` ya es el objeto de lectura compartida entre el
   hilo de ejecución y el hilo HTTP (según su propio docstring, "se leen desde el
   hilo del servidor"), y una referencia de objeto asignada una sola vez (nunca
   reasignada) es atómica bajo el GIL igual que los enteros que ya vivían ahí — no
   rompe la garantía documentada de "no hace falta lock". Un callback hubiese sido
   una capa extra sin necesidad real (no hay lógica de transformación entre
   `execute_over_source` y el consumidor HTTP). En `execute_over_source` seteo
   `progress.engine = engine` inmediatamente después de construir el motor, ANTES del
   bucle — así `/api/runs/current` puede llegar en cualquier momento de la corrida,
   incluso antes de la primera unidad, y ya encuentra el engine (con estado vacío).
   `execute_over_source` es el único call site compartido por `run_replay_from_config`
   y `run_live_from_config` (grep verificado), así que ambos modos quedan cubiertos
   sin duplicar código.

3. **Exponerlo en `/api/runs/current`** (`src/eovrt_control/service/run_manager.py`,
   `_describe_active`) — agregué la clave `"patterns"` con
   `active.progress.engine.snapshot_active() if active.progress.engine is not None
   else []`. No se tocó ningún router ni se creó endpoint nuevo; `_describe_active`
   ya es el único productor del payload que consume la consola vía polling.

## Qué NO se tocó

- La máquina de estados (`_advance_hit`, `_advance_clear`, `_expire_absent_subjects`,
  `_confirmation_met`, `_resolution_met`) — intacta.
- La emisión de alertas (`_maybe_alert`, `_make_alert`, cooldown) — intacta, ADR-011
  respetado (una alerta por transición a `confirmed`, sin repetición).
- Los `JsonlSink` — no se agregó `flush()`, como se pidió explícitamente.
- No hay nuevos endpoints; solo la respuesta de `/api/runs/current`.

## Tests (TDD)

Cada bloque de tests se escribió antes de la implementación y se vio fallar primero
(`AttributeError: no attribute 'snapshot_active'` para el motor, `KeyError: 'patterns'`
para el endpoint).

`tests/test_pattern_engine.py` — 5 tests nuevos:
- `test_snapshot_active_is_empty_when_nothing_was_ever_processed`
- `test_snapshot_active_excludes_a_candidate_pattern`
- `test_snapshot_active_includes_a_confirmed_pattern` (verifica el contrato completo
  de campos, incluido `since_timestamp_ms == 0.0` y `subjects_in_evidence == 1`)
- `test_snapshot_active_includes_a_sustained_pattern` (verifica que
  `since_timestamp_ms` sigue anclado al primer hit del episodio, no al último evento)
- `test_snapshot_active_drops_a_resolved_pattern`

`tests/test_service_api.py` — extendí
`test_current_reports_the_active_run_and_its_progress` (run `live` contra un bus
idle, ya existente en el archivo) con la aserción `body["patterns"] == []`: verifica
que la clave está siempre presente en el payload real de `/api/runs/current`, incluso
sin patrones activos.

## Resultado real de los comandos

Baseline (antes de tocar nada):
```
$ .venv/bin/python -m pytest tests/ -q --ignore=tests/labs
...
239 passed, 2 warnings in 4.71s
```
(el warning es un `PytestUnhandledThreadExceptionWarning` esperado de un test que
fuerza un `KeyboardInterrupt` en el hilo de ejecución para probar el manejo de
`BaseException` — no relacionado con esta feature.)

Tests nuevos vistos fallar antes de implementar:
```
$ .venv/bin/python -m pytest tests/test_pattern_engine.py -q -k snapshot_active
...
AttributeError: 'PatternEngine' object has no attribute 'snapshot_active'
5 failed, 24 deselected in 0.19s

$ .venv/bin/python -m pytest tests/test_service_api.py -q -k test_current_reports
...
KeyError: 'patterns'
1 failed, 31 deselected, 1 warning in 0.63s
```

Después de implementar:
```
$ .venv/bin/python -m pytest tests/test_pattern_engine.py -q
29 passed in 0.16s

$ .venv/bin/python -m pytest tests/test_service_api.py -q -k test_current_reports
1 passed, 31 deselected, 1 warning in 0.41s

$ .venv/bin/python -m pytest tests/ -q --ignore=tests/labs
244 passed, 2 warnings in 4.53s
```
(239 baseline + 5 tests nuevos de `snapshot_active` = 244; el `test_service_api.py`
existente se extendió in-place, no sumó un test nuevo.)

Lint:
```
$ .venv/bin/python -m ruff check src tests
All checks passed!
```

## Concerns / puntos a revisar

- **Nota importante sobre `python3` vs venv**: `python3 -m pytest ...` (el comando
  literal de la consigna) falla en collection con `ModuleNotFoundError: No module
  named 'pydantic'` porque resuelve a Python 3.14 del sistema, sin el venv del repo.
  Usé `.venv/bin/python -m pytest ...` en su lugar para obtener resultados reales; si
  el usuario corre el comando tal cual está en el mensaje sin activar el venv, va a
  ver 19 errores de colección que no tienen nada que ver con esta feature.
- `subjects_in_evidence` es el máximo episódico, no un conteo "ahora mismo" (el motor
  no lo trackea por separado). Si más adelante se necesita el conteo instantáneo, hay
  que agregar un campo nuevo a `PatternRuntimeState` — no lo hice porque es fuera de
  alcance (cambiaría la máquina de estados/su información, no solo la lectura).
- `since_timestamp_ms` es `None` en el caso límite de fuentes de imagen sin
  `timestamp_ms` (`event.source.timestamp_ms` puede ser `None`); el contrato del
  frontend no dice cómo tratar ese caso — lo dejo pasar tal cual (`None`) en vez de
  inventar un placeholder.
- No commiteé nada, como se pidió.
