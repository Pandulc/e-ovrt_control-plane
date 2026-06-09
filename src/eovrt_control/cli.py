"""CLI del plano de control."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from eovrt_control.config import load_replay_config
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


if __name__ == "__main__":
    app()

