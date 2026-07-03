"""CLI del plano de control."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console

from eovrt_control.config import load_replay_config
from eovrt_control.evaluation import evaluate_temporal_alerts
from eovrt_control.runtime.replay import run_replay


app = typer.Typer(help="E-OVRT control plane")
console = Console()


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def _parse_variants(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


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


@app.command("export-alerts-csv")
def export_alerts_csv(
    alerts: Path = typer.Option(..., "--alerts", exists=True, readable=True),
    output: Path = typer.Option(..., "--output", "-o"),
    stage: str = typer.Option(
        "all",
        "--stage",
        help="Etapa a exportar: confirm, candidate, both o all.",
    ),
    variants: str = typer.Option(
        "",
        "--variants",
        help="Filtro opcional por variantes separadas por coma.",
    ),
) -> None:
    """Exporta alertas CSV/JSONL a un CSV normalizado con bbox y condicion."""
    from eovrt_control.visualization.alert_frames import export_alert_details_csv

    output_path = export_alert_details_csv(
        alerts,
        output,
        stage=stage,
        variants=_parse_variants(variants),
    )
    console.print(f"CSV de alertas: {output_path}")


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
    variants: str = typer.Option(
        "",
        "--variants",
        help="Filtro opcional por variantes separadas por coma.",
    ),
    image_ext: str = typer.Option("jpg", "--image-ext"),
    line_thickness: int = typer.Option(2, "--line-thickness"),
    details_csv: Path | None = typer.Option(None, "--details-csv"),
) -> None:
    """Dibuja bboxes de alertas sobre frames del video fuente."""
    from eovrt_control.visualization.alert_frames import AlertFrameConfig, draw_alert_frames

    result = draw_alert_frames(
        AlertFrameConfig(
            video_path=video,
            alerts_path=alerts,
            output_dir=output_dir,
            stage=stage,
            variants=_parse_variants(variants),
            image_ext=image_ext,
            line_thickness=line_thickness,
            details_csv_path=details_csv,
        )
    )
    console.print(f"imagenes: {result.images_written}")
    console.print(f"index: {result.index_path}")
    console.print(f"csv detalle: {result.details_csv_path}")


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
    person_confidence: float = typer.Option(0.35, "--person-confidence"),
    helmet_confidence: float = typer.Option(0.25, "--helmet-confidence"),
    vest_confidence: float = typer.Option(0.25, "--vest-confidence"),
    min_box_area: float = typer.Option(100.0, "--min-box-area"),
    track: bool = typer.Option(
        False,
        "--track/--no-track",
        help="IDs estables por IoU en personas (video/secuencias); recomendado para evaluar persistencia temporal.",
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
    max_units: int | None = typer.Option(None, "--max-units"),
    stride: int = typer.Option(1, "--stride", min=1),
    run_id: str | None = typer.Option(None, "--run-id"),
    source_id: str | None = typer.Option(None, "--source-id"),
    prompt_set_id: str | None = typer.Option(None, "--prompt-set-id"),
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
    """Genera detections.jsonl con contrato media.detection.v1 completo."""
    _configure_logging()
    from eovrt_control.perception.generator import GenerationConfig, generate_detections_jsonl

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
    console.print(f"run_id: {result.run_id}")
    console.print(f"unidades: {result.units_written}")
    console.print(f"salida: {result.output_path}")
    if result.alert_frames_output_dir is not None:
        console.print(f"frames alertas: {result.alert_frames_output_dir}")
        console.print(f"index alertas: {result.alert_frames_index_path}")
        console.print(f"csv detalle alertas: {result.alert_frames_details_csv_path}")


if __name__ == "__main__":
    app()

