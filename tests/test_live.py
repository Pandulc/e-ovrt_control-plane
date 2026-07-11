from pathlib import Path

import pytest
import yaml

from eovrt_control.config import load_replay_config
from eovrt_control.contracts.media import DetectionEvent
from eovrt_control.runtime.live import build_bus_source, run_live
from eovrt_control.sources.memory import MemorySource

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATTERNS = _REPO_ROOT / "configs/patterns/cr01_cr02_v1.yaml"


def _live_config(tmp_path: Path, **bus_overrides) -> Path:
    bus = {
        "endpoint": "tcp://127.0.0.1:5557",
        "hwm": 1000,
        "finish": {"signal": "run_lifecycle", "poll_url": None, "poll_interval_s": 5},
    }
    bus.update(bus_overrides)
    config_path = tmp_path / "live.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "run": {
                    "id": "control-live",
                    "scenario": "EBE",
                    "name": "live",
                    "experiment_id": "exp-1",
                },
                "input": {"type": "bus", "bus": bus},
                "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
                "outputs": {"base_dir": str(tmp_path / "runs")},
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _event(unit_id: str) -> DetectionEvent:
    return DetectionEvent.model_validate(
        {
            "run_id": "media-run",
            "unit_id": unit_id,
            "source": {
                "source_id": "cam-1",
                "source_type": "video_frame",
                "frame_index": 0,
                "timestamp_ms": 0.0,
                "width": 640,
                "height": 480,
            },
            "model": {"name": "mock", "device": "cpu"},
            "prompts": {"prompt_set_id": "cr01_cr02_v1"},
            "detections": [
                {
                    "detection_id": "p1",
                    "label": "person",
                    "prompt_id": "person",
                    "confidence": 0.9,
                    "bbox_xyxy": [100, 100, 220, 420],
                }
            ],
        }
    )


def test_run_live_over_injected_source_writes_the_same_artifacts(tmp_path) -> None:
    summary = run_live(_live_config(tmp_path), source=MemorySource([_event("u1")]))

    assert summary.units_processed == 1
    assert summary.alerts_count == 1
    assert summary.source == "memory"
    assert summary.media_run_id == "media-run"
    assert summary.experiment_id == "exp-1"
    assert summary.scenario == "EBE"
    run_dir = tmp_path / "runs" / "control-live"
    for name in ("summary.json", "alerts.jsonl", "pattern_events.jsonl", "metrics.jsonl"):
        assert (run_dir / name).exists()


def test_run_live_marks_run_degraded_when_the_bus_dropped_events(tmp_path) -> None:
    class _LossySource(MemorySource):
        kind = "bus"

        @property
        def dropped_events(self) -> int:
            return 3

    summary = run_live(_live_config(tmp_path), source=_LossySource([_event("u1")]))

    assert summary.bus_dropped_events == 3
    assert summary.degraded is True
    assert "bus_dropped_events" in summary.degradation_causes


def test_build_bus_source_reads_endpoint_and_finish_from_config(tmp_path) -> None:
    config = load_replay_config(_live_config(tmp_path, endpoint="tcp://127.0.0.1:5599"))

    source = build_bus_source(config, "control-live")
    try:
        assert source.endpoint == "tcp://127.0.0.1:5599"
        assert source.kind == "bus"
    finally:
        source.close()


def test_bus_config_requires_the_bus_section(tmp_path) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "run": {"name": "x"},
                "input": {"type": "bus"},
                "patterns": {"file": str(_PATTERNS)},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="input.bus"):
        load_replay_config(config_path)


def test_jsonl_config_requires_a_path(tmp_path) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "run": {"name": "x"},
                "input": {"type": "media_jsonl"},
                "patterns": {"file": str(_PATTERNS)},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="input.path"):
        load_replay_config(config_path)
