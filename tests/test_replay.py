import json
from pathlib import Path

import yaml

from eovrt_control.runtime.replay import run_replay


def test_run_replay_writes_summary_and_alerts(tmp_path) -> None:
    input_path = tmp_path / "detections.jsonl"
    output_dir = tmp_path / "runs"
    config_path = tmp_path / "replay.yaml"
    repo_root = Path(__file__).resolve().parents[1]
    patterns_path = repo_root / "configs/patterns/cr01_cr02_v1.yaml"

    event = {
        "run_id": "media-run",
        "unit_id": "unit-1",
        "source": {"source_id": "image.jpg", "source_type": "image", "width": 640, "height": 480},
        "model": {"name": "mock", "device": "cpu"},
        "prompts": {"prompt_set_id": "cr01_cr02_v1"},
        "detections": [
            {
                "detection_id": "p1",
                "label": "person",
                "prompt_id": "person",
                "confidence": 0.9,
                "bbox_xyxy": [100, 100, 220, 420],
            }
        ],
    }
    input_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    config_path.write_text(
        yaml.safe_dump(
            {
                "run": {"id": "control-run", "scenario": "DBE", "name": "test"},
                "input": {"type": "media_jsonl", "path": str(input_path)},
                "patterns": {"file": str(patterns_path), "active_ids": ["CR-01"]},
                "outputs": {"base_dir": str(output_dir)},
            }
        ),
        encoding="utf-8",
    )

    summary = run_replay(config_path)

    assert summary.units_processed == 1
    assert summary.alerts_count == 1
    assert (output_dir / "control-run" / "summary.json").exists()
    assert (output_dir / "control-run" / "alerts.jsonl").read_text(encoding="utf-8").strip()
    assert summary.warnings == []


_IMAGE_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures/simulated_media/image_folder_smoke/detections.jsonl"
)


def _write_config(
    tmp_path: Path,
    *,
    granularity: str | None = None,
    coverage_memory_frames: int | None = None,
    input_path: Path | None = None,
    confirm_after_frames: int | None = None,
    confirm_after_ms: float | None = None,
) -> Path:
    """Escribe un ReplayConfig.

    Por defecto apunta al fixture temporal cr01_cr02_temporal (source_type
    "video"), con un patterns file derivado de cr01_cr02_temporal_eval.yaml
    donde opcionalmente se fuerza `granularity` y/o `coverage_memory_frames`
    en cada patron.

    Si se pasa `input_path` (p. ej. el fixture image_folder_smoke, source_type
    "image"), se usa como base el pattern set no-persistente cr01_cr02_v1.yaml
    (el mismo que usa el BENCH real), y opcionalmente se puede forzar
    `confirm_after_frames` para simular un pattern set con persistencia
    configurada sobre una fuente no temporal.
    """
    repo_root = Path(__file__).resolve().parents[1]
    if input_path is not None:
        fixture_path = input_path
        base_patterns_path = repo_root / "configs/patterns/cr01_cr02_v1.yaml"
    else:
        fixture_path = repo_root / "fixtures/simulated_media/cr01_cr02_temporal/detections.jsonl"
        base_patterns_path = repo_root / "configs/patterns/cr01_cr02_temporal_eval.yaml"

    patterns_doc = yaml.safe_load(base_patterns_path.read_text(encoding="utf-8"))
    for pattern in patterns_doc["pattern_set"]["patterns"]:
        if granularity is not None:
            pattern["granularity"] = granularity
        if coverage_memory_frames is not None:
            pattern["timing"]["coverage_memory_frames"] = coverage_memory_frames
        if confirm_after_frames is not None:
            pattern["timing"]["confirm_after_frames"] = confirm_after_frames
        if confirm_after_ms is not None:
            pattern["timing"]["confirm_after_ms"] = confirm_after_ms

    patterns_path = tmp_path / "patterns.yaml"
    patterns_path.write_text(yaml.safe_dump(patterns_doc), encoding="utf-8")

    output_dir = tmp_path / "runs"
    config_path = tmp_path / "replay.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "run": {"id": "control-run", "scenario": "DBE", "name": "test"},
                "input": {"type": "media_jsonl", "path": str(fixture_path)},
                "patterns": {"file": str(patterns_path), "active_ids": ["CR-01"]},
                "outputs": {"base_dir": str(output_dir)},
            }
        ),
        encoding="utf-8",
    )
    return config_path


