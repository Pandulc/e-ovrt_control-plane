from eovrt_control.config import PatternDefinition, PatternRegionConfig, load_patterns_file
from eovrt_control.contracts.media import Detection, DetectionEvent
from eovrt_control.engine.evaluators.spatial_absence import evaluate_spatial_absence


def _helmet_pattern(**overrides) -> PatternDefinition:
    base = dict(
        id="CR-01",
        name="person_without_helmet",
        condition_id="CR-01",
        required_absent_class="helmet",
        region=PatternRegionConfig(type="upper_body", y_min_ratio=0.0, y_max_ratio=0.45, x_margin_ratio=0.12),
    )
    base.update(overrides)
    return PatternDefinition(**base)


def _event(
    detections: list[Detection] | None = None,
    *,
    source_id: str = "image.jpg",
) -> DetectionEvent:
    return DetectionEvent(
        run_id="media-run",
        unit_id="unit-1",
        source={"source_id": source_id, "source_type": "image", "width": 640, "height": 480},
        model={"name": "mock", "device": "cpu"},
        prompts={"prompt_set_id": "cr01_cr02_v1"},
        detections=detections or [],
    )


def _detection(
    label: str,
    confidence: float,
    bbox_xyxy: list[float],
    *,
    track_id: str | None = None,
    detection_id: str | None = None,
) -> Detection:
    return Detection(
        detection_id=detection_id,
        track_id=track_id,
        label=label,
        prompt_id=label,
        confidence=confidence,
        bbox_xyxy=bbox_xyxy,
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
    # solo debe cubrir a la mas cercana; la otra sigue aportando evidencia
    # (asociacion 1:1), agregada en la unica clave de escena.
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
    assert result.evidences[0].subjects_in_evidence == 1


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
    # Bajo `scene`, ambos sujetos observados agregan en la unica clave de escena.
    assert result.observed_subject_keys == {"CR-01:image.jpg"}
    assert result.subjects_observed == 2


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

    rigid = PatternDefinition.model_validate(base)
    assert len(evaluate_spatial_absence(_event(detections), rigid).evidences) == 1

    adaptive_config = {**base, "region": {**base["region"], "full_height_aspect_ratio": 0.75}}
    adaptive = PatternDefinition.model_validate(adaptive_config)
    assert evaluate_spatial_absence(_event(detections), adaptive).evidences == []


def test_scene_granularity_collapses_subjects_into_one_state_key() -> None:
    """Dos personas sin casco en la misma escena => una sola evidencia de escena."""
    pattern = _helmet_pattern(granularity="scene")
    event = _event(
        source_id="cam-1",
        detections=[
            _detection("person", 0.9, [0, 0, 100, 200]),
            _detection("person", 0.9, [300, 0, 400, 200]),
        ],
    )

    result = evaluate_spatial_absence(event, pattern)

    assert result.observed_subject_keys == {"CR-01:cam-1"}
    assert len(result.evidences) == 1
    assert result.evidences[0].subject_key == "CR-01:cam-1"
    assert result.evidences[0].subjects_in_evidence == 2
    # El sujeto no representante queda auditable en supporting.
    assert len(result.evidences[0].supporting) == 1
    assert result.subjects_observed == 2


def test_scene_in_evidence_when_at_least_one_subject_uncovered() -> None:
    """Una persona con casco y otra sin casco => la escena esta en evidencia."""
    pattern = _helmet_pattern(granularity="scene")
    event = _event(
        source_id="cam-1",
        detections=[
            _detection("person", 0.9, [0, 0, 100, 200]),
            _detection("helmet", 0.8, [40, 10, 60, 30]),
            _detection("person", 0.9, [300, 0, 400, 200]),
        ],
    )

    result = evaluate_spatial_absence(event, pattern)

    assert len(result.evidences) == 1
    assert result.evidences[0].subjects_in_evidence == 1


def test_scene_not_in_evidence_when_every_subject_covered() -> None:
    pattern = _helmet_pattern(granularity="scene")
    event = _event(
        source_id="cam-1",
        detections=[
            _detection("person", 0.9, [0, 0, 100, 200]),
            _detection("helmet", 0.8, [40, 10, 60, 30]),
        ],
    )

    result = evaluate_spatial_absence(event, pattern)

    assert result.observed_subject_keys == {"CR-01:cam-1"}
    assert result.evidences == []


def test_scene_key_absent_when_no_subjects_observed() -> None:
    pattern = _helmet_pattern(granularity="scene")
    event = _event(source_id="cam-1", detections=[_detection("helmet", 0.8, [0, 0, 10, 10])])

    result = evaluate_spatial_absence(event, pattern)

    assert result.observed_subject_keys == set()
    assert result.evidences == []


def test_subject_granularity_keys_by_track_id() -> None:
    pattern = _helmet_pattern(granularity="subject")
    event = _event(
        source_id="cam-1",
        detections=[
            _detection("person", 0.9, [0, 0, 100, 200], track_id="subject_001"),
            _detection("person", 0.9, [300, 0, 400, 200], track_id="subject_002"),
        ],
    )

    result = evaluate_spatial_absence(event, pattern)

    assert result.observed_subject_keys == {
        "CR-01:cam-1:subject_001",
        "CR-01:cam-1:subject_002",
    }
    assert {e.subject_key for e in result.evidences} == result.observed_subject_keys
    assert all(e.subjects_in_evidence == 1 for e in result.evidences)


def test_subject_granularity_without_track_id_falls_back_to_scene() -> None:
    pattern = _helmet_pattern(granularity="subject")
    event = _event(source_id="cam-1", detections=[_detection("person", 0.9, [0, 0, 100, 200])])

    result = evaluate_spatial_absence(event, pattern)

    assert result.observed_subject_keys == {"CR-01:cam-1"}
    assert result.degradation_causes == {"no_track_id"}


def test_subject_granularity_with_track_id_reports_no_degradation() -> None:
    pattern = _helmet_pattern(granularity="subject")
    event = _event(
        source_id="cam-1",
        detections=[_detection("person", 0.9, [0, 0, 100, 200], track_id="subject_001")],
    )

    result = evaluate_spatial_absence(event, pattern)

    assert result.degradation_causes == set()


def test_subject_granularity_with_no_subjects_reports_no_degradation() -> None:
    """Caso limite: sin ningun sujeto de la clase del patron, no hay nada que
    degradar (el fallback a escena solo aplica cuando SI hay sujetos pero les
    falta track_id)."""
    pattern = _helmet_pattern(granularity="subject")
    event = _event(source_id="cam-1", detections=[_detection("helmet", 0.8, [0, 0, 10, 10])])

    result = evaluate_spatial_absence(event, pattern)

    assert result.observed_subject_keys == set()
    assert result.evidences == []
    assert result.degradation_causes == set()


def test_scene_granularity_without_track_id_is_not_a_degradation() -> None:
    pattern = _helmet_pattern(granularity="scene")
    event = _event(source_id="cam-1", detections=[_detection("person", 0.9, [0, 0, 100, 200])])

    assert evaluate_spatial_absence(event, pattern).degradation_causes == set()


def test_mixed_track_ids_degrade_whole_event_to_scene() -> None:
    """El fallback es por FUENTE (spec 41 §2.1: "el motor cae a G0 para esa
    fuente"), no por deteccion: claves de sujeto y de escena conviviendo para el
    mismo patron-fuente partirian el estado."""
    pattern = _helmet_pattern(granularity="subject")
    event = _event(
        source_id="cam-1",
        detections=[
            _detection("person", 0.9, [0, 0, 100, 200], track_id="subject_001"),
            _detection("person", 0.9, [300, 0, 400, 200]),
        ],
    )

    result = evaluate_spatial_absence(event, pattern)

    assert result.observed_subject_keys == {"CR-01:cam-1"}
    assert result.degradation_causes == {"no_track_id"}


def test_detection_id_is_never_used_as_identity() -> None:
    """Mismo track_id, distinto detection_id => misma clave de estado."""
    pattern = _helmet_pattern(granularity="subject")
    event_a = _event(
        source_id="cam-1",
        detections=[_detection("person", 0.9, [0, 0, 100, 200], track_id="subject_001", detection_id="det_000001")],
    )
    event_b = _event(
        source_id="cam-1",
        detections=[_detection("person", 0.9, [0, 0, 100, 200], track_id="subject_001", detection_id="det_000042")],
    )

    keys_a = evaluate_spatial_absence(event_a, pattern).observed_subject_keys
    keys_b = evaluate_spatial_absence(event_b, pattern).observed_subject_keys

    assert keys_a == keys_b == {"CR-01:cam-1:subject_001"}
