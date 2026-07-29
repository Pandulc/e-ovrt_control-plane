# ADR-0010 - Secuenciacion: plataforma primero, evaluacion despues

> Numeracion: sigue la serie de ADRs del proyecto (ver nota en ADR-0006).
> Este ADR no esta citado por el codigo de este repo; se materializa para
> completar la serie local y porque spec 41 seccion 7 lo invoca como regla de
> calibracion del pattern set `cr01_cr02_v2`.

## Contexto

Los documentos previos del proyecto (docs 02/07) argumentaban "clip bench
primero" por lead time. El usuario decidio (2026-07-09) invertir el orden de
ejecucion del proyecto.

## Decision

Dos tramos secuenciales:

1. Tramo plataforma (primero): ambos planos como servicios, bus media->control,
   config centralizada, corridas trazables (`experiment_id`) y toda la
   infraestructura de instrumentacion (hitos por alerta, percentiles, reporte
   consolidado con estados de aplicabilidad). Criterio de salida: la cadena
   completa corre sobre fuentes de prueba con cada numero reconstruible hasta
   su configuracion.
2. Tramo evaluacion (despues): dataset curado con GT temporal por escenario
   (spec 43) y las campañas que dependen de el. Disparador: el cierre del
   spec 44 (experimental-setup) — la distribucion (spec 45) no es prerequisito
   y queda para lo ultimo. El material crudo de videos se arma en paralelo.

## Motivo

El GT se diseña una sola vez, contra una plataforma estable (contratos, pattern
set v2, evaluador y matching definitivos), en lugar de anotarse dos veces o
contra un motor que aun cambia. El riesgo de comprimir R3/D1/escritura hacia el
final se acepta con mitigaciones: spec 43 congelado y ejecutable, tramites en
paralelo, escritura incremental.

## Consecuencias

Para este repo:

- Los parametros del pattern set v2 (`confirm_after_ms` 4000/7000, histeresis
  2000/3000) son valores iniciales declarados dentro de las bandas del informe,
  calibrables SOLO en el tramo de evaluacion — nunca entre corridas de una
  misma comparacion (spec 41 seccion 7).
- El fixture temporal sintetico siguio siendo el gate de regresion del motor
  durante todo el tramo plataforma.
- La cola de specs 40 -> 41 -> 42 -> 44 se ejecuto completa en este orden; el
  tooling del spec 43 (video-gt-lab) se construyo al cierre del 44.

## Fuentes

- `docs/decisiones/adr-010-secuenciacion-plataforma-primero.md` (repo `docs`,
  2026-07-09, decision del usuario; supera docs 02/03/04 e indice 00) —
  decision original, riesgo aceptado y mitigaciones.
- `docs/decisiones/estado-de-implementacion-adrs.md` (repo `docs`) — cumplida
  tal cual (docs 37/38/39/51/52/53; doc 54 video-gt-lab).
- `docs/specs/41-control-plane.md` seccion 7 — regla de calibracion del
  pattern set v2 que remite a este ADR.
