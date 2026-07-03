#!/usr/bin/env python3
"""Genera detections.jsonl con un modelo HF/YOLO para probar el plano de control.

Ejemplo (WSL, GPU):

    cd e-ovrt_control-plane
    source .venv/bin/activate
    pip install -e ".[perception]"
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126

    python scripts/generate_hf_detections.py \\
      --input ../e-ovrt_datasets/datasets/raw/construction_ppe \\
      --output fixtures/hf_media/construction_ppe/detections.jsonl \\
      --backend yolo-ppe \\
      --device cuda \\
      --max-units 30

    eovrt-control replay configs/replay_hf_detections.yaml
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from eovrt_control.perception.generator import (  # noqa: E402
    GenerationConfig,
    generate_detections_jsonl,
)

app = typer.Typer(add_completion=False, help="Genera detections.jsonl para replay del control plane.")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def _parse_variants(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@app.command()
def main(
    input_path: Path = typer.Option(..., "--input", "-i", exists=True, readable=True),
    output: Path = typer.Option(
        Path("fixtures/hf_media/latest/detections.jsonl"),
        "--output",
        "-o",
        help="Ruta del JSONL de salida (media.detection.v1).",
    ),
    backend: str = typer.Option(
        "yolo-ppe",
        "--backend",
        "-b",
        help="Backend: yolo-ppe, yoloe o gdino.",
    ),
    model_id: str | None = typer.Option(
        None,
        "--model-id",
        help="Override del modelo (ej. IDEA-Research/grounding-dino-tiny).",
    ),
    device: str = typer.Option("cuda", "--device", help="cuda o cpu."),
    confidence: float = typer.Option(0.25, "--confidence", min=0.0, max=1.0),
    person_confidence: float = typer.Option(0.35, "--person-confidence", min=0.0, max=1.0),
    helmet_confidence: float = typer.Option(0.25, "--helmet-confidence", min=0.0, max=1.0),
    vest_confidence: float = typer.Option(0.25, "--vest-confidence", min=0.0, max=1.0),
    min_box_area: float = typer.Option(100.0, "--min-box-area"),
    track: bool = typer.Option(
        False,
        "--track/--no-track",
        help="IDs estables por IoU en personas; usar al evaluar persistencia temporal en video.",
    ),
    track_iou_threshold: float = typer.Option(0.20, "--track-iou-threshold"),
    track_max_lost_ms: float = typer.Option(1500.0, "--track-max-lost-ms"),
    track_max_lost_frames: int = typer.Option(30, "--track-max-lost-frames"),
    track_center_gate_ratio: float = typer.Option(0.75, "--track-center-gate-ratio"),
    track_area_ratio_min: float = typer.Option(0.35, "--track-area-ratio-min"),
    track_min_score: float = typer.Option(0.25, "--track-min-score"),
    track_appearance: bool = typer.Option(
        True,
        "--track-appearance/--no-track-appearance",
        help="Usa una firma visual liviana del torso para sostener IDs en cruces/oclusiones.",
    ),
    track_appearance_weight: float = typer.Option(0.45, "--track-appearance-weight"),
    track_appearance_min_similarity: float = typer.Option(
        0.30,
        "--track-appearance-min-similarity",
    ),
    nms_iou_person: float = typer.Option(0.65, "--nms-iou-person"),
    nms_iou_epp: float = typer.Option(0.50, "--nms-iou-epp"),
    model_iou: float = typer.Option(0.50, "--model-iou"),
    image_size: int = typer.Option(640, "--image-size"),
    progress_interval: int = typer.Option(
        25,
        "--progress-every",
        help="Loguea avance cada N unidades procesadas. Usar 0 para desactivar progreso intermedio.",
    ),
    max_units: int | None = typer.Option(None, "--max-units", help="Limite de frames/imagenes."),
    stride: int = typer.Option(1, "--stride", min=1),
    run_id: str | None = typer.Option(None, "--run-id"),
    source_id: str | None = typer.Option(None, "--source-id"),
    prompt_set_id: str | None = typer.Option(None, "--prompt-set-id"),
    write_replay_config: Path | None = typer.Option(
        None,
        "--write-replay-config",
        help="Escribe un YAML de replay apuntando al JSONL generado.",
    ),
    draw_alerts_from: Path | None = typer.Option(
        None,
        "--draw-alert-frames-from",
        help="CSV/JSONL de alertas para dibujar frames al finalizar la generacion.",
    ),
    alert_frames_output_dir: Path | None = typer.Option(None, "--alert-frames-output-dir"),
    alert_frames_stage: str = typer.Option(
        "confirm",
        "--alert-frames-stage",
        help="Etapa a dibujar: confirm, candidate, both o all.",
    ),
    alert_frames_variants: str = typer.Option(
        "",
        "--alert-frames-variants",
        help="Filtro opcional por variantes separadas por coma.",
    ),
    alert_frames_image_ext: str = typer.Option("jpg", "--alert-frames-image-ext"),
    alert_frames_line_thickness: int = typer.Option(2, "--alert-frames-line-thickness"),
    alert_frames_details_csv: Path | None = typer.Option(None, "--alert-frames-details-csv"),
) -> None:
    """Inferencia perceptual liviana → detections.jsonl compatible con el control plane."""
    result = generate_detections_jsonl(
        GenerationConfig(
            input_path=input_path,
            output_path=output,
            backend=backend,
            model_id=model_id,
            device=device,
            confidence=confidence,
            person_confidence=person_confidence,
            helmet_confidence=helmet_confidence,
            vest_confidence=vest_confidence,
            min_box_area_px=min_box_area,
            track=track,
            track_iou_threshold=track_iou_threshold,
            track_max_lost_ms=track_max_lost_ms,
            track_max_lost_frames=track_max_lost_frames,
            track_center_gate_ratio=track_center_gate_ratio,
            track_area_ratio_min=track_area_ratio_min,
            track_min_score=track_min_score,
            track_appearance=track_appearance,
            track_appearance_weight=track_appearance_weight,
            track_appearance_min_similarity=track_appearance_min_similarity,
            nms_iou_person=nms_iou_person,
            nms_iou_epp=nms_iou_epp,
            model_iou=model_iou,
            image_size=image_size,
            progress_interval=progress_interval,
            max_units=max_units,
            stride=stride,
            run_id=run_id,
            source_id=source_id,
            prompt_set_id=prompt_set_id,
            alert_frames_alerts_path=draw_alerts_from,
            alert_frames_output_dir=alert_frames_output_dir,
            alert_frames_stage=alert_frames_stage,
            alert_frames_variants=_parse_variants(alert_frames_variants),
            alert_frames_image_ext=alert_frames_image_ext,
            alert_frames_line_thickness=alert_frames_line_thickness,
            alert_frames_details_csv_path=alert_frames_details_csv,
        )
    )
    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"unidades: {result.units_written}")
    typer.echo(f"salida: {result.output_path}")
    if result.alert_frames_output_dir is not None:
        typer.echo(f"frames alertas: {result.alert_frames_output_dir}")
        typer.echo(f"index alertas: {result.alert_frames_index_path}")
        typer.echo(f"csv detalle alertas: {result.alert_frames_details_csv_path}")

    if write_replay_config is not None:
        replay_yaml = _render_replay_config(result.output_path, result.run_id)
        write_replay_config.parent.mkdir(parents=True, exist_ok=True)
        write_replay_config.write_text(replay_yaml, encoding="utf-8")
        typer.echo(f"replay config: {write_replay_config}")


def _render_replay_config(detections_path: Path, run_id: str) -> str:
    rel = detections_path.as_posix()
    return f"""run:
  id: {run_id}
  scenario: DBE
  name: replay_hf_detections
  description: "Replay sobre detections.jsonl generado por scripts/generate_hf_detections.py"

input:
  type: media_jsonl
  path: {rel}

patterns:
  file: patterns/cr01_cr02_v1.yaml
  active_ids:
    - CR-01
    - CR-02

outputs:
  base_dir: ../runs
  save_pattern_events_jsonl: true
  save_alerts_jsonl: true
  save_metrics_jsonl: true
  save_errors_jsonl: true
  save_summary_json: true

logging:
  level: INFO
"""


if __name__ == "__main__":
    app()
