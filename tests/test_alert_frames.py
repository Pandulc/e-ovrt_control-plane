"""Tests for alert-frame annotation parsing and CSV export."""

from __future__ import annotations

import csv
import json

from eovrt_control.visualization.alert_frames import (
    export_alert_details_csv,
    read_alert_annotations,
)


def test_reads_focus_csv_with_candidate_and_confirm_annotations(tmp_path) -> None:
    alerts_path = tmp_path / "alerts_focus.csv"
    alerts_path.write_text(
        "\n".join(
            [
                "variant,alert_order,pattern_id,condition_id,severity,subject_id,"
                "missing_class,candidate_frame,candidate_second,candidate_bbox,"
                "confirm_frame,confirm_second,confirm_bbox,confirm_confidence",
                "baseline,1,CR-01,CR-01,high,worker_a,helmet,10,0.4,"
                '"[10, 20, 30, 40]",15,0.6,"[11, 21, 31, 41]",0.91',
            ]
        ),
        encoding="utf-8",
    )

    annotations = read_alert_annotations(alerts_path, stage="both")
    output_path = tmp_path / "alerts_details.csv"
    export_alert_details_csv(alerts_path, output_path, stage="both")
    roundtrip = read_alert_annotations(output_path, stage="both")

    assert [item.stage for item in annotations] == ["candidate", "confirm"]
    assert [item.stage for item in roundtrip] == ["candidate", "confirm"]
    assert annotations[0].bbox_xyxy == (10.0, 20.0, 30.0, 40.0)
    assert annotations[1].frame_index == 15
    assert annotations[1].condition_id == "CR-01"
    assert annotations[1].missing_class == "helmet"


def test_exports_jsonl_alerts_to_bbox_detail_csv(tmp_path) -> None:
    alerts_path = tmp_path / "alerts.jsonl"
    alerts_path.write_text(
        json.dumps(
            {
                "schema_version": "control.alert.v1",
                "event_type": "alert_event",
                "control_run_id": "control-run",
                "media_run_id": "media-run",
                "unit_id": "frame_000123",
                "source_id": "camera_01",
                "alert_id": "alert-1",
                "pattern_id": "CR-02",
                "condition_id": "CR-02",
                "subject_key": "CR-02:camera_01:subject_001",
                "severity": "medium",
                "state": "open",
                "frame_index": 123,
                "timestamp_ms": 4920.0,
                "evidence": {
                    "pattern_id": "CR-02",
                    "condition_id": "CR-02",
                    "subject_key": "CR-02:camera_01:subject_001",
                    "subject": {
                        "detection_id": "subject_001",
                        "label": "person",
                        "confidence": 0.88,
                        "bbox_xyxy": [100, 120, 220, 420],
                    },
                    "missing_class": "vest",
                    "supporting": [],
                    "score": 0.88,
                    "rationale": "Persona sin chaleco asociado.",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "alerts.csv"

    export_alert_details_csv(alerts_path, output_path)

    with output_path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["stage"] == "confirm"
    assert rows[0]["condition_id"] == "CR-02"
    assert rows[0]["risk_condition"] == "missing_vest"
    assert rows[0]["missing_class"] == "vest"
    assert rows[0]["bbox_xyxy"] == "[100, 120, 220, 420]"
    assert rows[0]["second"] == "4.920000"