def test_replay_reports_degradation_when_scene_pattern_configures_coverage_memory(tmp_path) -> None:
    summary = run_replay(_write_config(tmp_path, granularity="scene", coverage_memory_frames=5))

    assert summary.degraded is True
    assert "coverage_memory_unsupported_scene" in summary.degradation_causes


def test_replay_without_degradation_reports_clean_summary(tmp_path) -> None:
    summary = run_replay(_write_config(tmp_path, granularity="scene"))

    assert summary.degraded is False
    assert summary.degradation_causes == []


def test_replay_reports_no_track_id_for_subject_pattern_without_tracks(tmp_path) -> None:
    summary = run_replay(_write_config(tmp_path, granularity="subject"))

    assert summary.degraded is True
    assert "no_track_id" in summary.degradation_causes


def test_image_source_marks_pattern_evaluation_not_applicable(tmp_path) -> None:
    """ADR-013: sobre imagenes la evaluacion de patrones no aplica."""
    summary = run_replay(_write_config(tmp_path, input_path=_IMAGE_FIXTURE))

    assert summary.pattern_evaluation.state == "not_applicable"
    assert summary.pattern_evaluation.causes == ["non_temporal_source"]


def test_image_source_with_persistence_flags_unreachable_alerts(tmp_path) -> None:
    """Un patron con persistencia sobre imagenes no puede alertar NUNCA."""
    summary = run_replay(
        _write_config(tmp_path, input_path=_IMAGE_FIXTURE, confirm_after_frames=3)
    )

    assert summary.alerts_count == 0
    assert "persistence_unreachable_on_non_temporal_source" in summary.pattern_evaluation.causes


def test_video_source_marks_pattern_evaluation_computed(tmp_path) -> None:
    summary = run_replay(_write_config(tmp_path))  # fixture temporal (video_frame)

    assert summary.pattern_evaluation.state == "computed"
    assert summary.pattern_evaluation.causes == []


def test_missing_input_marks_pattern_evaluation_applicable_not_computed(tmp_path) -> None:
    """Defecto 1: 0 unidades procesadas por archivo de input inexistente no
    puede declararse 'computed' (cero silencioso)."""
    missing_path = tmp_path / "does_not_exist.jsonl"

    summary = run_replay(_write_config(tmp_path, input_path=missing_path))

    assert summary.units_processed == 0
    assert summary.pattern_evaluation.state == "applicable_not_computed"
    assert summary.pattern_evaluation.causes == ["no_units_processed"]


def test_empty_input_marks_pattern_evaluation_applicable_not_computed(tmp_path) -> None:
    """Defecto 1: 0 unidades procesadas por archivo de input vacio no puede
    declararse 'computed' (cero silencioso)."""
    empty_path = tmp_path / "empty.jsonl"
    empty_path.write_text("", encoding="utf-8")

    summary = run_replay(_write_config(tmp_path, input_path=empty_path))

    assert summary.units_processed == 0
    assert summary.pattern_evaluation.state == "applicable_not_computed"
    assert summary.pattern_evaluation.causes == ["no_units_processed"]


