# Contratos

## Entrada

El contrato de entrada principal es `DetectionEvent`, compatible con el formato estructurado del plano de medios:

- `run_id`
- `unit_id`
- `source`
- `model`
- `prompts`
- `detections`
- `timing`

Tambien se aceptan algunos campos planos historicos para facilitar replay sobre artefactos previos.

## Salidas

### PatternStateChanged

Evento interno emitido cuando un patron cambia de estado para un sujeto observado.

Estados:

- `inactive`
- `candidate`
- `confirmed`
- `sustained`
- `resolved`

### AlertEvent

Evento interno emitido cuando un patron entra en `confirmed`. No representa accion automatica externa ni fiscalizacion normativa.

### ControlMetricSample

Metrica por unidad visual procesada. Incluye cantidad de detecciones, sujetos, cambios de estado, alertas y latencia del plano de control.

### RunSummary

Resumen final de la corrida con conteos agregados y referencias a los artefactos generados.

