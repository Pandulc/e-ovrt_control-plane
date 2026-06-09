from eovrt_control.config import load_patterns_file
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

