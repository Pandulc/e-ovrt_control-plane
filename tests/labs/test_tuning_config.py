"""Tests de carga de TuningConfig (labs)."""

from __future__ import annotations

import pytest

from eovrt_labs.perception.tuning import TuningConfig, load_tuning_config


def test_load_tuning_returns_defaults_when_path_is_none() -> None:
    tuning = load_tuning_config(None)
    assert tuning == TuningConfig()
    assert tuning.person_confidence == 0.35
    assert tuning.image_folder_frame_interval_ms == 500.0


def test_load_tuning_applies_overrides_and_keeps_defaults(tmp_path) -> None:
    path = tmp_path / "tuning.yaml"
    path.write_text(
        "person_confidence: 0.5\ntrack_appearance: false\nimage_size: 800\n",
        encoding="utf-8",
    )

    tuning = load_tuning_config(path)

    assert tuning.person_confidence == 0.5
    assert tuning.track_appearance is False
    assert tuning.image_size == 800
    # Los no especificados conservan el default historico.
    assert tuning.helmet_confidence == 0.25
    assert tuning.nms_iou_person == 0.65


def test_load_tuning_rejects_unknown_keys(tmp_path) -> None:
    path = tmp_path / "tuning.yaml"
    path.write_text("person_confidence: 0.4\nbogus_key: 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no reconocidas"):
        load_tuning_config(path)
