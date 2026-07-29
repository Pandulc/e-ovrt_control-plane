# ADR-0007 - Corrida live 1:1 con el run del media-plane

> Numeracion: sigue la serie de ADRs del proyecto (ver nota en ADR-0006).

## Contexto

En EBE el motor consume el bus ZeroMQ del media-plane (ADR-003 del proyecto).
Habia que fijar la semantica de corrida del consumidor: sesion continua que
agrega varios runs de medios, o corrida acotada equivalente al replay.

## Decision

La corrida live del control-plane es 1:1 con exactamente un run del media-plane:

- Nace suscripta ANTES de que el run de medios se dispare (PUB/SUB pierde lo
  publicado antes de la suscripcion): el 201 de `POST :8081/api/runs` con
  `mode: live` implica `BusSource` ya suscripto — invariante estructural, no
  procedimental.
- Consume hasta `run.lifecycle.v1/run_finished` (sentinela END) y cierra sola.
- Escribe los mismos artefactos que el replay (`pattern_events.jsonl`,
  `alerts.jsonl`, `metrics.jsonl`, `errors.jsonl`, `summary.json`); el summary
  agrega `media_run_id`, `source: bus|jsonl`, `bus_dropped_events`,
  `degraded` + `degradation_causes[]`.
- Si la entrada mezclo varios runs de medios, se detecta y la corrida se
  degrada con causa (`runtime/core.py:326`); nunca se agrega en silencio.
- El sentinela END de la salida propia (`control.alert.v1`) se emite pase lo
  que pase (`transport/alert_bus.py:163`, en el `finally`).

## Motivo

- Preserva la unidad experimental del informe: cada corrida es reproducible y
  comparable replay<->live con el mismo corte (test de paridad replay<->stream,
  `pattern_events` y `alerts` identicos modulo timestamps de procesamiento).
- Evita semanticas de agregacion nuevas sin valor para R3/R4.
- Es la semantica que el runtime de replay ya tenia (corrida finita con resumen
  al agotar la fuente); el live solo cambia la señal de fin.

## Consecuencias

- Toda corrida live es re-evaluable offline con artefactos identicos (el JSONL
  sigue siendo la verdad, ADR-003).
- El orquestador debe disparar primero el control-plane y despues el
  media-plane; el orden es contrato operativo de la plataforma.
- Desde 2026-07-17, `GET /api/runs?media_run_id=` permite la correlacion
  inversa (consola).
- Las "ventanas de evaluacion propias" que la decision difirio nunca hicieron
  falta: la evaluacion temporal las cubrio por otro camino (doc 52).

## Fuentes

- `docs/decisiones/adr-007-semantica-corrida-1a1.md` (repo `docs`, 2026-07-09)
  — decision original; cierra doc 02 seccion 9.4.
- `docs/specs/41-control-plane.md` seccion 4 (runtime live) — comportamiento
  normativo.
- `docs/operacion/37-plan2-bus-y-live-resultados.md` y
  `38-servicio-minimo-control-plane.md` (repo `docs`) — implementacion
  2026-07-10, E2E 30/30 unidades, gate vacuo reemplazado por el invariante
  estructural del 201.
- Archivo local: `docs/_archive/superpowers/plans/2026-07-10-plan2-bus-y-runtime-live.md`.
- Citas en este repo: `src/eovrt_control/runtime/live.py:1`,
  `src/eovrt_control/runtime/core.py:326`,
  `src/eovrt_control/transport/alert_bus.py:163`.
