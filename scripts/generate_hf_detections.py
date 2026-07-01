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

from eovrt_control.perception.generator import GenerationConfig, generate_detections_jsonl

app = typer.Typer(add_completion=False, help="Genera detections.jsonl para replay del control plane.")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


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
    min_box_area: float = typer.Option(100.0, "--min-box-area"),
    track: bool = typer.Option(
        False,
        "--track/--no-track",
        help="IDs estables por IoU en personas; usar al evaluar persistencia temporal en video.",
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
            min_box_area_px=min_box_area,
            track=track,
            max_units=max_units,
            stride=stride,
            run_id=run_id,
            source_id=source_id,
            prompt_set_id=prompt_set_id,
        )
    )
    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"unidades: {result.units_written}")
    typer.echo(f"salida: {result.output_path}")

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
