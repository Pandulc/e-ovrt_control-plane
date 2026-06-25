import json
from pathlib import Path

import yaml

from eovrt_control.evaluation import evaluate_temporal_alerts
from eovrt_control.runtime.replay import run_replay


def test_simulated_temporal_replay_matches_expected_alerts(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    fixture_dir = repo_root / "fixtures/simulated_media/cr01_cr02_temporal"
    patterns_path = repo_root / "configs/patterns/cr01_cr02_temporal_eval.yaml"
    config_path = tmp_path / "replay.yaml"
    output_dir = tmp_path / "runs"

    config_path.write_text(
        yaml.safe_dump(
            {
                "run": {
                    "id": "simulated-cr01-cr02-test",
                    "scenario": "DBE",
                    "name": "test",
                },
                "input": {
                    "type": "media_jsonl",
                    "path": str(fixture_dir / "detections.jsonl"),
                },
                "patterns": {
                    "file": str(patterns_path),
                    "active_ids": ["CR-01", "CR-02"],
                },
                "outputs": {"base_dir": str(output_dir)},
            }
        ),
        encoding="utf-8",
    )

    summary = run_replay(config_path)
    run_dir = output_dir / "simulated-cr01-cr02-test"
    evaluation = evaluate_temporal_alerts(
        run_dir / "alerts.jsonl",
        fixture_dir / "ground_truth.json",
        run_dir / "eval_temporal.json",
    )

    assert summary.units_processed == 12
    assert summary.alerts_count == 2
    assert evaluation.expected_alerts_count == 2
    assert evaluation.observed_alerts_count == 2
    assert evaluation.matched_alerts_count == 2
    assert evaluation.missed_alerts_count == 0
    assert evaluation.unexpected_alerts_count == 0
    assert evaluation.precision == 1.0
    assert evaluation.recall == 1.0
    assert evaluation.f1 == 1.0
    assert evaluation.avg_latency_frames_from_first_evidence == 2.0
    assert (run_dir / "eval_temporal.json").exists()


def test_temporal_evaluation_reports_unexpected_alert(tmp_path) -> None:
    alerts_path = tmp_path / "alerts.jsonl"
    ground_truth_path = tmp_path / "ground_truth.json"

    alerts_path.write_text(
        json.dumps(
            {
                "control_run_id": "control-run",
                "media_run_id": "media-run",
                "unit_id": "frame_0001",
                "source_id": "sim-risk-stream",
                "alert_id": "unexpected-alert",
                "pattern_id": "CR-01",
                "condition_id": "CR-01",
                "subject_key": "CR-01:sim-risk-stream:unknown_worker",
                "severity": "medium",
                "frame_index": 1,
                "timestamp_ms": 500.0,
                "evidence": {
                    "pattern_id": "CR-01",
                    "condition_id": "CR-01",
                    "subject_key": "CR-01:sim-risk-stream:unknown_worker",
                    "subject": {
                        "detection_id": "unknown_worker",
                        "label": "person",
                        "confidence": 0.9,
                        "bbox_xyxy": [0, 0, 10, 10],
                    },
                    "missing_class": "helmet",
                    "supporting": [],
                    "score": 0.9,
                    "rationale": "synthetic",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    ground_truth_path.write_text(
        json.dumps({"scenario_id": "empty", "expected_alerts": []}),
        encoding="utf-8",
    )

    evaluation = evaluate_temporal_alerts(alerts_path, ground_truth_path)

    assert evaluation.observed_alerts_count == 1
    assert evaluation.expected_alerts_count == 0
    assert evaluation.matched_alerts_count == 0
    assert evaluation.unexpected_alerts_count == 1
    assert evaluation.precision == 0.0
    assert evaluation.recall == 0.0
