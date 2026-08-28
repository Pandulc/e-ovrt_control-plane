# ADR-0006 - Estados de aplicabilidad de metricas

> **Nota de serie (2026-07-29):** desde ADR-0006 la numeracion local adopta la
> serie de ADRs del proyecto (`docs/decisiones/` del repo `docs`), que es la que
> el codigo cita como `ADR-006`, `ADR-011`, etc. Ojo: los ADR-0001..0004 locales
> son una serie anterior propia de este repo y NO mapean con adr-001..004 del
> proyecto — las citas `ADR-002/003/004` en el codigo refieren a la serie del
> proyecto (granularidad G0/G1, bus ZeroMQ, experiment_id). No existe ADR-0005
> local: el adr-005 del proyecto (distribucion MQTT) pertenece al modulo de
> distribucion, aun no construido; este repo solo expone su frontera de salida
> (`transport/alert_bus.py`). Los ADR-0006..0013 se materializaron el 2026-07-29
> reconstruidos de las fuentes escritas del proyecto (ver Fuentes de cada uno).

## Contexto

El proyecto decidio (dimension D6, 2026-07-09) que ningun numero se reporta sin
declarar si pudo medirse: el reporte consolidado por `experiment_id` junta los
artefactos de ambos planos y cada metrica lleva un estado de aplicabilidad
literal. El control-plane produce metricas cuya interpretabilidad depende de la
fuente y de los relojes (latencia `t_capture->alert`, evaluacion temporal de
alertas), asi que el vocabulario debia entrar a sus contratos y evaluadores.

## Decision

Toda metrica del control-plane declara su estado de aplicabilidad con un
vocabulario cerrado: `computed | applicable_not_computed | not_applicable |
not_interpretable`, mas una causa (formato `"<estado>"` o `"<estado>:<causa>"`).
Las metricas no aplicables no se omiten ni se cerean: figuran con su estado.

Cableado en este repo:

- `contracts/metrics.py` define el vocabulario (usado por `RunSummary.
  pattern_evaluation`, junto con ADR-013).
- `metrics/latency.py::join_capture_to_alert` declara por alerta: `wallclock`
  single-host -> `computed`; video DBE (`media`) -> `not_interpretable:
  dbe_media_time`; imagenes (`none`) -> `not_applicable:non_temporal_source`;
  two-node sin sincronizacion -> `not_interpretable:clock_skew`; captura
  ausente -> `applicable_not_computed`. La precedencia `none`/`media` antes de
  `two_node` esta verificada por test.
- `evaluation/temporal.py` (evaluate-alerts v2) declara `not_applicable:
  non_temporal_source` cuando todas las alertas carecen de `timestamp_ms`, y
  `not_applicable:no_ground_truth` sin GT. ✎ 2026-08-28: precision — `temporal.py`
  **no emite** `not_applicable:no_ground_truth`; esa causa la emite `report.py` del
  experimental-setup (repo hermano, fuera de este repo). Lo cableado aca es solo
  `non_temporal_source` (`docs/operacion/130`, R-08).
- Extension A2 (2026-07-28, doc 57 seccion 6.7): episodios censurados por
  dimensionamiento del clip (`metric_censored`) — un episodio cuya ventana de
  matching no cabe en el clip no cuenta como fallo de recall/t_alert.

## Motivo

Un cero silencioso es el peor resultado posible de un experimento: distinguir
"no aplico" de "fallo" y de "no se pudo computar" evita leer una corrida
estructuralmente incapaz de medir algo como evidencia de ausencia de riesgo.
Adopta el Camino B del informe (seccion 17.3.13).

## Consecuencias

- El vocabulario es contrato: los consumidores (reporte consolidado del
  experimental-setup, webconsole) agregan sin recalcular.
- Con >= 1 episodio evaluable el recall es un numero real sobre los evaluables;
  los censurados se reportan aparte (`temporal.py:1156`, `:339`).
- Los estados nuevos son aditivos: `schema_version` de los artefactos no cambia.

## Fuentes

- `docs/decisiones/adr-006-reporte-consolidado-aplicabilidad.md` (repo `docs`,
  2026-07-09, decision D6 / Camino B seccion 17.3.13) — decision original.
- `docs/decisiones/estado-de-implementacion-adrs.md` (repo `docs`) — como quedo:
  implementado 2026-07-11, condicional de relojes two-node resuelto por la
  opcion declarativa (`not_interpretable/cross_node_monotonic_clock`), sin NTP.
  ✎ 2026-08-28: el nombre de causa que emite el codigo es **`clock_skew`**
  (`metrics/latency.py:51`; tests `test_latency.py`; tambien `applicability.py` y
  `report.py` del experimental-setup). `cross_node_monotonic_clock` es el nombre
  del doc 39 que no se materializo; en el informe usar `clock_skew`
  (`docs/operacion/130`, R-08).
- `docs/operacion/51-instrumentacion-5b-control-plane.md` (repo `docs`) — join
  `t_capture->alert` con estados; `docs/operacion/52-evaluate-alerts-v2.md` —
  estados en el evaluador temporal; `docs/operacion/57-...duracion-clips.md`
  seccion 6.7 y `58-plan-cierre...md` (A2, censura) — commit `b3c6cc8`.
- Citas en este repo: `src/eovrt_control/contracts/metrics.py:11`,
  `src/eovrt_control/evaluation/temporal.py:339,371,374,406,573,760,943,1092,1156`,
  `src/eovrt_control/metrics/latency.py`.
