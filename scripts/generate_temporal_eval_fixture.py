#!/usr/bin/env python3
"""Genera detecciones sinteticas media.detection.v1 para evaluacion temporal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "fixtures" / "simulated_media" / "cr01_cr02_temporal"
SOURCE_ID = "sim-risk-stream"
WIDTH = 1280
HEIGHT = 720


PERSON_BOXES = {
    "worker_a": [100.0, 100.0, 260.0, 620.0],
    "worker_b": [500.0, 120.0, 660.0, 620.0],
    "worker_c": [820.0, 110.0, 980.0, 620.0],
}

HELMET_BOXES = {
    "worker_a": [148.0, 110.0, 212.0, 160.0],
    "worker_c": [868.0, 120.0, 932.0, 170.0],
}

VEST_BOXES = {
    "worker_a": [122.0, 250.0, 238.0, 430.0],
    "worker_c": [842.0, 260.0, 958.0, 440.0],
}


def detection(
    detection_id: str,
    label: str,
    confidence: float,
    bbox: list[float],
) -> dict[str, Any]:
    return {
        "detection_id": detection_id,
        "label": label,
        "prompt_id": label,
        "confidence": confidence,
        "bbox_xyxy": bbox,
    }


def person(worker: str) -> dict[str, Any]:
    return detection(worker, "person", 0.92, PERSON_BOXES[worker])


def helmet(worker: str) -> dict[str, Any]:
    return detection(f"{worker}_helmet", "helmet", 0.86, HELMET_BOXES[worker])


def vest(worker: str) -> dict[str, Any]:
    return detection(f"{worker}_vest", "vest", 0.84, VEST_BOXES[worker])


def frame_detections(frame_index: int) -> list[dict[str, Any]]:
    detections: list[dict[str, Any]] = []

    # worker_a: equipado en frames 0-2, sin casco persistente en 3-7, equipado en 8-9.
    if 0 <= frame_index <= 9:
        detections.append(person("worker_a"))
        if frame_index <= 2 or frame_index >= 8:
            detections.append(helmet("worker_a"))
        detections.append(vest("worker_a"))

    # worker_b: condicion transitoria sin EPP en frames 2-3; no debe alertar.
    if 2 <= frame_index <= 3:
        detections.append(person("worker_b"))

    # worker_c: equipado en frames 4, sin chaleco persistente en 5-9, equipado en 10-11.
    if 4 <= frame_index <= 11:
        detections.append(person("worker_c"))
        detections.append(helmet("worker_c"))
        if frame_index == 4 or frame_index >= 10:
            detections.append(vest("worker_c"))

    return detections


def event(frame_index: int) -> dict[str, Any]:
    return {
        "schema_version": "media.detection.v1",
        "event_type": "detection_event",
        "run_id": "simulated-media-cr01-cr02-temporal",
        "unit_id": f"frame_{frame_index:04d}",
        "source": {
            "source_id": SOURCE_ID,
            "source_type": "video",
            "frame_index": frame_index,
            "timestamp_ms": frame_index * 500.0,
            "width": WIDTH,
            "height": HEIGHT,
        },
        "model": {
            "name": "simulated-media-output",
            "model_id": "manual-synthetic-v1",
            "device": "cpu",
        },
        "prompts": {"prompt_set_id": "cr01_cr02_temporal_eval"},
        "detections": frame_detections(frame_index),
        "timing": {
            "read_ms": 0.0,
            "preprocess_ms": 0.0,
            "inference_ms": 0.0,
            "postprocess_ms": 0.0,
            "write_ms": 0.0,
            "total_ms": 0.0,
        },
    }


def ground_truth() -> dict[str, Any]:
    return {
        "scenario_id": "cr01_cr02_temporal",
        "description": "Escena sintetica con CR-01 persistente, CR-02 persistente y riesgo transitorio no alertable.",
        "expected_alerts": [
            {
                "id": "worker_a_cr01",
                "condition_id": "CR-01",
                "subject_key": f"CR-01:{SOURCE_ID}:worker_a",
                "first_evidence_frame_index": 3,
                "expected_alert_frame_index": 5,
                "max_alert_frame_index": 5,
                "first_evidence_timestamp_ms": 1500.0,
            },
            {
                "id": "worker_c_cr02",
                "condition_id": "CR-02",
                "subject_key": f"CR-02:{SOURCE_ID}:worker_c",
                "first_evidence_frame_index": 5,
                "expected_alert_frame_index": 7,
                "max_alert_frame_index": 7,
                "first_evidence_timestamp_ms": 2500.0,
            },
        ],
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    detections_path = OUT_DIR / "detections.jsonl"
    with detections_path.open("w", encoding="utf-8") as fh:
        for frame_index in range(12):
            fh.write(json.dumps(event(frame_index), ensure_ascii=False) + "\n")
    (OUT_DIR / "ground_truth.json").write_text(
        json.dumps(ground_truth(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(detections_path)
    print(OUT_DIR / "ground_truth.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
