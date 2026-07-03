"""Tests for alert-frame annotation parsing and CSV export."""

from __future__ import annotations

import csv
import json

from eovrt_control.sinks.alerts_csv import (
    export_alert_details_csv,
    read_alert_annotations,
)


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

    roundtrip = read_alert_annotations(output_path, stage="all")
    assert len(roundtrip) == 1
    assert roundtrip[0].stage == "confirm"
    assert roundtrip[0].condition_id == "CR-02"
    assert roundtrip[0].bbox_xyxy == (100.0, 120.0, 220.0, 420.0)
