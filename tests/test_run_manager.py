import json
from pathlib import Path

import pytest
import yaml

from eovrt_control.service.run_manager import RunBusyError, RunManager, UnknownRunError
from eovrt_control.service.run_request import ControlRunRequest
from eovrt_control.service.settings import ServiceSettings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATTERNS = _REPO_ROOT / "configs/patterns/cr01_cr02_v1.yaml"


def _event(unit_id: str) -> dict:
    return {
        "run_id": "media-run",
        "unit_id": unit_id,
        "source": {"source_id": "cam-1", "source_type": "video", "frame_index": 0,
                   "timestamp_ms": 0.0, "width": 640, "height": 480},
        "model": {"name": "mock", "device": "cpu"},
        "prompts": {"prompt_set_id": "cr01_cr02_v1"},
        "detections": [{"detection_id": "p1", "label": "person", "prompt_id": "person",
                        "confidence": 0.9, "bbox_xyxy": [100, 100, 220, 420]}],
    }


def _detections(tmp_path: Path) -> Path:
    path = tmp_path / "detections.jsonl"
    path.write_text(json.dumps(_event("u1")) + "\n", encoding="utf-8")
    return path


def _payload(tmp_path: Path) -> dict:
    return {
        "run": {"id": "manager-run", "scenario": "DBE", "name": "mgr"},
        "input": {"type": "media_jsonl", "path": str(_detections(tmp_path))},
        "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
    }


@pytest.fixture()
def manager(tmp_path):
    m = RunManager(ServiceSettings(runs_dir=tmp_path / "runs"))
    yield m
    m.shutdown()
    m.join_active(timeout=15.0)


@pytest.fixture()
def bus_endpoint():
    """Un XPUB bindeado que nunca publica: el `BusSource` se suscribe y se queda esperando."""
    import socket

    import zmq

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    endpoint = f"tcp://127.0.0.1:{port}"
    sock = zmq.Context.instance().socket(zmq.XPUB)
    sock.setsockopt(zmq.LINGER, 0)
    sock.bind(endpoint)
    yield endpoint
    sock.close(linger=0)


def test_replay_by_payload_runs_and_reports_succeeded(manager, tmp_path) -> None:
    run_id = manager.start_run(ControlRunRequest(mode="replay", config=_payload(tmp_path)))
    manager.join_active(timeout=30.0)

    assert run_id == "manager-run"
    state = manager.get(run_id)
    assert state["status"] == "succeeded"
    assert state["summary"]["alerts_count"] == 1
    assert state["summary"]["source"] == "jsonl"


def test_replay_by_reference_resolves_paths_against_the_yaml(manager, tmp_path) -> None:
    _detections(tmp_path)
    config_path = tmp_path / "replay.yaml"
    config_path.write_text(
        yaml.safe_dump({
            "run": {"id": "ref-run", "scenario": "DBE", "name": "ref"},
            "input": {"type": "media_jsonl", "path": "detections.jsonl"},  # relativa al YAML
            "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
        }),
        encoding="utf-8",
    )

    manager.start_run(ControlRunRequest(mode="replay", config_path=str(config_path)))
    manager.join_active(timeout=30.0)

    assert manager.get("ref-run")["summary"]["units_processed"] == 1


def test_outputs_base_dir_is_forced_to_the_service_runs_dir(manager, tmp_path) -> None:
    payload = _payload(tmp_path)
    payload["outputs"] = {"base_dir": "/tmp/deberia-ser-ignorado"}

    manager.start_run(ControlRunRequest(mode="replay", config=payload))
    manager.join_active(timeout=30.0)

    assert (tmp_path / "runs" / "manager-run" / "summary.json").exists()


def test_outputs_base_dir_by_reference_is_also_forced_to_the_service_runs_dir(
    manager, tmp_path
) -> None:
    """Un YAML de referencia con `outputs.base_dir` propio no debe poder escribir
    fuera del `runs_dir` del servicio: el dueno del `runs_dir` es el despliegue."""
    _detections(tmp_path)
    otro_dir = tmp_path / "otro-lugar"
    config_path = tmp_path / "replay.yaml"
    config_path.write_text(
        yaml.safe_dump({
            "run": {"id": "ref-run-2", "scenario": "DBE", "name": "ref2"},
            "input": {"type": "media_jsonl", "path": "detections.jsonl"},
            "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
            "outputs": {"base_dir": str(otro_dir)},
        }),
        encoding="utf-8",
    )

    manager.start_run(ControlRunRequest(mode="replay", config_path=str(config_path)))
    manager.join_active(timeout=30.0)

    state = manager.get("ref-run-2")
    assert state["status"] == "succeeded"
    assert (tmp_path / "runs" / "ref-run-2" / "summary.json").exists()
    assert not otro_dir.exists()


