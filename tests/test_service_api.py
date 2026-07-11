import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eovrt_control.service.app import create_app
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


def _payload(tmp_path: Path, run_id: str = "api-run", count: int = 1) -> dict:
    path = tmp_path / f"{run_id}.jsonl"
    path.write_text(
        "\n".join(json.dumps(_event(f"u{i}")) for i in range(count)) + "\n", encoding="utf-8"
    )
    return {
        "run": {"id": run_id, "scenario": "DBE", "name": "api"},
        "input": {"type": "media_jsonl", "path": str(path)},
        "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
    }


@pytest.fixture()
def client(tmp_path):
    app = create_app(ServiceSettings(runs_dir=tmp_path / "runs"))
    with TestClient(app) as c:
        yield c


def test_healthz_and_readyz(client) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


def test_post_run_returns_201_and_the_run_completes(client, tmp_path) -> None:
    response = client.post("/api/runs", json={"mode": "replay", "config": _payload(tmp_path)})

    assert response.status_code == 201
    run_id = response.json()["control_run_id"]
    assert run_id == "api-run"

    client.app.state.manager.join_active(timeout=30.0)
    state = client.get(f"/api/runs/{run_id}")
    assert state.status_code == 200
    assert state.json()["status"] == "succeeded"
    assert state.json()["summary"]["alerts_count"] == 1


def _idle_live_payload(endpoint: str) -> dict:
    """Corrida live contra un bus que nunca publica: queda activa de forma DETERMINISTA.

    Un replay sobre un archivo grande seria una carrera (el motor procesa decenas de
    miles de unidades por segundo y puede terminar antes del segundo request).
    """
    return {
        "run": {"id": "idle-run", "scenario": "EBE", "name": "idle"},
        "input": {"type": "bus", "bus": {"endpoint": endpoint, "recv_timeout_ms": 100,
                                         "idle_timeout_s": 30}},
        "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
    }


@pytest.fixture()
def bus_endpoint():
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


def test_second_run_while_active_returns_409(client, tmp_path, bus_endpoint) -> None:
    client.post("/api/runs", json={"mode": "live", "config": _idle_live_payload(bus_endpoint)})
    try:
        conflict = client.post("/api/runs", json={"mode": "replay", "config": _payload(tmp_path)})
        assert conflict.status_code == 409
        assert conflict.json()["active_run_id"] == "idle-run"
    finally:
        client.app.state.manager.shutdown()
        client.app.state.manager.join_active(timeout=30.0)


def test_invalid_config_returns_422(client, tmp_path) -> None:
    payload = _payload(tmp_path)
    payload["patterns"]["file"] = "configs/relativa.yaml"  # por payload debe ser absoluta

    response = client.post("/api/runs", json={"mode": "replay", "config": payload})

    assert response.status_code == 422
    assert "absoluta" in response.json()["detail"]


def test_mode_live_with_a_jsonl_config_returns_422(client, tmp_path) -> None:
    response = client.post("/api/runs", json={"mode": "live", "config": _payload(tmp_path)})

    assert response.status_code == 422
    assert "mode='live'" in response.json()["detail"]


def test_unknown_field_in_the_body_returns_422(client, tmp_path) -> None:
    response = client.post("/api/runs", json={"mode": "replay", "configuracion": {}})
    assert response.status_code == 422


def test_live_with_a_malformed_bus_endpoint_returns_422(client) -> None:
    """Hallazgo 1, caso 1: `connect()` de ZMQ levanta `zmq.ZMQError`, no `ValueError`
    ni `FileNotFoundError`; sin traducir, el router lo dejaba pasar como 500."""
    payload = {
        "run": {"id": "bad-endpoint-run", "scenario": "EBE", "name": "bad-endpoint"},
        "input": {"type": "bus", "bus": {"endpoint": "no-es-un-endpoint"}},
        "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
    }

    response = client.post("/api/runs", json={"mode": "live", "config": payload})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "ZMQError" in detail or "endpoint" in detail.lower()


def test_config_path_to_a_corrupt_yaml_returns_422(client, tmp_path) -> None:
    """Hallazgo 1, caso 2: `yaml.safe_load` levanta `yaml.YAMLError`, no `ValueError`
    ni `FileNotFoundError`; sin traducir, el router lo dejaba pasar como 500."""
    config_path = tmp_path / "corrupt.yaml"
    config_path.write_text("run: [unclosed", encoding="utf-8")

    response = client.post(
        "/api/runs", json={"mode": "replay", "config_path": str(config_path)}
    )

    assert response.status_code == 422


def test_config_path_to_a_missing_file_returns_422(client, tmp_path) -> None:
    """Caso hermano del anterior (ya funcionaba via `FileNotFoundError`): se agrega
    como regresion, no se debe perder al traducir `OSError` en `start_run`."""
    missing = tmp_path / "no-existe.yaml"

    response = client.post(
        "/api/runs", json={"mode": "replay", "config_path": str(missing)}
    )

    assert response.status_code == 422


