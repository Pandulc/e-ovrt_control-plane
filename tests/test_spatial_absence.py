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


def test_shared_epp_covers_only_closest_person() -> None:
    # Un unico casco cuyo centro cae en la region de dos personas superpuestas
    # solo debe cubrir a la mas cercana; la otra genera evidencia (asociacion 1:1).
    patterns = load_patterns_file("configs/patterns/cr01_cr02_v1.yaml")
    pattern = patterns.active_patterns(["CR-01"])[0]
    event = _event(
        [
            Detection(
                detection_id="pA",
                label="person",
                prompt_id="person",
                confidence=0.9,
                bbox_xyxy=[100, 100, 300, 500],
            ),
            Detection(
                detection_id="pB",
                label="person",
                prompt_id="person",
                confidence=0.9,
                bbox_xyxy=[150, 100, 350, 500],
            ),
            Detection(
                detection_id="h1",
                label="helmet",
                prompt_id="helmet",
                confidence=0.8,
                bbox_xyxy=[180, 130, 220, 170],
            ),
        ]
    )

    result = evaluate_spatial_absence(event, pattern)

    assert len(result.evidences) == 1
    assert result.evidences[0].subject.detection_id == "pB"


def test_overlapping_persons_do_not_steal_each_others_helmet() -> None:
    # Regresion del caso real (video5, frame 282): dos cajas de persona superpuestas,
    # dos cascos detectados. Un greedy puro por distancia le asignaba el casco del
    # sujeto trasero a la caja delantera (mas cercana) y disparaba un CR-01 falso,
    # dejando el otro casco sin usar. El matching de cardinalidad maxima debe
    # cubrir a ambos.
    patterns = load_patterns_file("configs/patterns/cr01_cr02_v1.yaml")
    pattern = patterns.active_patterns(["CR-01"])[0]
    event = _event(
        [
            # p1: caja superpuesta cuya region contiene ambos cascos; el casco de p2
            # le queda mas cerca que el propio.
            Detection(
                detection_id="p1",
                label="person",
                prompt_id="person",
                confidence=0.9,
                bbox_xyxy=[90, 60, 210, 260],
            ),
            # p2: dueno real del casco h_a.
            Detection(
                detection_id="p2",
                label="person",
                prompt_id="person",
                confidence=0.9,
                bbox_xyxy=[100, 100, 200, 500],
            ),
            Detection(
                detection_id="h_a",
                label="helmet",
                prompt_id="helmet",
                confidence=0.8,
                bbox_xyxy=[140, 120, 180, 160],
            ),
            Detection(
                detection_id="h_b",
                label="helmet",
                prompt_id="helmet",
                confidence=0.8,
                bbox_xyxy=[95, 65, 125, 95],
            ),
        ]
    )

    result = evaluate_spatial_absence(event, pattern)

    assert result.evidences == []
    assert len(result.observed_subject_keys) == 2


def test_bent_pose_expands_region_to_full_height() -> None:
    # Regresion del caso real (video5, frame 366): trabajador agachado con el
    # chaleco detectado en el tope de su caja. La caja es mas ancha que alta y
    # la banda torso (25%-85%) queda por debajo del chaleco -> falso CR-02.
    # Con full_height_aspect_ratio la region cubre toda la caja.
    base = {
        "id": "CR-02",
        "name": "person_without_vest",
        "condition_id": "CR-02",
        "subject_class": "person",
        "required_absent_class": "vest",
        "evidence": {"min_absent_class_confidence": 0.20},
        "region": {
            "type": "torso",
            "y_min_ratio": 0.25,
            "y_max_ratio": 0.85,
            "x_margin_ratio": 0.08,
        },
    }
    # Persona agachada: caja 246x326 px (aspecto 0.75), chaleco con centro
    # apenas por encima del inicio de la banda torso.
    detections = [
        Detection(
            detection_id="p1",
            label="person",
            prompt_id="person",
            confidence=0.9,
            bbox_xyxy=[1264, 1381, 1510, 1707],
        ),
        Detection(
            detection_id="v1",
            label="vest",
            prompt_id="vest",
            confidence=0.26,
            bbox_xyxy=[1266, 1389, 1421, 1531],
        ),
    ]

    from eovrt_control.config import PatternDefinition

    rigid = PatternDefinition.model_validate(base)
    assert len(evaluate_spatial_absence(_event(detections), rigid).evidences) == 1

    adaptive_config = {**base, "region": {**base["region"], "full_height_aspect_ratio": 0.75}}
    adaptive = PatternDefinition.model_validate(adaptive_config)
    assert evaluate_spatial_absence(_event(detections), adaptive).evidences == []

