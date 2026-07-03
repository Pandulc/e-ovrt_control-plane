# Runbook: comparativa de alertas con gdino (antes/despues)

Objetivo: medir el efecto de las mejoras del motor (asociacion 1:1, expiracion,
cooldown) sobre alertas reales, usando el generador de labs con `gdino`. Requiere
GPU y el extra pesado.

## Requisitos

```bash
pip install -e ".[labs]"
# torch con CUDA segun tu entorno, p. ej.:
# pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
```

## 1. Generar detecciones con gdino + tracking

```bash
eovrt-labs generate-detections \
  --input path/al/video.mp4 \
  --output fixtures/hf_media/latest/detections.jsonl \
  --backend gdino \
  --device cuda \
  --track \
  --run-id run_gdino_001 \
  --source-id camera_01
```

El `--track` es necesario para que los patrones con `confirm_after_frames > 1`
puedan confirmar (IDs de sujeto estables). Sin tracking, el replay lo advertira.
Ajustes finos opcionales: `--tuning configs/tuning/example.yaml`.

## 2. Replay con persistencia temporal

```bash
eovrt-control replay configs/replay_hf_temporal.yaml
```

Usa `patterns/cr01_cr02_temporal_eval.yaml` (confirm 3 frames / 1000 ms, expiracion
3000 ms, cooldown 5000 ms). Anota el `control_run_id` que imprime.

## 3. Analizar / comparar

```bash
# Resumen de una corrida
python scripts/analyze_alerts.py runs/<control_run>/alerts.jsonl

# Comparar dos corridas (p. ej. cr01_cr02_v1 permisivo vs temporal_eval)
python scripts/analyze_alerts.py runs/<antes>/alerts.jsonl runs/<despues>/alerts.jsonl
```

Metricas: total de alertas, desglose por condicion, pares (condicion, sujeto)
unicos, frames con alerta y sujetos con re-alertas. Con las mejoras se espera
menos re-alertas por sujeto y menos falsos CR-02 por asociacion de EPP ajeno.

## Regresion sintetica (sin GPU)

El caso conocido con ground truth debe seguir dando F1 = 1.0:

```bash
eovrt-control replay configs/replay_simulated_cr01_cr02_temporal.yaml
eovrt-control evaluate-alerts \
  runs/simulated_cr01_cr02_temporal/alerts.jsonl \
  fixtures/simulated_media/cr01_cr02_temporal/ground_truth.json
```

Tambien cubierto automaticamente por `tests/test_temporal_evaluation.py`.
