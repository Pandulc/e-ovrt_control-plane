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


def test_run_replay_warns_when_persistence_needs_stable_ids(tmp_path) -> None:
    input_path = tmp_path / "detections.jsonl"
    output_dir = tmp_path / "runs"
    config_path = tmp_path / "replay.yaml"
    repo_root = Path(__file__).resolve().parents[1]
    patterns_path = repo_root / "configs/patterns/cr01_cr02_temporal_eval.yaml"

    events = [
        {
            "run_id": "media-run",
            "unit_id": f"frame_{frame:04d}",
            "source": {
                "source_id": "camera",
                "source_type": "video_frame",
                "frame_index": frame,
                "timestamp_ms": frame * 500.0,
                "width": 640,
                "height": 480,
            },
            "model": {"name": "mock", "device": "cpu"},
            "prompts": {"prompt_set_id": "cr01_cr02_temporal_eval"},
            "detections": [
                {
                    "detection_id": f"det_{frame + 1:06d}",
                    "label": "person",
                    "prompt_id": "person",
                    "confidence": 0.9,
                    "bbox_xyxy": [100, 100, 220, 420],
                }
            ],
        }
        for frame in range(4)
    ]
    input_path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )
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

    assert summary.alerts_count == 0
    assert len(summary.warnings) == 1
    assert "Persistencia temporal inactiva" in summary.warnings[0]

