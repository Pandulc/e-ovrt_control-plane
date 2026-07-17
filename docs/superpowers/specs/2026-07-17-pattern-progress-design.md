# Progreso parcial de patrones de riesgo — Diseño (Pieza A)

**Fecha**: 2026-07-17
**Estado**: aprobado en brainstorming; pendiente de plan de implementación
**Repo**: `e-ovrt_control-plane`
**Alcance**: el motor de patrones materializa, persiste y expone el **progreso parcial**
de cada condición de riesgo por-frame (ej: "persona sin casco, va por 3.2s de 4s = 80%").

## 1. Propósito y contexto

Esta es la **pieza A** de una feature de dos piezas que cruza dos repos:

- **A (este spec) — control-plane**: exponer el progreso parcial de los patrones. Es el
  cimiento.
- **B (ciclo de diseño aparte) — webconsole**: una vista con las detecciones por frame +
  los patrones cumplidos (alertas) + la barra de progreso, leyendo lo que A expone.

B no puede mostrar el "80%" hasta que A lo emita, por eso A va primero y es un spec
independiente que produce software funcional por sí mismo (el dato queda disponible por
HTTP y verificable con `curl` + el JSONL).

**Modo elegido: post-hoc.** El progreso se persiste por-frame y se relee sobre una corrida
terminada. Encaja con el principio del proyecto —el JSONL es la verdad, toda corrida es
re-evaluable offline— y con la vista de detalle de la consola, que ya es post-hoc. El modo
**en vivo** (streamear el progreso mientras corre una cámara) queda fuera de alcance y se
podrá sumar después sobre esta misma base (§8).

## 2. El hallazgo que hace esto barato

El cálculo del progreso **ya existe** en el motor y se descarta cada frame.
`_confirmation_met` (`src/eovrt_control/engine/pattern_engine.py:397`) hace:

```python
elapsed_ms = event.source.timestamp_ms - runtime_state.first_hit_timestamp_ms
return elapsed_ms >= pattern.timing.confirm_after_ms          # rama temporal
# fallback sin reloj:
return runtime_state.hit_count >= pattern.timing.confirm_after_frames  # rama por frames
```

`first_hit_timestamp_ms` (`PatternRuntimeState`, `pattern_engine.py:21`) se guarda por
`(pattern_id, subject_key)` cuando la persona entra en evidencia y se resetea a `None` al
resolverse. O sea: los insumos del ratio (`elapsed`, `threshold`) ya están calculados y
vivos por track. Esta feature **no inventa lógica** — deriva un número que el motor ya
computa, lo materializa y lo expone. No toca la máquina de estados, ni las alertas, ni
ningún contrato existente.

## 3. Decisiones cerradas

| # | Decisión | Razón |
|---|---|---|
| D1 | Post-hoc primero; live fuera de alcance | JSONL como verdad; la vista de detalle ya es post-hoc |
| D2 | `progress = clamp(elapsed / threshold, 0, 1)`, relativo a **cada condición** | 3.2s es 80% de CR-01 (4000ms) pero 46% de CR-02 (7000ms) |
| D3 | Solo se emite en estado `candidate` | `inactive` = sin progreso; `confirmed` = 100% y ya es alerta |
| D4 | Persistir en un **archivo nuevo** `pattern_progress.jsonl` | No cambia la semántica de `pattern_events.jsonl` (hoy = solo transiciones) ni de `alerts.jsonl`; nada existente cambia de forma |
| D5 | Endpoint nuevo `GET /api/runs/{id}/pattern-progress`, simétrico a `/alerts` | Reusa el patrón de `RunManager.alerts()` |
| D6 | No tocar máquina de estados, alertas, ni contratos existentes | La feature es aditiva |

## 4. El modelo de progreso

Por cada frame en el que un `(pattern_id, subject_key)` está en estado `candidate`, el motor
produce un registro de progreso:

```
{
  schema_version: "control.pattern_progress.v1",
  control_run_id, media_run_id, experiment_id,   # misma convención de ids que control.pattern_state.v1
  pattern_id, condition_id, subject_key,
  frame_index, unit_id, timestamp_ms,
  mode: "time" | "frames",     # cuál umbral rige (según reloj de la fuente)
  elapsed_ms, threshold_ms,    # crudos temporales (null si el patrón no define confirm_after_ms)
  elapsed_frames, threshold_frames,  # elapsed_frames siempre (= hit_count); threshold_frames solo si el patrón define confirm_after_frames, si no null
  progress: float              # 0..1, clamp
}
```

Reglas:

- **Relativo a la condición** (D2): `threshold_ms` sale de `pattern.timing.confirm_after_ms`
  de ese patrón; `threshold_frames` de `confirm_after_frames`.
- **Dos modos, un ratio** (espeja `_confirmation_met`): si la fuente tiene reloj
  (`event.source.timestamp_ms` no nulo y hay `confirm_after_ms`), `mode="time"` y
  `progress = clamp(elapsed_ms / threshold_ms, 0, 1)`. Si no, `mode="frames"` y
  `progress = clamp(elapsed_frames / threshold_frames, 0, 1)` con
  `elapsed_frames = hit_count`. El `progress` siempre viene normalizado 0–1; los crudos van
  para que la consola pueda mostrar "3.2s / 4s" o "12 / 16 frames".
- **Clamp y confirmación** (D3): nunca > 1.0. Al cruzar el umbral el patrón pasa a
  `confirmed` (alerta) y **deja de emitir progreso** — el último registro de progreso es el
  frame previo a la confirmación. No hay registros de progreso para estados `confirmed`,
  `sustained`, `resolved` ni `inactive`.
