# Evidencia perceptual generada con modelos HF/YOLO

Este directorio recibe `detections.jsonl` producidos por el generador liviano del
plano de control, sin pasar por el plano de medios completo.

La salida cumple el contrato `media.detection.v1` alineado al media-plane:
`bbox_norm_xyxy`, `area_px`, `model_name` por detección, `source_type=video_frame`,
`unit_id=frame_000123`, timing con `inference_ms` / `postprocess_ms` / `write_ms` /
`total_ms`, y serialización con `exclude_none=True`.

## Generar evidencia

```bash
cd e-ovrt_control-plane
source .venv/bin/activate
pip install -e ".[perception]"
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126

# Detector cerrado construction-PPE (Hardhat / Safety Vest → helmet / vest)
eovrt-control generate-detections \
  --input ../e-ovrt_datasets/datasets/raw/construction_ppe \
  --output fixtures/hf_media/latest/detections.jsonl \
  --backend yolo-ppe \
  --device cuda \
  --max-units 30

# YOLOE 26s (prompt set bench v2)
eovrt-control generate-detections \
  --input ../e-ovrt_datasets/datasets/video/video1.avi \
  --output fixtures/hf_media/video_intel/detections.jsonl \
  --backend yoloe \
  --device cuda \
  --stride 2 \
  --track \
  --run-id run_20260626_001 \
  --source-id camera_01 \
  --prompt-set-id cr01_cr02_bench_v2

# Open-vocabulary con Grounding DINO tiny
eovrt-control generate-detections \
  --input path/a/imagenes_o_video.mp4 \
  --backend gdino \
  --device cuda
```

## Replay en el control plane

```bash
eovrt-control replay configs/replay_hf_detections.yaml
# Video Intel (video1.avi):
eovrt-control replay configs/replay_hf_video_intel.yaml
```

## Reporte de resultados (video Intel)

Ver [`docs/reportes/REPORTE_HF_VIDEO_INTEL_20260626.md`](../../docs/reportes/REPORTE_HF_VIDEO_INTEL_20260626.md) para el análisis completo del run end-to-end (976 frames, 82 alertas, interpretación CR-01/CR-02).

## Backends

| Backend | Modelo default | Cuándo usarlo |
|---|---|---|
| `yolo-ppe` | `models/yolo/construction-site-safety.pt` (descarga automatica) | Person / Hardhat / Safety Vest; rapido en GPU |
| `yoloe` | `models/yoloe/yoloe-26s-seg.pt` (descarga o copia desde media-plane) | Prompts abiertos person/helmet/vest; alineado a BENCH v2 |
| `gdino` | `IDEA-Research/grounding-dino-tiny` | Prompts abiertos; más flexible, algo más lento |

Todos emiten etiquetas canónicas `person`, `helmet` y `vest` compatibles con CR-01/CR-02.

**IDs de detección:**
- Por defecto (`--no-track`): `det_000001`, `det_000002`, … reinician en cada frame. Sirve para PoC y validar el contrato.
- Con `--track` en video/secuencias: las **personas** reciben `subject_001`, `subject_002`, … estables entre frames (IoU). Recomendado para evaluar persistencia temporal del control plane; el replay usa `detection_id` de la persona para armar `subject_key`.
- Cascos/chalecos siguen con `det_*` por frame; el control plane los asocia espacialmente a la persona.
