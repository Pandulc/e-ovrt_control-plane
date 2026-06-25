from eovrt_control.config import PatternDefinition, load_patterns_file
from eovrt_control.contracts.media import Detection, DetectionEvent
from eovrt_control.engine.pattern_engine import PatternEngine


def test_engine_confirms_and_alerts_on_missing_helmet() -> None:
    patterns_file = load_patterns_file("configs/patterns/cr01_cr02_v1.yaml")
    engine = PatternEngine(
        control_run_id="control-run",
        patterns=patterns_file.active_patterns(["CR-01"]),
    )
    event = DetectionEvent(
        run_id="media-run",
        unit_id="unit-1",
        source={"source_id": "image.jpg", "source_type": "image", "width": 640, "height": 480},
        model={"name": "mock", "device": "cpu"},
        prompts={"prompt_set_id": "cr01_cr02_v1"},
        detections=[
            Detection(
                detection_id="p1",
                label="person",
                prompt_id="person",
                confidence=0.9,
                bbox_xyxy=[100, 100, 220, 420],
            )
        ],
    )

    result = engine.process(event)

    assert len(result.pattern_events) == 1
    assert result.pattern_events[0].state == "confirmed"
    assert len(result.alerts) == 1


def _time_pattern() -> PatternDefinition:
    return PatternDefinition.model_validate(
        {
            "id": "CR-01",
            "name": "person_without_helmet",
            "condition_id": "CR-01",
            "subject_class": "person",
            "required_absent_class": "helmet",
            "region": {
                "type": "upper_body",
                "y_min_ratio": 0.0,
                "y_max_ratio": 0.45,
                "x_margin_ratio": 0.12,
            },
            "timing": {
                "confirm_after_frames": 99,
                "resolve_after_frames": 99,
                "confirm_after_ms": 1000.0,
                "resolve_after_ms": 500.0,
            },
        }
    )


def _event_at(timestamp_ms: float, frame_index: int, has_helmet: bool = False) -> DetectionEvent:
    detections = [
        Detection(
            detection_id="p1",
            label="person",
            prompt_id="person",
            confidence=0.9,
            bbox_xyxy=[100, 100, 220, 420],
        )
    ]
    if has_helmet:
        detections.append(
            Detection(
                detection_id="h1",
                label="helmet",
                prompt_id="helmet",
                confidence=0.9,
                bbox_xyxy=[130, 105, 190, 150],
            )
        )
    return DetectionEvent(
        run_id="media-run",
        unit_id=f"unit-{frame_index}",
        source={
            "source_id": "video.mp4",
            "source_type": "video_frame",
            "frame_index": frame_index,
            "timestamp_ms": timestamp_ms,
            "width": 640,
            "height": 480,
        },
        model={"name": "mock", "device": "cpu"},
        prompts={"prompt_set_id": "cr01_cr02_v1"},
        detections=detections,
    )


def test_engine_uses_elapsed_time_for_confirmation() -> None:
    engine = PatternEngine(control_run_id="control-run", patterns=[_time_pattern()])

    first = engine.process(_event_at(0.0, 0))
    second = engine.process(_event_at(400.0, 1))
    third = engine.process(_event_at(1000.0, 2))

    assert [event.state for event in first.pattern_events] == ["candidate"]
    assert second.pattern_events == []
    assert [event.state for event in third.pattern_events] == ["confirmed"]
    assert len(third.alerts) == 1


def test_engine_uses_elapsed_time_for_resolution() -> None:
    engine = PatternEngine(control_run_id="control-run", patterns=[_time_pattern()])

    engine.process(_event_at(0.0, 0))
    engine.process(_event_at(1000.0, 1))
    first_clear = engine.process(_event_at(1100.0, 2, has_helmet=True))
    second_clear = engine.process(_event_at(1600.0, 3, has_helmet=True))

    assert first_clear.pattern_events == []
    assert [event.state for event in second_clear.pattern_events] == ["resolved"]
