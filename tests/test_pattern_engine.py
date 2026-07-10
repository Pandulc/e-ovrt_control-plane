from eovrt_control.config import PatternDefinition, PatternTimingConfig, load_patterns_file
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


def _event_at(
    timestamp_ms: float,
    frame_index: int,
    has_helmet: bool = False,
    track_id: str | None = None,
) -> DetectionEvent:
    detections = [
        Detection(
            detection_id="p1",
            label="person",
            prompt_id="person",
            confidence=0.9,
            bbox_xyxy=[100, 100, 220, 420],
            track_id=track_id,
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


def _empty_event(timestamp_ms: float, frame_index: int) -> DetectionEvent:
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
        detections=[],
    )


def _lifecycle_pattern(granularity: str | None = None, **timing_overrides) -> PatternDefinition:
    timing = {"confirm_after_frames": 1, "resolve_after_frames": 1}
    timing.update(timing_overrides)
    base: dict = {
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
        "timing": timing,
    }
    if granularity is not None:
        base["granularity"] = granularity
    return PatternDefinition.model_validate(base)


def test_engine_expires_absent_subject() -> None:
    engine = PatternEngine(
        control_run_id="control-run",
        patterns=[_lifecycle_pattern(subject_absent_timeout_ms=1000.0)],
    )

    first = engine.process(_event_at(0.0, 0))
    assert [event.state for event in first.pattern_events] == ["confirmed"]
    assert len(first.alerts) == 1

    still_present = engine.process(_empty_event(500.0, 1))
    assert still_present.pattern_events == []

    expired = engine.process(_empty_event(1000.0, 2))
    assert [event.state for event in expired.pattern_events] == ["resolved"]
    assert expired.alerts == []


def test_engine_coverage_memory_bridges_detection_gaps() -> None:
    # Caso real (video5, subject_006): el chaleco se detecta en ~80% de los
    # frames con huecos breves; sin memoria, los hits se acumulan a traves de
    # los huecos y confirman. Con memoria de cobertura no debe alertar.
    # ADR-012: la memoria de cobertura solo vive bajo `subject` (con track_id).
    engine = PatternEngine(
        control_run_id="control-run",
        patterns=[
            _lifecycle_pattern(
                granularity="subject",
                confirm_after_frames=99,
                confirm_after_ms=1000.0,
                coverage_memory_ms=1500.0,
            )
        ],
    )

    engine.process(_event_at(0.0, 0, has_helmet=True, track_id="subject_006"))
    results = [
        engine.process(_event_at(500.0, 1, track_id="subject_006")),
        engine.process(_event_at(1000.0, 2, track_id="subject_006")),
        engine.process(_event_at(1500.0, 3, track_id="subject_006")),
    ]

    assert all(result.alerts == [] for result in results)
    assert all(result.pattern_events == [] for result in results)


def test_engine_coverage_memory_does_not_mask_never_covered_subject() -> None:
    # ADR-012: la memoria de cobertura solo vive bajo `subject` (con track_id).
    engine = PatternEngine(
        control_run_id="control-run",
        patterns=[
            _lifecycle_pattern(
                granularity="subject",
                confirm_after_frames=99,
                confirm_after_ms=1000.0,
                coverage_memory_ms=1500.0,
            )
        ],
    )

    engine.process(_event_at(0.0, 0, track_id="subject_006"))
    engine.process(_event_at(500.0, 1, track_id="subject_006"))
    confirmed = engine.process(_event_at(1000.0, 2, track_id="subject_006"))

    assert [event.state for event in confirmed.pattern_events] == ["confirmed"]
    assert len(confirmed.alerts) == 1


def test_engine_realert_cooldown_suppresses_reconfirm_alert() -> None:
    engine = PatternEngine(
        control_run_id="control-run",
        patterns=[_lifecycle_pattern(realert_cooldown_ms=5000.0)],
    )

    first = engine.process(_event_at(0.0, 0))
    assert len(first.alerts) == 1

    resolved = engine.process(_event_at(500.0, 1, has_helmet=True))
    assert [event.state for event in resolved.pattern_events] == ["resolved"]

    reconfirm = engine.process(_event_at(1000.0, 2))
    assert [event.state for event in reconfirm.pattern_events] == ["confirmed"]
    assert reconfirm.alerts == []


def _person(bbox: list[float], track_id: str | None = None) -> Detection:
    return Detection(
        label="person", prompt_id="person", confidence=0.9,
        bbox_xyxy=bbox, area_px=(bbox[2] - bbox[0]) * (bbox[3] - bbox[1]),
        track_id=track_id,
    )


def _helmet(bbox: list[float]) -> Detection:
    return Detection(label="helmet", prompt_id="helmet", confidence=0.8, bbox_xyxy=bbox)


def _event(frame_index: int, detections: list[Detection], timestamp_ms: float | None = None) -> DetectionEvent:
    return DetectionEvent.model_validate(
        {
            "run_id": "media-run-1",
            "unit_id": f"img_{frame_index:06d}",
            "source": {
                "source_id": "cam-1", "source_type": "video_frame",
                "frame_index": frame_index, "timestamp_ms": timestamp_ms,
                "width": 640, "height": 480,
            },
            "model": {"name": "mock", "model_id": "mock", "device": "cpu"},
            "prompts": {"prompt_set_id": "test"},
            "detections": [d.model_dump() for d in detections],
            "timing": {},
        }
    )


def _helmet_pattern(**overrides) -> PatternDefinition:
    base = {
        "id": "CR-01", "name": "person_without_helmet", "condition_id": "CR-01",
        "required_absent_class": "helmet",
        "region": {"type": "upper_body", "y_min_ratio": 0.0, "y_max_ratio": 0.45, "x_margin_ratio": 0.12},
    }
    base.update({k: (v.model_dump() if hasattr(v, "model_dump") else v) for k, v in overrides.items()})
    return PatternDefinition.model_validate(base)


def test_coverage_memory_is_ignored_under_scene_granularity() -> None:
    """ADR-012: sin identidad de sujeto no hay a que colgar la memoria."""
    pattern = _helmet_pattern(
        granularity="scene",
        timing=PatternTimingConfig(confirm_after_frames=1, coverage_memory_frames=5),
    )
    engine = PatternEngine("run-1", [pattern])

    # f0: persona cubierta -> la escena queda cubierta
    engine.process(_event(0, [_person([0, 0, 100, 200]), _helmet([40, 10, 60, 30])]))
    # f1: el casco desaparece. Con memoria activa NO alertaria; sin memoria, alerta.
    result = engine.process(_event(1, [_person([0, 0, 100, 200])]))

    assert len(result.alerts) == 1
    assert "coverage_memory_unsupported_scene" in result.degradation_causes


def test_coverage_memory_still_applies_under_subject_granularity() -> None:
    pattern = _helmet_pattern(
        granularity="subject",
        timing=PatternTimingConfig(confirm_after_frames=1, coverage_memory_frames=5),
    )
    engine = PatternEngine("run-1", [pattern])

    engine.process(_event(0, [_person([0, 0, 100, 200], track_id="subject_001"), _helmet([40, 10, 60, 30])]))
    result = engine.process(_event(1, [_person([0, 0, 100, 200], track_id="subject_001")]))

    assert result.alerts == []
    assert result.degradation_causes == set()


def test_hysteresis_absorbs_epp_flicker_without_coverage_memory() -> None:
    """FALSACION DE ADR-012 (rama 1 de un par discriminante; ver tambien
    test_sustained_coverage_beyond_hysteresis_resolves).

    Un EPP que parpadea durante VARIOS frames consecutivos cuyo lapso total
    (100ms) es menor que `resolve_after_ms` (2000ms) NO debe resolver la
    condicion ni re-alertar. Si esto falla, la apuesta del ADR-012 (la
    histeresis subsume la memoria) es incorrecta y hay que revisar la
    decision — no el test.
    """
    pattern = _helmet_pattern(
        granularity="scene",
        timing=PatternTimingConfig(confirm_after_ms=0.0, resolve_after_ms=2000.0),
    )
    engine = PatternEngine("run-1", [pattern])

    # t=0: persona sin casco -> confirma y alerta
    first = engine.process(_event(0, [_person([0, 0, 100, 200])], timestamp_ms=0.0))
    assert len(first.alerts) == 1
    assert [e.state for e in first.pattern_events] == ["confirmed"]

    # t=100/150/200ms: el casco aparece espuriamente durante varios frames
    # consecutivos (parpadeo del detector), lapso total 100ms << 2000ms.
    clears = [
        engine.process(
            _event(1, [_person([0, 0, 100, 200]), _helmet([40, 10, 60, 30])], timestamp_ms=100.0)
        ),
        engine.process(
            _event(2, [_person([0, 0, 100, 200]), _helmet([40, 10, 60, 30])], timestamp_ms=150.0)
        ),
        engine.process(
            _event(3, [_person([0, 0, 100, 200]), _helmet([40, 10, 60, 30])], timestamp_ms=200.0)
        ),
    ]
    assert all(c.pattern_events == [] for c in clears)
    assert all(c.alerts == [] for c in clears)

    # t=250ms: el casco desaparece de nuevo, todavia muy dentro de la ventana.
    resumed = engine.process(_event(4, [_person([0, 0, 100, 200])], timestamp_ms=250.0))

    # No resolvio (nunca paso a `resolved`) => no hay nueva transicion a
    # `confirmed` => no hay re-alerta. OJO: el hit sobre estado `confirmed` SI
    # emite la transicion confirmed->sustained (pattern_engine.py:154-155); eso
    # es correcto y NO invalida la decision. Lo que falsaria el ADR-012 es una
    # transicion a `resolved` (la histeresis no absorbio el parpadeo) o una
    # re-alerta.
    assert resumed.alerts == []
    assert [e.state for e in resumed.pattern_events] == ["sustained"]


def test_sustained_coverage_beyond_hysteresis_resolves() -> None:
    """Control positivo del par discriminante (ver
    test_hysteresis_absorbs_epp_flicker_without_coverage_memory).

    Mismo patron y mismo `resolve_after_ms` (2000ms), pero la cobertura del
    EPP se sostiene MAS ALLA de la ventana (clears en t=100ms y t=2500ms,
    lapso 2400ms >= 2000ms). Esto SI debe resolver. Sin este control, el test
    de arriba seria trivial: cualquier valor de `resolve_after_ms` (50ms,
    2000ms, 1_000_000ms) haria pasar "nunca resuelve" por igual. Este test
    prueba que la ventana se esta midiendo de verdad.
    """
    pattern = _helmet_pattern(
        granularity="scene",
        timing=PatternTimingConfig(confirm_after_ms=0.0, resolve_after_ms=2000.0),
    )
    engine = PatternEngine("run-1", [pattern])

    first = engine.process(_event(0, [_person([0, 0, 100, 200])], timestamp_ms=0.0))
    assert len(first.alerts) == 1
    assert [e.state for e in first.pattern_events] == ["confirmed"]

    engine.process(
        _event(1, [_person([0, 0, 100, 200]), _helmet([40, 10, 60, 30])], timestamp_ms=100.0)
    )
    resolved = engine.process(
        _event(2, [_person([0, 0, 100, 200]), _helmet([40, 10, 60, 30])], timestamp_ms=2500.0)
    )

    assert [e.state for e in resolved.pattern_events] == ["resolved"]


def test_scene_condition_resolves_when_no_subject_observed_within_timeout() -> None:
    """ADR-012 Sec.3: bajo escena, "ningun sujeto observado durante la
    ventana" resuelve la condicion, igual que bajo subject (Task 5)."""
    pattern = _helmet_pattern(
        granularity="scene",
        timing=PatternTimingConfig(
            confirm_after_frames=1, resolve_after_frames=1, subject_absent_timeout_frames=2
        ),
    )
    engine = PatternEngine("run-1", [pattern])

    confirmed = engine.process(_event(0, [_person([0, 0, 100, 200])]))
    assert len(confirmed.alerts) == 1

    # La escena queda vacia de personas.
    engine.process(_event(1, []))
    expired = engine.process(_event(2, []))

    assert [e.state for e in expired.pattern_events] == ["resolved"]
    assert expired.pattern_events[0].subject_key == "CR-01:cam-1"


def test_alert_carries_episode_max_subjects_in_evidence() -> None:
    pattern = _helmet_pattern(
        granularity="scene", timing=PatternTimingConfig(confirm_after_frames=2)
    )
    engine = PatternEngine("run-1", [pattern])

    # f0: dos personas sin casco (candidate; max episodico = 2)
    engine.process(_event(0, [_person([0, 0, 100, 200]), _person([300, 0, 400, 200])]))
    # f1: queda una sin casco -> confirma
    result = engine.process(_event(1, [_person([0, 0, 100, 200])]))

    assert len(result.alerts) == 1
    assert result.alerts[0].subjects_in_evidence_max == 2


def test_max_subjects_resets_on_new_episode() -> None:
    pattern = _helmet_pattern(
        granularity="scene",
        timing=PatternTimingConfig(confirm_after_frames=1, resolve_after_frames=1),
    )
    engine = PatternEngine("run-1", [pattern])

    engine.process(_event(0, [_person([0, 0, 100, 200]), _person([300, 0, 400, 200])]))
    # f1: todos cubiertos -> resuelve (fin del episodio 1)
    engine.process(
        _event(1, [_person([0, 0, 100, 200]), _helmet([40, 10, 60, 30])])
    )
    # f2: una persona sin casco -> episodio 2
    result = engine.process(_event(2, [_person([0, 0, 100, 200])]))

    assert result.alerts[0].subjects_in_evidence_max == 1
