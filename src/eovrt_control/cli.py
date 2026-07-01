"""CLI del plano de control."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from eovrt_control.config import load_replay_config
from eovrt_control.evaluation import evaluate_temporal_alerts
from eovrt_control.runtime.replay import run_replay


app = typer.Typer(help="E-OVRT control plane")
console = Console()


@app.command()
def validate_config(config: Path) -> None:
    """Valida una configuracion de replay."""
    load_replay_config(config)
    console.print(f"Configuracion valida: {config}")


@app.command()
def replay(config: Path) -> None:
    """Ejecuta replay offline sobre eventos JSONL del plano de medios."""
    summary = run_replay(config)
    console.print(f"Control run: {summary.control_run_id}")
    console.print(f"Unidades procesadas: {summary.units_processed}")
    console.print(f"Alertas: {summary.alerts_count}")
    console.print(f"Resumen: {summary.output_files['summary']}")


@app.command("evaluate-alerts")
def evaluate_alerts(
    alerts: Path,
    ground_truth: Path,
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Archivo JSON donde guardar la evaluacion temporal.",
    ),
) -> None:
    """Evalua alertas contra ground truth temporal debil."""
    evaluation = evaluate_temporal_alerts(alerts, ground_truth, output)
    console.print(f"Escenario: {evaluation.scenario_id}")
    console.print(f"Esperadas: {evaluation.expected_alerts_count}")
    console.print(f"Observadas: {evaluation.observed_alerts_count}")
    console.print(f"Matcheadas: {evaluation.matched_alerts_count}")
    console.print(f"Missed: {evaluation.missed_alerts_count}")
    console.print(f"Unexpected: {evaluation.unexpected_alerts_count}")
    console.print(f"Precision: {evaluation.precision:.3f}")
    console.print(f"Recall: {evaluation.recall:.3f}")
    console.print(f"F1: {evaluation.f1:.3f}")
    if output is not None:
        console.print(f"Evaluacion: {output}")


@app.command("generate-detections")
def generate_detections(
    input_path: Path = typer.Option(..., "--input", "-i", exists=True, readable=True),
    output: Path = typer.Option(
        Path("fixtures/hf_media/latest/detections.jsonl"),
        "--output",
        "-o",
    ),
    backend: str = typer.Option("yolo-ppe", "--backend", "-b"),
    model_id: str | None = typer.Option(None, "--model-id"),
    device: str = typer.Option("cuda", "--device"),
    confidence: float = typer.Option(0.25, "--confidence"),
    min_box_area: float = typer.Option(100.0, "--min-box-area"),
    track: bool = typer.Option(
        False,
        "--track/--no-track",
        help="IDs estables por IoU en personas (video/secuencias); recomendado para evaluar persistencia temporal.",
    ),
    max_units: int | None = typer.Option(None, "--max-units"),
    stride: int = typer.Option(1, "--stride", min=1),
    run_id: str | None = typer.Option(None, "--run-id"),
    source_id: str | None = typer.Option(None, "--source-id"),
    prompt_set_id: str | None = typer.Option(None, "--prompt-set-id"),
) -> None:
    """Genera detections.jsonl con contrato media.detection.v1 completo."""
    from eovrt_control.perception.generator import GenerationConfig, generate_detections_jsonl

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
    console.print(f"run_id: {result.run_id}")
    console.print(f"unidades: {result.units_written}")
    console.print(f"salida: {result.output_path}")


if __name__ == "__main__":
    app()

