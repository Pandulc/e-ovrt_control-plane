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

### PatternProgress

Progreso parcial de un patron en estado `candidate` (`control.pattern_progress.v1`): un registro por (frame, patron, sujeto) mientras la condicion esta en curso, con el umbral que rige (`mode: time|frames`), lo transcurrido y `progress` 0..1. Se persiste en `pattern_progress.jsonl` y se expone por HTTP (`GET /api/runs/{id}/pattern-progress`). Aditivo: no reemplaza nada.

### ControlMetricSample

Metrica por unidad visual procesada. Incluye cantidad de detecciones, sujetos, cambios de estado, alertas y latencia del plano de control.

### RunSummary

Resumen final de la corrida con conteos agregados y referencias a los artefactos generados.

## Bus de alertas

### control.alert.v1

Topico publicado por `transport/alert_bus.py` (XPUB, bind `tcp://0.0.0.0:5558`, apagado por default via `alert_bus.enabled`). Cada mensaje viaja en el envelope `bus.envelope.v1` (msgpack: `topic`, `key`, `seq` monotono, `ts_publish_ms`, `payload` = los bytes del `AlertEvent` tal cual van al JSONL), espejo del publisher del media-plane (ADR-003). El lifecycle usa el prefijo `run.lifecycle.v1.`. El JSONL es la verdad; el bus solo transporta, y la perdida se detecta por huecos de `seq` del lado del consumidor.

## Servicio HTTP

### ControlRunRequest

Body de `POST /api/runs` (`service/run_request.py`, `extra="forbid"`): `mode` (`replay`|`live`) y exactamente UNO de `config_path` (config por referencia) o `config` (payload completo, ADR-009); `experiment_id` opcional pisa el `run.experiment_id` de la config (ADR-004).

