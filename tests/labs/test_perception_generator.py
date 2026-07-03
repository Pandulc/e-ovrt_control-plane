"""Tests del generador de evidencia perceptual."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from eovrt_control.contracts.media import DetectionEvent
from eovrt_labs.perception.events import (
    RawModelDetection,
    attach_epp_to_persons,
    build_detection_event,
    serialize_event,
)
from eovrt_labs.perception.labels import normalize_gdino_label, normalize_yolo_label, to_canonical_label
from eovrt_labs.perception.normalizer import normalize_detections, postprocess_raw_detections
from eovrt_labs.perception.tracking import SimpleIoUTracker, iou


def test_normalize_yolo_labels():
    assert normalize_yolo_label("Person") == "person"
    assert normalize_yolo_label("Hardhat") == "helmet"
    assert normalize_yolo_label("Safety Vest") == "vest"
    assert normalize_yolo_label("NO-Hardhat") is None
    assert normalize_yolo_label("NO-Safety Vest") is None
    assert to_canonical_label("hard hat", backend="yolo-ppe") == "helmet"
    assert to_canonical_label("bare_head", backend="yoloe") is None


def test_normalize_gdino_labels():
    prompts = ["person", "helmet", "safety vest"]
    assert normalize_gdino_label("person", prompts) == "person"
    assert normalize_gdino_label("safety vest", prompts) == "vest"


def test_normalizer_bbox_norm_and_area():
    raw = [
        RawModelDetection("person", 0.9, [10.0, 10.0, 30.0, 20.0]),
    ]
    detections = normalize_detections(
        raw,
        width=100,
        height=100,
        model_name="yoloe-26s",
        min_confidence=0.25,
    )
    assert len(detections) == 1
    det = detections[0]
    assert det.bbox_norm_xyxy == [0.1, 0.1, 0.3, 0.2]
    assert det.area_px == 200.0
    assert det.detection_id == "det_000001"
    assert det.model_name == "yoloe-26s"


def test_serialize_excludes_none_fields():
    event = build_detection_event(
        run_id="run_20260626_001",
        unit_id="frame_000123",
        source_id="camera_01",
        source_type="video_frame",
        frame_index=123,
        timestamp_ms=5066.0,
        width=640,
        height=480,
        model_name="yoloe-26s",
        model_id="yoloe/yoloe-26s",
        device="cuda:0",
        prompt_set_id="cr01_cr02_bench_v2",
        detections=normalize_detections(
            [RawModelDetection("helmet", 0.87, [100.0, 50.0, 200.0, 150.0])],
            width=640,
            height=480,
            model_name="yoloe-26s",
            min_confidence=0.25,
        ),
        inference_ms=34.6,
        postprocess_ms=2.1,
        write_ms=0.4,
        total_ms=38.3,
    )
    payload = serialize_event(event)
    data = json.loads(payload)
    assert data["timing"]["normalize_ms"] == 0.0
    assert data["source"]["source_type"] == "video_frame"
    assert data["unit_id"] == "frame_000123"
    assert data["detections"][0]["detection_id"] == "det_000001"
    roundtrip = DetectionEvent.model_validate(data)
    assert roundtrip.run_id == "run_20260626_001"


def test_detection_event_matches_media_plane_contract_shape():
    media_src = Path(__file__).resolve().parents[3] / "e-ovrt_media-plane" / "src"
    if str(media_src) not in sys.path:
        sys.path.insert(0, str(media_src))
    from eovrt_media.contracts.events import DetectionEvent as MediaDetectionEvent

    event = build_detection_event(
        run_id="run_20260626_001",
        unit_id="frame_000123",
        source_id="camera_01",
        source_type="video_frame",
        frame_index=123,
        timestamp_ms=1719412345678.0,
        width=1920,
        height=1080,
        model_name="yoloe-26s",
        model_id="yoloe/yoloe-26s",
        device="cuda:0",
        prompt_set_id="cr01_cr02_bench_v2",
        detections=normalize_detections(
            [RawModelDetection("helmet", 0.8731, [420.5, 110.0, 510.2, 205.7])],
            width=1920,
            height=1080,
            model_name="yoloe-26s",
            min_confidence=0.25,
        ),
        normalize_ms=1.2,
        inference_ms=34.6,
        postprocess_ms=2.1,
        write_ms=0.4,
        total_ms=38.3,
    )
    data = json.loads(serialize_event(event))
    media_event = MediaDetectionEvent.model_validate(data)
    detection_keys = set(data["detections"][0])

    assert media_event.schema_version == "media.detection.v1"
    assert media_event.event_type == "detection_event"
    assert data["timing"]["normalize_ms"] == 1.2
    assert detection_keys == {
        "detection_id",
        "label",
        "prompt_id",
        "confidence",
        "bbox_xyxy",
        "bbox_norm_xyxy",
        "area_px",
        "model_name",
    }


def test_detection_event_accepts_legacy_timing_fields():
    legacy = {
        "schema_version": "media.detection.v1",
        "event_type": "detection_event",
        "run_id": "legacy",
        "unit_id": "frame_0001",
        "source": {
            "source_id": "cam",
            "source_type": "video",
            "frame_index": 1,
            "timestamp_ms": 0.0,
            "width": 100,
            "height": 100,
        },
        "model": {"name": "test", "device": "cpu"},
        "prompts": {"prompt_set_id": "test"},
        "detections": [],
        "timing": {
            "read_ms": 1.0,
            "preprocess_ms": 2.0,
            "inference_ms": 3.0,
            "total_ms": 6.0,
        },
    }
    event = DetectionEvent.model_validate(legacy)
    assert event.timing.inference_ms == 3.0
    assert event.timing.total_ms == 6.0


def test_normalizer_preserves_stable_detection_id():
    raw = [
        RawModelDetection("person", 0.9, [10.0, 10.0, 60.0, 120.0], "subject_001"),
        RawModelDetection("helmet", 0.8, [20.0, 12.0, 50.0, 40.0]),
    ]
    detections = normalize_detections(
        raw,
        width=100,
        height=100,
        model_name="yolo_construction_ppe",
        min_confidence=0.25,
    )
    by_label = {item.label: item for item in detections}
    assert by_label["person"].detection_id == "subject_001"
    assert by_label["helmet"].detection_id == "det_000002"


def test_normalizer_clips_boxes_and_applies_nms():
    raw = [
        RawModelDetection("helmet", 0.9, [-10.0, -10.0, 50.0, 50.0]),
        RawModelDetection("helmet", 0.8, [0.0, 0.0, 48.0, 48.0]),
        RawModelDetection("person", 0.30, [10.0, 10.0, 80.0, 90.0]),
        RawModelDetection("vest", 0.7, [80.0, 80.0, 70.0, 90.0]),
    ]

    detections = normalize_detections(
        raw,
        width=100,
        height=100,
        model_name="yoloe-26s",
        min_confidence=0.25,
        class_confidence_thresholds={"person": 0.35, "helmet": 0.25, "vest": 0.25},
        nms_iou_thresholds={"helmet": 0.5, "person": 0.65, "vest": 0.5},
    )

    assert len(detections) == 1
    assert detections[0].label == "helmet"
    assert detections[0].bbox_xyxy == [0.0, 0.0, 50.0, 50.0]
    assert detections[0].bbox_norm_xyxy == [0.0, 0.0, 0.5, 0.5]
    assert detections[0].area_px == 2500.0


def test_postprocess_raw_detections_returns_contract_ready_boxes():
    raw = [
        RawModelDetection("person", 0.9, [-5.0, 10.0, 45.0, 110.0]),
    ]

    postprocessed = postprocess_raw_detections(
        raw,
        width=100,
        height=100,
        min_confidence=0.25,
    )

    assert len(postprocessed) == 1
    assert postprocessed[0].bbox_xyxy == [0.0, 10.0, 45.0, 100.0]


def test_apply_person_tracking_assigns_stable_ids():
    from eovrt_labs.perception.tracking import apply_person_tracking

    tracker = SimpleIoUTracker(iou_threshold=0.3)
    box_a = [10.0, 10.0, 60.0, 120.0]
    box_b = [12.0, 12.0, 58.0, 118.0]

    first = apply_person_tracking(
        [RawModelDetection("person", 0.9, box_a)],
        tracker,
    )
    second = apply_person_tracking(
        [RawModelDetection("person", 0.9, box_b)],
        tracker,
    )
    assert first[0].detection_id == second[0].detection_id
    assert first[0].detection_id.startswith("subject_")


def test_apply_person_tracking_preserves_detection_order():
    from eovrt_labs.perception.tracking import apply_person_tracking

    tracker = SimpleIoUTracker()
    raw = [
        RawModelDetection("helmet", 0.9, [10.0, 10.0, 30.0, 30.0]),
        RawModelDetection("person", 0.9, [0.0, 0.0, 50.0, 100.0]),
        RawModelDetection("vest", 0.8, [5.0, 40.0, 45.0, 80.0]),
    ]

    tracked = apply_person_tracking(raw, tracker, frame_index=0, timestamp_ms=0.0)

    assert [item.label for item in tracked] == ["helmet", "person", "vest"]
    assert tracked[1].detection_id == "subject_001"


def test_iou_and_tracker_assigns_stable_ids():
    tracker = SimpleIoUTracker(iou_threshold=0.3)
    box_a = [10.0, 10.0, 60.0, 120.0]
    box_b = [12.0, 12.0, 58.0, 118.0]
    assert iou(box_a, box_b) > 0.5

    first = tracker.assign([box_a])
    second = tracker.assign([box_b])
    assert first == second
    assert first[0].startswith("subject_")


def test_tracker_keeps_id_after_short_gap():
    tracker = SimpleIoUTracker(iou_threshold=0.2, max_lost_ms=750.0)
    first = tracker.assign(
        [[10.0, 10.0, 60.0, 120.0]],
        confidences=[0.9],
        frame_index=0,
        timestamp_ms=0.0,
    )
    tracker.assign([], frame_index=1, timestamp_ms=200.0)
    second = tracker.assign(
        [[14.0, 10.0, 64.0, 120.0]],
        confidences=[0.9],
        frame_index=2,
        timestamp_ms=400.0,
    )

    assert second == first


def test_tracker_opens_new_id_after_max_lost():
    tracker = SimpleIoUTracker(iou_threshold=0.2, max_lost_ms=100.0, max_lost_frames=1)
    first = tracker.assign(
        [[10.0, 10.0, 60.0, 120.0]],
        confidences=[0.9],
        frame_index=0,
        timestamp_ms=0.0,
    )
    tracker.assign([], frame_index=1, timestamp_ms=150.0)
    second = tracker.assign(
        [[12.0, 10.0, 62.0, 120.0]],
        confidences=[0.9],
        frame_index=2,
        timestamp_ms=200.0,
    )

    assert second != first
    assert second == ["subject_002"]


def test_tracker_recovers_lost_id_with_matching_appearance():
    tracker = SimpleIoUTracker(
        iou_threshold=0.2,
        max_lost_ms=1500.0,
        appearance_weight=0.7,
    )
    first = tracker.assign(
        [[0.0, 0.0, 50.0, 100.0]],
        confidences=[0.9],
        appearance_features=[[1.0, 0.0, 0.0]],
        frame_index=0,
        timestamp_ms=0.0,
    )
    tracker.assign([], frame_index=1, timestamp_ms=300.0)
    second = tracker.assign(
        [[140.0, 0.0, 190.0, 100.0]],
        confidences=[0.9],
        appearance_features=[[1.0, 0.0, 0.0]],
        frame_index=2,
        timestamp_ms=600.0,
    )

    assert second == first


def test_tracker_does_not_reuse_lost_id_for_different_appearance():
    tracker = SimpleIoUTracker(
        iou_threshold=0.2,
        max_lost_ms=1500.0,
        appearance_weight=0.7,
        appearance_min_similarity=0.5,
    )
    first = tracker.assign(
        [[0.0, 0.0, 50.0, 100.0]],
        confidences=[0.9],
        appearance_features=[[1.0, 0.0, 0.0]],
        frame_index=0,
        timestamp_ms=0.0,
    )
    tracker.assign([], frame_index=1, timestamp_ms=300.0)
    second = tracker.assign(
        [[140.0, 0.0, 190.0, 100.0]],
        confidences=[0.9],
        appearance_features=[[0.0, 1.0, 0.0]],
        frame_index=2,
        timestamp_ms=600.0,
    )

    assert first == ["subject_001"]
    assert second == ["subject_002"]


def test_tracker_follows_boxes_when_input_order_changes():
    tracker = SimpleIoUTracker(iou_threshold=0.2)
    first = tracker.assign(
        [
            [0.0, 0.0, 50.0, 100.0],
            [200.0, 0.0, 250.0, 100.0],
        ],
        frame_index=0,
        timestamp_ms=0.0,
    )
    second = tracker.assign(
        [
            [205.0, 0.0, 255.0, 100.0],
            [5.0, 0.0, 55.0, 100.0],
        ],
        frame_index=1,
        timestamp_ms=33.0,
    )

    assert second == [first[1], first[0]]


def test_attach_epp_to_persons_assigns_derived_ids():
    persons = [
        RawModelDetection("person", 0.9, [100.0, 100.0, 200.0, 400.0], "worker_a"),
    ]
    epp = [
        RawModelDetection("helmet", 0.8, [130.0, 105.0, 170.0, 140.0]),
        RawModelDetection("vest", 0.7, [120.0, 180.0, 180.0, 300.0]),
    ]
    detections = attach_epp_to_persons(persons, epp)
    labels = {item.label for item in detections}
    ids = {item.detection_id for item in detections}
    assert labels == {"person", "helmet", "vest"}
    assert "worker_a" in ids
    assert "worker_a_helmet" in ids
    assert "worker_a_vest" in ids
