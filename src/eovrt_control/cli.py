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
    for warning in summary.warnings:
        console.print(f"[yellow]Advertencia:[/yellow] {warning}")


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


@app.command("export-alerts-csv")
def export_alerts_csv(
    alerts: Path = typer.Option(..., "--alerts", exists=True, readable=True),
    output: Path = typer.Option(..., "--output", "-o"),
    stage: str = typer.Option(
        "all",
        "--stage",
        help="Etapa a exportar: confirm, candidate, both o all.",
    ),
) -> None:
    """Exporta alertas CSV/JSONL a un CSV normalizado con bbox y condicion."""
    from eovrt_control.sinks.alerts_csv import export_alert_details_csv

    output_path = export_alert_details_csv(alerts, output, stage=stage)
    console.print(f"CSV de alertas: {output_path}")


if __name__ == "__main__":
    app()
