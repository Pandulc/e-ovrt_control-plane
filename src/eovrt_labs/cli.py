"""CLI de labs: generacion de detecciones y visualizacion de alertas."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(help="E-OVRT labs: generacion de detecciones y visualizacion.")
console = Console()


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


@app.command("generate-detections")
def generate_detections(
    input_path: Path = typer.Option(..., "--input", "-i", exists=True, readable=True),
    output: Path = typer.Option(
        Path("fixtures/hf_media/latest/detections.jsonl"),
        "--output",
        "-o",
    ),
    backend: str = typer.Option("gdino", "--backend", "-b", help="gdino, yolo-ppe o yoloe."),
    model_id: str | None = typer.Option(None, "--model-id"),
    device: str = typer.Option("cuda", "--device", help="cuda o cpu."),
    confidence: float = typer.Option(0.25, "--confidence", min=0.0, max=1.0),
    track: bool = typer.Option(
        False,
        "--track/--no-track",
        help="IDs estables por IoU en personas; recomendado para evaluar persistencia temporal en video.",
    ),
    stride: int = typer.Option(1, "--stride", min=1),
    max_units: int | None = typer.Option(None, "--max-units"),
    run_id: str | None = typer.Option(None, "--run-id"),
    source_id: str | None = typer.Option(None, "--source-id"),
    prompt_set_id: str | None = typer.Option(None, "--prompt-set-id"),
    tuning: Path | None = typer.Option(
        None,
        "--tuning",
        exists=True,
        readable=True,
        help="YAML opcional con parametros finos de deteccion y tracking.",
    ),
    progress_interval: int = typer.Option(
        25,
        "--progress-every",
        help="Loguea avance cada N unidades procesadas. Usar 0 para desactivar progreso intermedio.",
    ),
) -> None:
    """Genera detections.jsonl con contrato media.detection.v1 completo."""
    _configure_logging()
    from eovrt_labs.perception.generator import (
        GenerationConfig,
        generate_detections_jsonl,
        load_tuning_config,
    )

    result = generate_detections_jsonl(
        GenerationConfig(
            input_path=input_path,
            output_path=output,
            backend=backend,
            model_id=model_id,
            device=device,
            confidence=confidence,
            track=track,
            stride=stride,
            max_units=max_units,
            run_id=run_id,
            source_id=source_id,
            prompt_set_id=prompt_set_id,
            progress_interval=progress_interval,
            tuning=load_tuning_config(tuning),
        )
    )
    console.print(f"run_id: {result.run_id}")
    console.print(f"unidades: {result.units_written}")
    console.print(f"salida: {result.output_path}")


@app.command("draw-alert-frames")
def draw_alert_frames_command(
    video: Path = typer.Option(..., "--video", exists=True, readable=True),
    alerts: Path = typer.Option(..., "--alerts", exists=True, readable=True),
    output_dir: Path = typer.Option(
        Path("alert_frame_previews"),
        "--output-dir",
    ),
    stage: str = typer.Option(
        "confirm",
        "--stage",
        help="Etapa a dibujar: confirm, candidate, both o all.",
    ),
    image_ext: str = typer.Option("jpg", "--image-ext"),
    line_thickness: int = typer.Option(2, "--line-thickness"),
    details_csv: Path | None = typer.Option(None, "--details-csv"),
) -> None:
    """Dibuja bboxes de alertas sobre frames del video fuente."""
    from eovrt_labs.visualization.frame_drawing import AlertFrameConfig, draw_alert_frames

    result = draw_alert_frames(
        AlertFrameConfig(
            video_path=video,
            alerts_path=alerts,
            output_dir=output_dir,
            stage=stage,
            image_ext=image_ext,
            line_thickness=line_thickness,
            details_csv_path=details_csv,
        )
    )
    console.print(f"imagenes: {result.images_written}")
    console.print(f"index: {result.index_path}")
    console.print(f"csv detalle: {result.details_csv_path}")


if __name__ == "__main__":
    app()
