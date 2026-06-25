# E-OVRT Control Plane

Plano de control experimental para E-OVRT-VDP. Recibe evidencia perceptual generada por el plano de medios, evalua patrones de riesgo y registra alertas internas trazables.

## Alcance inicial

Esta primera iteracion baja deliberadamente la complejidad operacional para concentrarse en la logica:

- Entrada offline DBE desde `detections.jsonl` del plano de medios.
- Patrones CR-01 y CR-02 sobre evidencia `person`, `helmet` y `vest`.
- Persistencia append-only en JSONL, sin base de datos robusta.
- Estado temporal simple por sujeto observado, con persistencia por `timestamp_ms` y fallback por frames.
- CLI local para replay y generacion de artefactos de corrida.

No incluye servicio HTTP, UI, brokers, base de datos relacional/documental, tracking multi-frame avanzado, zonas, integracion con notificaciones externas ni decisiones normativas automaticas.

## Flujo

```text
media detections.jsonl
        |
        v
DetectionEvent -> PatternEngine -> PatternStateChanged -> AlertEvent
        |                 |
        v                 v
 errors.jsonl       metrics.jsonl / summary.json
```

## Uso previsto

```bash
eovrt-control replay configs/replay_dbe_cr01_cr02.yaml
```

La corrida crea un directorio bajo `runs/` con:

- `effective_config.yaml`
- `pattern_events.jsonl`
- `alerts.jsonl`
- `metrics.jsonl`
- `errors.jsonl`
- `summary.json`

## Simulacion temporal CR-01/CR-02

El repo incluye un fixture sintetico que simula la salida del plano de medios
(`media.detection.v1`) para probar persistencia temporal en el plano de control:

- `worker_a`: CR-01 persistente, persona sin casco durante 3+ frames.
- `worker_b`: condicion transitoria sin EPP durante 2 frames, no debe alertar.
- `worker_c`: CR-02 persistente, persona sin chaleco durante 3+ frames.

Generar fixtures:

```bash
python scripts/generate_temporal_eval_fixture.py
```

Ejecutar replay:

```bash
eovrt-control replay configs/replay_simulated_cr01_cr02_temporal.yaml
```

Evaluar alertas contra ground truth temporal debil:

```bash
eovrt-control evaluate-alerts \
  runs/simulated_cr01_cr02_temporal/alerts.jsonl \
  fixtures/simulated_media/cr01_cr02_temporal/ground_truth.json \
  --output runs/simulated_cr01_cr02_temporal/eval_temporal.json
```

Metricas principales:

- alertas esperadas/observadas/matcheadas;
- missed alerts;
- unexpected alerts;
- duplicate alerts;
- precision, recall y F1;
- latencia promedio desde primera evidencia hasta alerta, en frames y milisegundos cuando hay timestamps.

## Estado

Ver [docs/progress.md](docs/progress.md).