- **Reset**: si la persona recupera el EPP antes del umbral, `first_hit_timestamp_ms` se
  resetea (ya ocurre, `pattern_engine.py:247,339`) y el próximo episodio arranca de 0. El
  progreso no queda "pegado".

## 5. Emisión y persistencia

- **Emisión**: el motor emite el registro de progreso en `process()`
  (`pattern_engine.py:80`) para cada frame donde un subject está en `candidate`. Es una
  salida **aditiva** del `PatternEngineResult` — no reemplaza `PatternStateChanged` ni
  `AlertEvent`, se suma.
- **Persistencia**: un writer nuevo escribe `runs/<id>/pattern_progress.jsonl`, un registro
  por línea, siguiendo el patrón de escritura de artefactos que ya usa `pattern_events.jsonl`
  y `alerts.jsonl` (runtime/core + artifacts). `pattern_events.jsonl` y `alerts.jsonl`
  quedan **byte-idénticos** a lo que son hoy.
- **Volumen (consecuencia aceptada)**: a diferencia de `pattern_events.jsonl` (solo
  transiciones), este artefacto crece linealmente — un registro por (frame, patrón, sujeto)
  mientras haya estado `candidate`, acotado por el volumen de detección. En modo time un
  sujeto persistente confirma al llegar al umbral y deja de emitir; solo un sujeto que oscila
  justo por debajo del umbral produce crecimiento sostenido. Post-hoc, sin streaming: aceptado.

## 6. El endpoint

`GET /api/runs/{run_id}/pattern-progress?limit=` en el servicio HTTP del control-plane
(`src/eovrt_control/service/routers/runs.py`, prefijo `/api`). Implementación simétrica a
`RunManager.alerts()` (`run_manager.py:179`): lee `pattern_progress.jsonl` crudo y devuelve
las filas tal cual, con `limit` opcional.

- **200**: lista de registros de progreso (posiblemente vacía).
- **404**: run inexistente.
- Run sin `pattern_progress.jsonl` (ninguna condición estuvo nunca en curso, o fuente sin
  tiempo): **200 con lista vacía**, no 404 — la ausencia de progreso es un resultado válido.

La consola (pieza B) unirá estos registros con las detecciones por `frame_index`/`unit_id`,
que es la clave de join canónica entre planos.

## 7. Trampa explícita: fuentes sin reloj

El progreso **temporal** solo existe si la fuente tiene tiempo. En corridas de video/live
(`source_clock: wallclock`/`media`) el `elapsed_ms` es real. En imágenes sueltas
(`source_clock: none`; el campo `timestamp_ms` viene omitido/None en el contrato — no `0`)
no hay tiempo: el motor cae al umbral por-frames
si el pattern set lo define (`mode="frames"`), y si tampoco hay umbral por-frames útil, no
emite progreso. El endpoint no inventa: devuelve lo que haya. La consola mostrará "sin
progreso temporal" cuando `pattern_progress.jsonl` esté vacío o solo traiga `mode=frames`.

## 8. Fuera de alcance

- **Modo en vivo** (streamear progreso por bus/WS y consumirlo en tiempo real). La base
  queda lista: el mismo registro de progreso que hoy se persiste podría publicarse. Es un
  spec aparte si se decide hacerlo.
- **La vista en la consola** (pieza B): detecciones por frame + patrones cumplidos + barra
  de progreso. Ciclo de diseño propio, sobre este cimiento.
- Cambiar la máquina de estados, el modelo de alertas, o los contratos
  `control.pattern_state.v1` / `control.alert.v1`.

## 9. Testing

- **Ratio correcto a mitad de camino**: elapsed = mitad del umbral → `progress == 0.5`, con
  umbrales distintos por condición (CR-01 4000ms vs CR-02 7000ms) para anclar D2.
- **Clamp**: `progress` nunca > 1.0; al confirmarse queda en 1.0 y deja de emitir progreso.
- **Reset**: recuperar el EPP antes del umbral vuelve el progreso a 0 en el próximo episodio.
- **Modo frames**: fuente sin reloj (`timestamp_ms` nulo/0) con `confirm_after_frames` →
  `mode="frames"`, ratio por `hit_count / confirm_after_frames`.
- **Sin progreso posible**: fuente sin tiempo ni umbral por-frames útil → no emite; el
  endpoint devuelve 200 con lista vacía.
- **Endpoint**: 200 con las filas del jsonl, `limit` respetado, run inexistente → 404, run
  sin archivo → 200 vacío.
- **No-regresión**: `pattern_events.jsonl` y `alerts.jsonl` byte-idénticos a antes; la
  máquina de estados y las alertas sin cambios de comportamiento.

## 10. Criterios de éxito

1. Una corrida de video con una condición de riesgo que se cumple genera
   `pattern_progress.jsonl` con registros de progreso creciente 0→1 por-frame durante el
   episodio `candidate`, y ninguno después de confirmarse.
2. `GET /api/runs/{id}/pattern-progress` devuelve esos registros, uniéndose con las
   detecciones por `frame_index`.
3. El progreso es relativo a cada condición (mismo `elapsed`, distinto `progress` para
   CR-01 vs CR-02).
4. `pattern_events.jsonl` y `alerts.jsonl` no cambian; la máquina de estados no cambia de
   comportamiento (tests previos verdes).
5. Fuentes sin reloj no rompen: 200 con lista vacía.
