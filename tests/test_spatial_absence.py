from eovrt_control.config import load_patterns_file
from eovrt_control.contracts.media import Detection, DetectionEvent
from eovrt_control.engine.evaluators.spatial_absence import evaluate_spatial_absence


def _event(detections: list[Detection]) -> DetectionEvent:
    return DetectionEvent(
        run_id="media-run",
        unit_id="unit-1",
        source={"source_id": "image.jpg", "source_type": "image", "width": 640, "height": 480},
        model={"name": "mock", "device": "cpu"},
        prompts={"prompt_set_id": "cr01_cr02_v1"},
        detections=detections,
    )


def test_person_without_helmet_generates_evidence() -> None:
    patterns = load_patterns_file("configs/patterns/cr01_cr02_v1.yaml")
    pattern = patterns.active_patterns(["CR-01"])[0]
    event = _event(
        [
            Detection(
                detection_id="p1",
                label="person",
                prompt_id="person",
                confidence=0.9,
                bbox_xyxy=[100, 100, 220, 420],
            )
        ]
    )

    result = evaluate_spatial_absence(event, pattern)

    assert len(result.evidences) == 1
    assert result.evidences[0].missing_class == "helmet"


def test_associated_helmet_suppresses_cr01_evidence() -> None:
    patterns = load_patterns_file("configs/patterns/cr01_cr02_v1.yaml")
    pattern = patterns.active_patterns(["CR-01"])[0]
    event = _event(
        [
            Detection(
                detection_id="p1",
                label="person",
                prompt_id="person",
                confidence=0.9,
                bbox_xyxy=[100, 100, 220, 420],
            ),
            Detection(
                detection_id="h1",
                label="helmet",
                prompt_id="helmet",
                confidence=0.8,
                bbox_xyxy=[130, 110, 180, 155],
            ),
        ]
    )

    result = evaluate_spatial_absence(event, pattern)

    assert result.evidences == []

