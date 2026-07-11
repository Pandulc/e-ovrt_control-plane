from pathlib import Path

import pytest
from pydantic import ValidationError

from eovrt_control.config import (
    PatternDefinition,
    PatternRegionConfig,
    load_patterns_file,
    load_replay_config,
)
from eovrt_control.contracts.media import Detection


def test_load_replay_config_resolves_patterns_file() -> None:
    config = load_replay_config(Path("configs/replay_dbe_cr01_cr02.yaml"))

    assert config.patterns_file is not None
    assert config.patterns_file.pattern_set.id == "cr01_cr02_v1"
    assert [pattern.id for pattern in config.patterns_file.active_patterns(["CR-01"])] == ["CR-01"]


def test_cr01_cr02_v2_pattern_set_matches_informe() -> None:
    """Task 6: pattern set oficial de plataforma alineado al informe (spec 41 SS7).

    Valores verbatim (Tabla 24/D.4): CR-01 severidad high, confirm 4000ms,
    resolve 2000ms; CR-02 severidad medium, confirm 7000ms, resolve 3000ms;
    ambos granularity scene, sin cooldown (ADR-011) ni memoria de cobertura
    (ADR-012, inaplicable bajo escena).
    """
    pf = load_patterns_file("configs/patterns/cr01_cr02_v2.yaml")

    assert pf.pattern_set.id == "cr01_cr02_v2"
    by_id = {p.condition_id: p for p in pf.pattern_set.patterns}
    cr01, cr02 = by_id["CR-01"], by_id["CR-02"]

    assert cr01.severity == "high"
    assert cr01.timing.confirm_after_ms == 4000.0
    assert cr01.timing.resolve_after_ms == 2000.0
    assert cr01.granularity == "scene"

    assert cr02.severity == "medium"
    assert cr02.timing.confirm_after_ms == 7000.0
    assert cr02.timing.resolve_after_ms == 3000.0
    assert cr02.granularity == "scene"

    for pattern in (cr01, cr02):
        assert pattern.timing.realert_cooldown_ms is None
        assert pattern.timing.realert_cooldown_frames is None
        assert pattern.timing.coverage_memory_ms is None
        assert pattern.timing.coverage_memory_frames is None


def _pattern(**overrides) -> PatternDefinition:
    base = dict(
        id="CR-01",
        name="person_without_helmet",
        condition_id="CR-01",
        required_absent_class="helmet",
        region=PatternRegionConfig(type="upper_body"),
    )
    base.update(overrides)
    return PatternDefinition(**base)


def test_pattern_granularity_defaults_to_scene() -> None:
    assert _pattern().granularity == "scene"


def test_pattern_granularity_accepts_subject() -> None:
    assert _pattern(granularity="subject").granularity == "subject"


def test_pattern_granularity_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        _pattern(granularity="camera")


def test_detection_track_id_is_optional_and_defaults_to_none() -> None:
    detection = Detection(label="person", confidence=0.9, bbox_xyxy=[0, 0, 10, 20])
    assert detection.track_id is None


def test_detection_accepts_track_id() -> None:
    detection = Detection(
        label="person", confidence=0.9, bbox_xyxy=[0, 0, 10, 20], track_id="subject_001"
    )
    assert detection.track_id == "subject_001"
