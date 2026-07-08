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


def test_backend_confidence_floor_is_min_of_all_thresholds(tmp_path) -> None:
    # Un umbral por clase menor al confidence global debe bajar el piso del
    # backend; si no, el modelo descartaria esas detecciones antes del
    # postproceso y el umbral por clase seria un no-op.
    pytest.importorskip("cv2", reason="generator requiere el extra [labs]")
    from eovrt_labs.perception.generator import GenerationConfig, _backend_confidence

    config = GenerationConfig(
        input_path=tmp_path,
        output_path=tmp_path / "out.jsonl",
        confidence=0.25,
        tuning=TuningConfig(vest_confidence=0.20),
    )
    assert _backend_confidence(config) == 0.20

    config_default = GenerationConfig(
        input_path=tmp_path,
        output_path=tmp_path / "out.jsonl",
    )
    assert _backend_confidence(config_default) == 0.25
