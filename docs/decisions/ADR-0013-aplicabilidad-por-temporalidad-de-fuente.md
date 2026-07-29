# ADR-0013 - Aplicabilidad por temporalidad de la fuente

> Numeracion: sigue la serie de ADRs del proyecto (ver nota en ADR-0006).

## Contexto

Correr el motor sobre datasets de imagenes (BENCH) producia ceros silenciosos:
con `confirm_after_frames > 1` la alerta es estructuralmente inalcanzable
(`hit_count` nunca supera 1) y una corrida sin alertas se podia leer como
"no hubo riesgo". Habia que decidir entre rechazar esas corridas, pedirle al
operador que declare la temporalidad, o detectarla.

## Decision

La temporalidad se DETECTA, no se configura:

1. La señal ya existe en el contrato: `DetectionEventSource.source_type`.
   `image` => fuente no temporal (unidades independientes, `timestamp_ms` y
   `frame_index` en None, `source_clock: none`); `video_frame` => fuente
   temporal (`media` para video DBE, `wallclock` para RTSP). No se agrega
   ningun campo.
2. Sobre fuentes no temporales la evaluacion de patrones (episodio,
   persistencia, histeresis, resolucion, re-alertas) se declara
   `not_applicable / non_temporal_source` en `RunSummary.pattern_evaluation`
   (vocabulario de ADR-0006). La corrida NO se rechaza: sigue valiendo como
   smoke de contrato y diagnostico espacial.
3. La deteccion distingue ademas: sin unidades procesadas =>
   `applicable_not_computed / no_units_processed`; `source_type` mezclados =>
   `not_interpretable / mixed_source_types`.
4. Los umbrales temporales del pattern set caen en dos categorias distintas
   sobre fuente no temporal: `confirm_after_frames > 1` vuelve la alerta
   inalcanzable => causa `persistence_unreachable_on_non_temporal_source`;
   los umbrales en ms (`confirm_after_ms`, `resolve_after_*`,
   `subject_absent_timeout_*`) se ignoran => causa `inert_temporal_thresholds`.
5. La superficie de seleccion (webconsole/runner) lo comunica ANTES de correr.

## Motivo

1. La division ya existia en las specs (imagenes -> percepcion espacial; video
   -> patrones con GT temporal; RTSP -> patrones + end-to-end); este ADR la
   detecta y la aplica, no la inventa.
2. Un cero silencioso es el peor resultado posible de un experimento; la
   plataforma ya distingue `not_applicable` de `computed` justamente para esto.
3. Detectar, no configurar: pedir la declaracion al operador duplicaria una
   verdad que el sistema ya conoce y permitiria que se contradigan.
4. No rechazar preserva el valor diagnostico del BENCH.

## Consecuencias

- Regla de uso: los datasets de imagenes NUNCA validan el motor — validan
  percepcion y asociacion espacial.
- Evidencia medida que motivo la decision y quedo como comportamiento cableado:
  137 eventos de patron / 0 alertas sobre el BENCH de imagenes (doc 33
  seccion 4).
- `source_clock` gana el valor `none`; el fixture y el gate temporal del motor
  no cambian (son temporales).

## Fuentes

- `docs/decisiones/adr-013-aplicabilidad-por-temporalidad-de-fuente.md`
  (repo `docs`, 2026-07-09, decision del usuario) — decision completa (5
  puntos), fundamento e impacto.
- `docs/specs/41-control-plane.md` seccion 2.3 — la misma decision en el spec
  del plano.
- `docs/operacion/33-fase0-rerun-motor-mati.md` (repo `docs`) — la medicion
  (137 eventos / 0 alertas) que expuso el problema.
- Archivo local: `docs/_archive/superpowers/plans/2026-07-09-g0-granularidad-escena.md`
  (Task 9: deteccion automatica de fuente no temporal).
- Citas en este repo: `src/eovrt_control/contracts/metrics.py:12,70`,
  `src/eovrt_control/runtime/core.py:128`, `tests/test_replay.py:142`,
  `configs/_archive/fase0_dbe_gdino_bench_rerun.yaml:6`,
  `configs/_archive/fase0_dbe_gdino_bench_persistence_probe.yaml:8`,
  `configs/_archive/patterns/cr01_cr02_persistence_probe.yaml:8`.
