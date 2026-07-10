from pathlib import Path

import pytest
from pydantic import ValidationError

from eovrt_control.config import PatternDefinition, PatternRegionConfig, load_replay_config
from eovrt_control.contracts.media import Detection


def test_load_replay_config_resolves_patterns_file() -> None:
    config = load_replay_config(Path("configs/replay_dbe_cr01_cr02.yaml"))

    assert config.patterns_file is not None
    assert config.patterns_file.pattern_set.id == "cr01_cr02_v1"
    assert [pattern.id for pattern in config.patterns_file.active_patterns(["CR-01"])] == ["CR-01"]


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
