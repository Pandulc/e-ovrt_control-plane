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

## Estructura: nucleo `eovrt_control` + `eovrt_labs`

El repo separa el nucleo de la logica de control de las herramientas experimentales:

- **`eovrt_control` (nucleo, `eovrt-control`)**: contratos, motor de patrones, replay, evaluacion y export de artefactos. Dependencias livianas (`pydantic`, `pyyaml`, `typer`, `rich`); no requiere torch ni OpenCV.
- **`eovrt_labs` (labs, `eovrt-labs`)**: generador de detecciones con inferencia real (gdino / yolo-ppe / yoloe) y visualizacion de alertas sobre video. Requiere el extra pesado `.[labs]` (torch, transformers, ultralytics, opencv). Depende del nucleo solo para los contratos y el export de alertas.

```bash
pip install -e .            # nucleo (control plane)
pip install -e ".[labs]"    # + generador y visualizacion (torch/opencv/transformers)
pip install -e ".[dev]"     # + pytest y ruff
```

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

## Uso previsto (nucleo)

```bash
eovrt-control replay configs/replay_dbe_cr01_cr02.yaml
```

La corrida crea un directorio bajo `runs/` con `effective_config.yaml`, `pattern_events.jsonl`, `alerts.jsonl`, `alerts.csv`, `metrics.jsonl`, `errors.jsonl` y `summary.json`.

Si los patrones exigen confirmacion multi-frame (`confirm_after_frames > 1` o `confirm_after_ms`) pero las detecciones de personas no traen un `detection_id` estable, el replay emite una advertencia (en consola y en `summary.warnings`): la persistencia temporal no podra confirmar condiciones. Genera detecciones con tracking o publica IDs estables desde el plano de medios.

## Semantica de patrones

- **Asociacion EPP<->persona 1:1**: cada casco/chaleco valida a lo sumo a una persona (la mas cercana al centro de la region esperada). Un EPP ajeno ya no suprime la alerta de otra persona superpuesta.
- **Persistencia temporal**: una condicion se confirma tras `confirm_after_ms`/`confirm_after_frames` de evidencia sostenida y se resuelve tras `resolve_after_ms`/`resolve_after_frames` de evidencia limpia.
- **Expiracion de sujetos ausentes** (opcional): `subject_absent_timeout_ms`/`_frames` resuelve un sujeto que deja de observarse antes de limpiar la condicion.
- **Cooldown de re-alerta** (opcional): `realert_cooldown_ms`/`_frames` evita una nueva alerta por cada ciclo `resolved -> confirmed` dentro de la ventana.

Ver `configs/patterns/cr01_cr02_v1.yaml` (smoke test permisivo) y `cr01_cr02_temporal_eval.yaml` (persistencia + expiracion + cooldown).

## Simulacion temporal CR-01/CR-02

El repo incluye un fixture sintetico que simula la salida del plano de medios (`media.detection.v1`) para probar persistencia temporal:

- `worker_a`: CR-01 persistente, persona sin casco durante 3+ frames.
- `worker_b`: condicion transitoria sin EPP durante 2 frames, no debe alertar.
- `worker_c`: CR-02 persistente, persona sin chaleco durante 3+ frames.

```bash
eovrt-control replay configs/replay_simulated_cr01_cr02_temporal.yaml
eovrt-control evaluate-alerts \
  runs/simulated_cr01_cr02_temporal/alerts.jsonl \
  fixtures/simulated_media/cr01_cr02_temporal/ground_truth.json \
  --output runs/simulated_cr01_cr02_temporal/eval_temporal.json
```

Metricas: alertas esperadas/observadas/matcheadas, missed, unexpected, duplicates, precision, recall, F1 y latencia hasta alerta (frames y ms). El caso feliz da F1 = 1.0.

## Generador de detecciones (labs, sin plano de medios)

Para probar el control plane con inferencia real sin levantar el media plane:

```bash
pip install -e ".[labs]"
eovrt-labs generate-detections \
  --input path/a/imagenes_o_video \
  --output fixtures/hf_media/latest/detections.jsonl \
  --backend gdino \
  --device cuda \
  --track \
  --run-id run_20260702_001 \
  --source-id camera_01
eovrt-control replay configs/replay_hf_detections.yaml
```

Backends disponibles: `gdino` (open-vocabulary, por defecto), `yolo-ppe` (construction-site-safety) y `yoloe`. El CLI expone solo los parametros esenciales; los ajustes finos de deteccion y tracking se pasan por un YAML opcional:

```bash
eovrt-labs generate-detections -i video.mp4 --backend gdino --track \
  --tuning configs/tuning/example.yaml
```

Ver `configs/tuning/example.yaml` para la lista completa de parametros y sus defaults.

## Visualizacion de alertas sobre video (labs)

Cada `replay` deja un `alerts.csv` normalizado (bbox, condicion, severidad, sujeto, clase ausente, rationale) que puede usarse para revisar frames:

```bash
eovrt-labs draw-alert-frames \
  --video path/al/video.mp4 \
  --alerts runs/<control_run>/alerts.csv \
  --output-dir runs/<control_run>/visual_alerts \
  --stage confirm
```

Tambien se aceptan `alerts.jsonl` y `pattern_events.jsonl`. La salida incluye imagenes anotadas, `index.csv` y `alerts_details.csv`.

## Estado

Ver [docs/progress.md](docs/progress.md).