def test_experiment_id_from_the_request_overrides_the_config(manager, tmp_path) -> None:
    manager.start_run(
        ControlRunRequest(mode="replay", config=_payload(tmp_path), experiment_id="exp-9")
    )
    manager.join_active(timeout=30.0)

    assert manager.get("manager-run")["summary"]["experiment_id"] == "exp-9"


def _idle_live_payload(endpoint: str, idle_timeout_s: float = 2.0) -> dict:
    """Corrida live contra un bus que nunca publica: se queda activa hasta el idle timeout.

    Es la unica forma DETERMINISTA de mantener un run activo mientras el test hace
    otra cosa. Un replay sobre un archivo grande es una carrera (el motor procesa
    decenas de miles de unidades por segundo y puede terminar antes del segundo POST.
    """
    return {
        "run": {"id": "idle-run", "scenario": "EBE", "name": "idle"},
        "input": {"type": "bus", "bus": {"endpoint": endpoint, "recv_timeout_ms": 100,
                                         "idle_timeout_s": idle_timeout_s}},
        "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
    }


def test_second_run_while_active_raises_run_busy(manager, tmp_path, bus_endpoint) -> None:
    manager.start_run(ControlRunRequest(mode="live", config=_idle_live_payload(bus_endpoint)))
    try:
        with pytest.raises(RunBusyError) as exc:
            manager.start_run(ControlRunRequest(mode="replay", config=_payload(tmp_path)))
        assert exc.value.active_run_id == "idle-run"
    finally:
        manager.shutdown()
        manager.join_active(timeout=30.0)


def test_shutdown_unblocks_a_live_run_cooperatively(manager, bus_endpoint) -> None:
    """`shutdown()` NO cierra el socket desde este hilo (SIGABRT de libzmq): levanta
    una bandera y el hilo de la corrida sale solo."""
    manager.start_run(
        ControlRunRequest(mode="live", config=_idle_live_payload(bus_endpoint, idle_timeout_s=300))
    )

    manager.shutdown()
    manager.join_active(timeout=15.0)

    assert manager.get("idle-run")["status"] == "succeeded"
    with pytest.raises(UnknownRunError):
        manager.current()


