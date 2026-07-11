import json
from pathlib import Path

import yaml

from eovrt_control.config import load_replay_config
from eovrt_control.runtime.core import RunProgress, prepare_run
from eovrt_control.runtime.replay import run_replay, run_replay_from_config

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATTERNS = _REPO_ROOT / "configs/patterns/cr01_cr02_v1.yaml"


def _event(unit_id: str) -> dict:
    return {
        "run_id": "media-run",
        "unit_id": unit_id,
        "source": {"source_id": "cam-1", "source_type": "video", "frame_index": 0,
                   "timestamp_ms": 0.0, "width": 640, "height": 480},
        "model": {"name": "mock", "device": "cpu"},
        "prompts": {"prompt_set_id": "cr01_cr02_v1"},
        "detections": [{"detection_id": "p1", "label": "person", "prompt_id": "person",
                        "confidence": 0.9, "bbox_xyxy": [100, 100, 220, 420]}],
    }


def _config_path(tmp_path: Path) -> Path:
    input_path = tmp_path / "detections.jsonl"
    input_path.write_text(json.dumps(_event("u1")) + "\n", encoding="utf-8")
    config_path = tmp_path / "replay.yaml"
    config_path.write_text(
        yaml.safe_dump({
            "run": {"id": "prog-run", "scenario": "DBE", "name": "prog"},
            "input": {"type": "media_jsonl", "path": str(input_path)},
            "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
            "outputs": {"base_dir": str(tmp_path / "runs")},
        }),
        encoding="utf-8",
    )
    return config_path


def test_prepare_run_writes_effective_config_and_resolves_patterns(tmp_path) -> None:
    config = load_replay_config(_config_path(tmp_path))

    prepared = prepare_run(config)

    assert prepared.control_run_id == "prog-run"
    assert prepared.artifacts.effective_config_path.exists()
    assert [p.id for p in prepared.active_patterns] == ["CR-01"]


def test_progress_is_updated_during_the_run(tmp_path) -> None:
    config = load_replay_config(_config_path(tmp_path))
    progress = RunProgress()

    summary = run_replay_from_config(config, progress=progress)

    assert progress.units_processed == summary.units_processed == 1
    assert progress.alerts_count == summary.alerts_count == 1
    assert progress.pattern_events_count == summary.pattern_events_count
    assert progress.errors_count == 0
    assert progress.bus_dropped_events == 0


def test_prepare_run_can_be_reused_so_the_run_id_is_stable(tmp_path) -> None:
    """El servicio necesita el control_run_id ANTES de correr (para suscribir el bus)."""
    config = load_replay_config(_config_path(tmp_path))
    prepared = prepare_run(config)

    summary = run_replay_from_config(config, prepared=prepared)

    assert summary.control_run_id == prepared.control_run_id


def test_run_replay_from_path_still_works(tmp_path) -> None:
    summary = run_replay(_config_path(tmp_path))
    assert summary.units_processed == 1
