from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from eovrt_control.config import load_replay_config_data
from eovrt_control.service.run_ids import is_valid_run_id, new_control_run_id, require_valid_run_id
from eovrt_control.service.run_request import ControlRunRequest
from eovrt_control.service.settings import ServiceSettings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATTERNS = _REPO_ROOT / "configs/patterns/cr01_cr02_v1.yaml"


def _payload(tmp_path: Path, **overrides) -> dict:
    data = {
        "run": {"scenario": "DBE", "name": "por_payload"},
        "input": {"type": "media_jsonl", "path": str(tmp_path / "detections.jsonl")},
        "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
        "outputs": {"base_dir": str(tmp_path / "runs")},
    }
    data.update(overrides)
    return data


def test_load_config_data_resolves_the_patterns_file(tmp_path) -> None:
    config = load_replay_config_data(_payload(tmp_path))

    assert config.patterns_file is not None
    assert config.patterns_file.pattern_set.id == "cr01_cr02_v1"
    assert config.config_path is None


def test_config_by_payload_rejects_a_relative_patterns_path(tmp_path) -> None:
    data = _payload(tmp_path)
    data["patterns"]["file"] = "configs/patterns/cr01_cr02_v1.yaml"

    with pytest.raises(ValueError, match="ruta absoluta"):
        load_replay_config_data(data)


def test_config_by_payload_rejects_a_relative_input_path(tmp_path) -> None:
    data = _payload(tmp_path)
    data["input"]["path"] = "runs/latest/detections.jsonl"

    with pytest.raises(ValueError, match="ruta absoluta"):
        load_replay_config_data(data)


def test_request_requires_exactly_one_config_source() -> None:
    with pytest.raises(ValidationError, match="config_path"):
        ControlRunRequest(mode="replay")
    with pytest.raises(ValidationError, match="config_path"):
        ControlRunRequest(mode="replay", config_path="/tmp/a.yaml", config={"run": {}})


def test_request_accepts_either_source() -> None:
    assert ControlRunRequest(mode="replay", config_path="/tmp/a.yaml").config is None
    assert ControlRunRequest(mode="live", config={"run": {}}).config_path is None


def test_request_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ControlRunRequest(mode="replay", config_path="/tmp/a.yaml", modo="replay")


def test_run_id_validation_rejects_path_traversal() -> None:
    assert is_valid_run_id("run_2026-07-10_abc")
    assert not is_valid_run_id("../etc/passwd")
    assert not is_valid_run_id("a/b")
    with pytest.raises(HTTPException) as exc:
        require_valid_run_id("../x")
    assert exc.value.status_code == 404


def test_new_control_run_id_respects_an_explicit_id(tmp_path) -> None:
    data = _payload(tmp_path)
    data["run"]["id"] = "mi-corrida"
    config = load_replay_config_data(data)

    assert new_control_run_id(config) == "mi-corrida"


def test_new_control_run_id_is_unique_when_not_declared(tmp_path) -> None:
    config = load_replay_config_data(_payload(tmp_path))

    first, second = new_control_run_id(config), new_control_run_id(config)

    assert first != second, "dos corridas en el mismo segundo colisionarian"
    assert first.startswith("por_payload_")
    assert is_valid_run_id(first)


def test_new_control_run_id_rejects_an_explicit_id_with_path_traversal(tmp_path) -> None:
    data = _payload(tmp_path)
    data["run"]["id"] = "../../etc/evil"
    config = load_replay_config_data(data)

    with pytest.raises(ValueError, match="run.id"):
        new_control_run_id(config)


def test_new_control_run_id_rejects_a_name_with_path_traversal(tmp_path) -> None:
    data = _payload(tmp_path)
    data["run"]["name"] = "../evil"
    config = load_replay_config_data(data)

    with pytest.raises(ValueError, match="run.name"):
        new_control_run_id(config)


def test_new_control_run_id_rejects_a_name_with_spaces(tmp_path) -> None:
    data = _payload(tmp_path)
    data["run"]["name"] = "con espacios"
    config = load_replay_config_data(data)

    with pytest.raises(ValueError):
        new_control_run_id(config)


@pytest.mark.parametrize("hostile_name", ["../evil", "a/b", "con espacios", "x y", ""])
def test_new_control_run_id_never_escapes_runs_dir(tmp_path, hostile_name) -> None:
    runs_dir = tmp_path / "runs"
    data = _payload(tmp_path, run={"scenario": "DBE", "name": hostile_name})
    config = load_replay_config_data(data)

    try:
        run_id = new_control_run_id(config)
    except ValueError:
        return

    assert (runs_dir / run_id).resolve().is_relative_to(runs_dir.resolve())


def test_new_control_run_id_legit_cases_still_work(tmp_path) -> None:
    data = _payload(tmp_path)
    data["run"]["id"] = "mi-corrida"
    config = load_replay_config_data(data)
    assert new_control_run_id(config) == "mi-corrida"

    config = load_replay_config_data(_payload(tmp_path))
    run_id = new_control_run_id(config)
    assert run_id.startswith("por_payload_")
    assert is_valid_run_id(run_id)


def test_settings_read_runs_dir_from_env(tmp_path) -> None:
    settings = ServiceSettings.from_env({"EOVRT_CONTROL_RUNS_DIR": str(tmp_path / "r")})

    assert settings.runs_dir == (tmp_path / "r").resolve()
    assert ServiceSettings.from_env({}).runs_dir == Path("runs").resolve()
