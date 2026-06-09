# E-OVRT Control Plane

Plano de control experimental para E-OVRT-VDP. Recibe evidencia perceptual generada por el plano de medios, evalua patrones de riesgo y registra alertas internas trazables.

## Alcance inicial

Esta primera iteracion baja deliberadamente la complejidad operacional para concentrarse en la logica:

- Entrada offline DBE desde `detections.jsonl` del plano de medios.
- Patrones CR-01 y CR-02 sobre evidencia `person`, `helmet` y `vest`.
- Persistencia append-only en JSONL, sin base de datos robusta.
- Estado temporal simple por persona observada en una unidad visual.
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

## Estado

Ver [docs/progress.md](docs/progress.md).

