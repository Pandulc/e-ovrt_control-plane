from pathlib import Path

from eovrt_control.config import load_replay_config


def test_load_replay_config_resolves_patterns_file() -> None:
    config = load_replay_config(Path("configs/replay_dbe_cr01_cr02.yaml"))

    assert config.patterns_file is not None
    assert config.patterns_file.pattern_set.id == "cr01_cr02_v1"
    assert [pattern.id for pattern in config.patterns_file.active_patterns(["CR-01"])] == ["CR-01"]