def test_an_internal_bug_in_start_run_is_not_downgraded_to_422(client, monkeypatch) -> None:
    """Un `RuntimeError` por un bug nuestro (no de traduccion de config) debe seguir
    siendo un error del SERVIDOR, nunca un 422. Se usa `raise_server_exceptions=False`
    en un client local para poder leer el 500 en vez de que la excepcion se propague
    a traves del `TestClient` (ambas formas son validas; esta permite seguir
    inspeccionando el status_code como en el resto del archivo)."""
    from eovrt_control.service.run_manager import RunManager

    def _boom(self, request):
        raise RuntimeError("bug interno")

    monkeypatch.setattr(RunManager, "start_run", _boom)

    with TestClient(client.app, raise_server_exceptions=False) as raw_client:
        response = raw_client.post(
            "/api/runs",
            json={
                "mode": "replay",
                "config": {
                    "run": {"id": "x", "scenario": "DBE", "name": "x"},
                    "input": {"type": "media_jsonl", "path": "/no/importa.jsonl"},
                    "patterns": {"file": str(_PATTERNS)},
                },
            },
        )

    assert response.status_code != 422
    assert response.status_code == 500


def test_reusing_a_finished_run_id_returns_422(client, tmp_path) -> None:
    """Revision final, hallazgo 1: reusar un `run.id` terminado es error del CLIENTE
    (422), no un 500 ni un 201 que trunca los artefactos de la corrida vieja."""
    first = client.post(
        "/api/runs", json={"mode": "replay", "config": _payload(tmp_path, run_id="reuse-run")}
    )
    assert first.status_code == 201
    client.app.state.manager.join_active(timeout=30.0)
    assert client.get("/api/runs/reuse-run").status_code == 200

    response = client.post(
        "/api/runs", json={"mode": "replay", "config": _payload(tmp_path, run_id="reuse-run")}
    )

    assert response.status_code == 422
    assert "reuse-run" in response.json()["detail"]


def test_current_returns_404_when_no_run_is_active(client) -> None:
    assert client.get("/api/runs/current").status_code == 404


def test_current_reports_the_active_run_and_its_progress(client, bus_endpoint) -> None:
    client.post("/api/runs", json={"mode": "live", "config": _idle_live_payload(bus_endpoint)})
    try:
        current = client.get("/api/runs/current")
        assert current.status_code == 200
        body = current.json()
        assert body["control_run_id"] == "idle-run"
        assert body["status"] == "running"
        assert body["mode"] == "live"
        assert set(body["progress"]) == {
            "units_processed", "units_failed", "errors_count",
            "pattern_events_count", "alerts_count", "bus_dropped_events",
        }
        assert body["progress"]["units_processed"] == 0
    finally:
        client.app.state.manager.shutdown()
        client.app.state.manager.join_active(timeout=30.0)


def test_unknown_run_returns_404(client) -> None:
    assert client.get("/api/runs/no-existe").status_code == 404
    assert client.get("/api/runs/no-existe/alerts").status_code == 404


def test_run_id_with_a_slash_is_rejected_by_fastapi_routing_not_the_guard(client) -> None:
    """`..%2F..%2Fetc` decodifica a `../../etc`, que trae un `/`: el converter de
    path por defecto de `{run_id}` no lo acepta como un solo segmento, asi que
    Starlette ni siquiera matchea la ruta y devuelve su 404 NATIVO. Esto pasa
    ANTES de que `require_valid_run_id` corra: el detail generico lo confirma."""
    response = client.get("/api/runs/..%2F..%2Fetc/alerts")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


@pytest.mark.parametrize(
    "run_id_encoded",
    [
        "%2e%2e",  # ".." sin barra: es UN segmento, matchea el converter por defecto
        "a%3Bb",  # "a;b": ';' no es '/', tambien es un segmento valido para la ruta
        "con%20espacios",
        "%00",
    ],
)
def test_run_id_that_reaches_the_guard_returns_its_custom_404(client, run_id_encoded) -> None:
    """Casos que SI llegan a `require_valid_run_id` (a diferencia del caso con `/`
    de arriba): la ruta matchea porque el segmento no contiene una barra, y el
    guard es el que produce el 404, con su detail propio `Run desconocido: ...`."""
    response = client.get(f"/api/runs/{run_id_encoded}/alerts")

    assert response.status_code == 404
    assert response.json()["detail"].startswith("Run desconocido: ")


def test_alerts_endpoint_serves_the_alert_events(client, tmp_path) -> None:
    client.post("/api/runs", json={"mode": "replay", "config": _payload(tmp_path)})
    client.app.state.manager.join_active(timeout=30.0)

    response = client.get("/api/runs/api-run/alerts")

    assert response.status_code == 200
    alerts = response.json()
    assert len(alerts) == 1
    assert alerts[0]["pattern_id"] == "CR-01"
    assert alerts[0]["schema_version"] == "control.alert.v1"
    assert client.get("/api/runs/api-run/alerts?limit=0").json() == []


def test_config_endpoint_returns_404_before_any_run_and_the_config_after(client, tmp_path) -> None:
    assert client.get("/api/config").status_code == 404

    client.post("/api/runs", json={"mode": "replay", "config": _payload(tmp_path)})
    client.app.state.manager.join_active(timeout=30.0)

    config = client.get("/api/config")
    assert config.status_code == 200
    assert config.json()["run"]["id"] == "api-run"