def test_mixed_source_types_mark_pattern_evaluation_not_interpretable(tmp_path) -> None:
    """Defecto 2: una corrida que mezcla image y video_frame no tiene una
    semantica temporal coherente."""
    mixed_path = tmp_path / "mixed.jsonl"
    events = [
        {
            "run_id": "media-run",
            "unit_id": "unit-1",
            "source": {
                "source_id": "photo.jpg",
                "source_type": "image",
                "width": 640,
                "height": 480,
            },
            "model": {"name": "mock", "device": "cpu"},
            "prompts": {"prompt_set_id": "cr01_cr02_v1"},
            "detections": [],
        },
        {
            "run_id": "media-run",
            "unit_id": "unit-2",
            "source": {
                "source_id": "video.mp4",
                "source_type": "video_frame",
                "frame_index": 0,
                "timestamp_ms": 0.0,
                "width": 640,
                "height": 480,
            },
            "model": {"name": "mock", "device": "cpu"},
            "prompts": {"prompt_set_id": "cr01_cr02_v1"},
            "detections": [],
        },
    ]
    mixed_path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )

    summary = run_replay(_write_config(tmp_path, input_path=mixed_path))

    assert summary.units_processed == 2
    assert summary.pattern_evaluation.state == "not_interpretable"
    assert summary.pattern_evaluation.causes == ["mixed_source_types"]



def test_ms_thresholds_on_images_are_inert_not_unreachable(tmp_path) -> None:
    """`confirm_after_ms` sobre imagenes NO bloquea la alerta: `_confirmation_met`
    exige timestamp_ms (None en imagenes) y cae al camino por frames. Declarar
    `persistence_unreachable` ahi seria falso -- la corrida SI alerta. La trampa
    real es que el umbral se ignora en silencio."""
    summary = run_replay(
        _write_config(tmp_path, input_path=_IMAGE_FIXTURE, confirm_after_ms=4000.0)
    )

    assert summary.alerts_count > 0, "con confirm_after_ms sobre imagenes el motor alerta"
    causes = summary.pattern_evaluation.causes
    assert "inert_temporal_thresholds" in causes
    assert "persistence_unreachable_on_non_temporal_source" not in causes


def test_frame_threshold_on_images_is_genuinely_unreachable(tmp_path) -> None:
    """`confirm_after_frames > 1` si vuelve la alerta inalcanzable: cada imagen es
    su propia clave de estado, asi que hit_count nunca supera 1."""
    summary = run_replay(
        _write_config(tmp_path, input_path=_IMAGE_FIXTURE, confirm_after_frames=3)
    )

    assert summary.alerts_count == 0
    assert "persistence_unreachable_on_non_temporal_source" in summary.pattern_evaluation.causes


def test_replay_summary_declares_jsonl_source_and_media_run_id(tmp_path) -> None:
    """Spec 41 SS4: el summary declara de que fuente vino la corrida."""
    summary = run_replay(_write_config(tmp_path))

    assert summary.source == "jsonl"
    assert summary.bus_dropped_events == 0
    assert summary.media_run_id == summary.media_run_ids[0]


def test_replay_writes_pattern_progress_jsonl(tmp_path) -> None:
    """Task 3 (pattern-progress): el runtime persiste el progreso que el motor
    ya emite (Tasks 1-2) en runs/<id>/pattern_progress.jsonl."""
    config_path = _write_config(tmp_path)  # fixture temporal (video_frame), estado 'computed'
    run_replay(config_path)

    run_dir = tmp_path / "runs" / "control-run"
    progress_path = run_dir / "pattern_progress.jsonl"
    assert progress_path.exists()
    rows = [json.loads(line) for line in progress_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows, "al menos un frame en candidate debe emitir progreso"
    assert all(r["schema_version"] == "control.pattern_progress.v1" for r in rows)
    assert all(0.0 <= r["progress"] <= 1.0 for r in rows)


def test_replay_summary_carries_experiment_id(tmp_path) -> None:
    """Spec 41 SS8.1: experiment_id viaja de la config al summary."""
    config_path = _write_config(tmp_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["run"]["experiment_id"] = "exp-42"
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    summary = run_replay(config_path)

    assert summary.experiment_id == "exp-42"
