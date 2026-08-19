# Arquitectura

## Rol del plano de control

El plano de control no ejecuta inferencia visual. Su responsabilidad es consumir evidencia normalizada del plano de medios, evaluar patrones definidos para condiciones de riesgo y producir eventos internos trazables.

El plano de control opera en dos caminos: **replay offline DBE** (relee `detections.jsonl` del plano de medios; el camino original, que permitio validar logica de patrones y contratos sin infraestructura) y **runtime live EBE** (`runtime/live.py` + `sources/bus.py`, ADR-0007: consume el bus ZeroMQ del media-plane con corrida 1:1 que cierra por `run_finished`). Ademas expone un **servicio HTTP** FastAPI en `:8081` (`eovrt-control serve`, ADR-0008) desde el cual la webconsole y el runner disparan ambos tipos de corrida.

## Componentes

El nucleo `eovrt_control` (paquete liviano, CLI `eovrt-control`) contiene:

- `sources`: lectura de eventos del plano de medios desde JSONL y desde el bus ZeroMQ (`bus.py`), mas tracking opcional de personas (`tracking.py`).
- `contracts`: modelos Pydantic para eventos de entrada, cambios de estado, alertas, progreso de patron, metricas y errores.
- `engine`: motor de patrones y evaluadores por familia de patron.
- `sinks`: escritura append-only en JSONL, resumen de corrida y export de `alerts.csv`.
- `runtime`: orquestacion de una corrida completa (replay y live).
- `service`: servicio HTTP FastAPI (`serve`, `:8081`): dispara corridas, expone estado y artefactos.
- `transport`: publisher del bus de alertas (`control.alert.v1`, XPUB `:5558`, apagado por default).
- `evaluation`: evaluacion temporal de alertas contra ground truth (`evaluate-alerts`).
- `metrics`: instrumentacion de latencia `t_capture->alert` (ADR-0006).
- `tools`: utilitarios offline (p. ej. `track_detections`).
- `cli`: interfaz local para replay, live, evaluacion, export de alertas y serve.

Las herramientas experimentales viven en un paquete separado `eovrt_labs` (CLI `eovrt-labs`, extra `.[labs]`): el generador de detecciones con inferencia real y la visualizacion de alertas sobre video. `eovrt_labs` depende del nucleo solo para los contratos y el export de alertas; el nucleo no depende de labs. Asi el plano de control se instala y ejecuta sin torch ni OpenCV.

## Cadena conceptual

```text
condicion -> patron -> evidencia perceptual -> estado de patron -> alerta interna
```

Una deteccion no es una alerta. Una alerta se emite solo cuando el motor confirma que la evidencia satisface el patron configurado.

## Evaluacion CR-01/CR-02

El plano de medios produce evidencia positiva para `person`, `helmet` y `vest`. Por eso, el plano de control infiere ausencia de EPP de forma indirecta:

1. toma cada deteccion `person` como sujeto;
2. define una region esperada dentro de la caja de la persona;
3. asocia el EPP requerido cuyo centro cae en esa region, con matching **1:1 de cardinalidad maxima**: cada casco/chaleco valida a lo sumo a una persona, cubriendo a la mayor cantidad posible de sujetos y prefiriendo las asociaciones mas cercanas. Un EPP ajeno no puede cubrir a dos personas a la vez, y con cajas superpuestas no le "roba" el EPP a su dueno si hay otro disponible;
4. si el sujeto queda sin EPP asociado, genera evidencia positiva del patron `person_without_*`.

Esta aproximacion es intencionalmente simple. Es apta para primeras corridas DBE, pero no reemplaza tracking, segmentacion, calibracion por escena ni validacion estadistica posterior.

## Persistencia temporal

El motor de patrones confirma y resuelve condiciones mediante ventanas basadas en `timestamp_ms` cuando la evidencia del plano de medios lo provee. Los umbrales `confirm_after_ms` y `resolve_after_ms` son el criterio preferido para video; `confirm_after_frames` y `resolve_after_frames` quedan como fallback para fixtures o artefactos historicos sin timestamp.

Dos mecanismos opcionales complementan el ciclo de estados:

- **Expiracion de sujetos ausentes** (`subject_absent_timeout_ms`/`_frames`): un sujeto en estado activo que deja de observarse mas alla del timeout pasa a `resolved`, evitando estados colgados indefinidamente.
- **Cooldown de re-alerta** (`realert_cooldown_ms`/`_frames`): tras un ciclo `resolved -> confirmed`, no se emite una nueva alerta para el mismo (patron, sujeto) dentro de la ventana de cooldown; el cambio de estado si se registra. Reduce alertas duplicadas por parpadeos de deteccion. **La plataforma no lo usa** (ADR-0011): el pattern set oficial `cr01_cr02_v2` emite en cada confirmacion y las `re_alerts` no se cuentan como falsos positivos; queda como capacidad del motor.

Ambos son opt-in (sin valor configurado, el comportamiento es el historico).

El plano de control no realiza tracking por default. La via preferida sigue siendo que el plano de medios publique un identificador estable de sujeto o track dentro de la evidencia normalizada. Como capacidad de plataforma existe un **tracking opcional de personas** en la entrada (`sources/tracking.py`, opt-in con `input.track_persons: true`, p. ej. `configs/smoke_ebe_b.yaml`): habilita identidad por sujeto cuando la evidencia no trae ids estables. Las metricas MOT quedan fuera de alcance. Cuando los patrones exigen persistencia multi-frame pero las personas solo traen ids autogenerados (`det_NNN`), el replay lo advierte en `summary.warnings`.

## Persistencia de artefactos

Se usa JSONL append-only para conservar bajo el costo de cambio. Una base de datos mas robusta puede incorporarse luego cuando la logica este estabilizada y haya necesidades claras de consulta, retencion o integracion.