def test_base_exception_in_thread_does_not_leave_the_slot_taken(
    manager, tmp_path, monkeypatch
) -> None:
    """Un BaseException que no hereda de Exception (KeyboardInterrupt, SystemExit,
    GeneratorExit) igual debe soltar el slot activo, via el `finally` de `_execute`."""

    def _boom(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr("eovrt_control.service.run_manager.run_replay_from_config", _boom)

    manager.start_run(ControlRunRequest(mode="replay", config=_payload(tmp_path)))
    manager.join_active(timeout=30.0)

    with pytest.raises(UnknownRunError):
        manager.current()

    # El slot quedo libre: una corrida nueva funciona sin RunBusyError.
    monkeypatch.undo()
    manager.start_run(ControlRunRequest(mode="replay", config=_payload(tmp_path)))
    manager.join_active(timeout=30.0)
    assert manager.get("manager-run")["status"] == "succeeded"


def test_mode_must_match_the_input_type(manager, tmp_path) -> None:
    with pytest.raises(ValueError, match="mode='live'"):
        manager.start_run(ControlRunRequest(mode="live", config=_payload(tmp_path)))


def test_unknown_run_raises(manager) -> None:
    with pytest.raises(UnknownRunError):
        manager.get("no-existe")
    with pytest.raises(UnknownRunError):
        manager.current()
    with pytest.raises(UnknownRunError):
        manager.effective_config()


def test_alerts_are_read_from_the_run_artifacts(manager, tmp_path) -> None:
    manager.start_run(ControlRunRequest(mode="replay", config=_payload(tmp_path)))
    manager.join_active(timeout=30.0)

    alerts = manager.alerts("manager-run")

    assert len(alerts) == 1
    assert alerts[0]["pattern_id"] == "CR-01"
    assert alerts[0]["control_run_id"] == "manager-run"
    assert manager.alerts("manager-run", limit=0) == []


def test_alerts_skips_a_corrupted_line(manager, tmp_path) -> None:
    manager.start_run(ControlRunRequest(mode="replay", config=_payload(tmp_path)))
    manager.join_active(timeout=30.0)

    alerts_path = tmp_path / "runs" / "manager-run" / "alerts.jsonl"
    with alerts_path.open("a", encoding="utf-8") as fh:
        fh.write("{esto no es json valido\n")

    alerts = manager.alerts("manager-run")

    assert len(alerts) == 1
    assert alerts[0]["pattern_id"] == "CR-01"


def test_alerts_with_negative_limit_returns_empty_list(manager, tmp_path) -> None:
    manager.start_run(ControlRunRequest(mode="replay", config=_payload(tmp_path)))
    manager.join_active(timeout=30.0)

    # La corrida deja UNA alerta, y `[x][:-1]` tambien es `[]`: con una sola alerta
    # este test pasa con y sin el fix. Duplicamos la linea para que discrimine de
    # verdad entre `rows[:limit]` (devolveria 1) y `rows[:max(limit, 0)]` (devuelve 0).
    alerts_path = tmp_path / "runs" / "manager-run" / "alerts.jsonl"
    original = alerts_path.read_text(encoding="utf-8")
    alerts_path.write_text(original + original, encoding="utf-8")
    assert len(manager.alerts("manager-run")) == 2

    assert manager.alerts("manager-run", limit=-1) == []
    assert manager.alerts("manager-run", limit=0) == []
    assert len(manager.alerts("manager-run", limit=1)) == 1


def test_effective_config_is_available_after_the_run(manager, tmp_path) -> None:
    manager.start_run(ControlRunRequest(mode="replay", config=_payload(tmp_path)))
    manager.join_active(timeout=30.0)

    config = manager.effective_config()

    assert config["run"]["id"] == "manager-run"
    assert config["input"]["type"] == "media_jsonl"


def test_reusing_a_finished_run_id_is_rejected_and_keeps_the_old_artifacts(
    manager, tmp_path
) -> None:
    """Revision final, hallazgo 1: reusar un `run.id` terminado no debe truncar los
    artefactos de la corrida vieja ni dejar el slot tomado tras el rechazo."""
    manager.start_run(ControlRunRequest(mode="replay", config=_payload(tmp_path)))
    manager.join_active(timeout=30.0)
    assert manager.get("manager-run")["status"] == "succeeded"

    alerts_path = tmp_path / "runs" / "manager-run" / "alerts.jsonl"
    summary_path = tmp_path / "runs" / "manager-run" / "summary.json"
    original_alerts = alerts_path.read_text(encoding="utf-8")
    original_summary = summary_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="manager-run"):
        manager.start_run(ControlRunRequest(mode="replay", config=_payload(tmp_path)))

    # Los artefactos de la primera corrida siguen intactos: no se pisaron ni se
    # borraron al rechazar la segunda.
    assert alerts_path.read_text(encoding="utf-8") == original_alerts
    assert summary_path.read_text(encoding="utf-8") == original_summary

    # El slot no quedo tomado por el rechazo.
    with pytest.raises(UnknownRunError):
        manager.current()

    # Un run.id nuevo (no usado) sigue funcionando despues del rechazo.
    payload = _payload(tmp_path)
    payload["run"]["id"] = "manager-run-2"
    manager.start_run(ControlRunRequest(mode="replay", config=payload))
    manager.join_active(timeout=30.0)
    assert manager.get("manager-run-2")["status"] == "succeeded"


def test_get_exposes_degraded_at_the_top_level(manager, tmp_path) -> None:
    """Revision final, hallazgo 3: un poller que solo mira `status`/`degraded` de
    primer nivel tiene que poder ver la degradacion sin bucear en `summary`."""
    run_dir = tmp_path / "runs" / "degraded-run"
    run_dir.mkdir(parents=True)
    summary = {
        "schema_version": "control.summary.v1",
        "control_run_id": "degraded-run",
        "scenario": "EBE",
        "pattern_set_id": "cr01_cr02_v1",
        "active_pattern_ids": ["CR-01"],
        "units_processed": 1,
        "units_failed": 0,
        "pattern_events_count": 0,
        "alerts_count": 0,
        "errors_count": 0,
        "avg_processing_ms": 0.0,
        "output_files": {},
        "degraded": True,
        "degradation_causes": ["bus_dropped_events"],
        "started_at": "2026-07-10T00:00:00Z",
        "finished_at": "2026-07-10T00:00:01Z",
    }
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    state = manager.get("degraded-run")

    assert state["degraded"] is True
    assert state["degradation_causes"] == ["bus_dropped_events"]


def test_a_config_error_leaves_no_active_run_behind(manager, tmp_path) -> None:
    """El fallo ocurre al preparar la corrida, bajo el lock: el slot no queda tomado."""
    payload = _payload(tmp_path)
    payload["patterns"]["active_ids"] = ["NO-EXISTE"]

    with pytest.raises(ValueError):
        manager.start_run(ControlRunRequest(mode="replay", config=payload))

    with pytest.raises(UnknownRunError):
        manager.current()
    # Y el servicio sigue aceptando corridas.
    manager.start_run(ControlRunRequest(mode="replay", config=_payload(tmp_path)))
    manager.join_active(timeout=30.0)
    assert manager.get("manager-run")["status"] == "succeeded"
