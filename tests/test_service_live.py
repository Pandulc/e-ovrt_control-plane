"""Gate del servicio: corrida live disparada por la API, contra un bus ZeroMQ real."""

import json
import socket
import threading
from pathlib import Path

import msgpack
import pytest
import zmq
from fastapi.testclient import TestClient

from eovrt_control.service.app import create_app
from eovrt_control.service.run_manager import ActiveRun, RunManager
from eovrt_control.service.settings import ServiceSettings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATTERNS = _REPO_ROOT / "configs/patterns/cr01_cr02_v1.yaml"
_MEDIA_RUN_ID = "media-run-live"

# Un BusSource con los dos prefijos por default emite DOS notificaciones de
# suscripcion; drenar una sola devuelve antes de tiempo (slow joiner).
_EXPECTED_SUBSCRIPTIONS = 2


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _event(unit_id: str, frame_index: int) -> dict:
    return {
        "run_id": _MEDIA_RUN_ID,
        "unit_id": unit_id,
        "source": {"source_id": "cam-1", "source_type": "video", "frame_index": frame_index,
                   "timestamp_ms": frame_index * 100.0, "width": 640, "height": 480},
        "model": {"name": "mock", "device": "cpu"},
        "prompts": {"prompt_set_id": "cr01_cr02_v1"},
        "detections": [{"detection_id": "p1", "label": "person", "prompt_id": "person",
                        "confidence": 0.9, "bbox_xyxy": [100, 100, 220, 420]}],
    }


class _Publisher:
    """Publicador de prueba: pinea el wire format del media-plane sin importarlo."""

    def __init__(self, endpoint: str) -> None:
        self._sock = zmq.Context.instance().socket(zmq.XPUB)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.setsockopt(zmq.XPUB_VERBOSE, 1)
        self._sock.bind(endpoint)
        self._seq = 0

    def wait_for_subscriber(self, expected: int = _EXPECTED_SUBSCRIPTIONS, timeout_ms: int = 5000):
        poller = zmq.Poller()
        poller.register(self._sock, zmq.POLLIN)
        seen = 0
        while seen < expected:
            if not dict(poller.poll(timeout=timeout_ms)):
                raise AssertionError(f"solo llegaron {seen}/{expected} suscripciones")
            self._sock.recv()
            seen += 1

    def _send(self, topic: str, key: str, payload: bytes) -> None:
        envelope = msgpack.packb(
            {"schema_version": "bus.envelope.v1", "topic": topic, "key": key,
             "seq": self._seq, "ts_publish_ms": 0.0, "payload": payload},
            use_bin_type=True,
        )
        self._seq += 1
        self._sock.send_multipart([topic.encode("utf-8"), envelope])

    def publish_events(self, count: int) -> None:
        for i in range(count):
            payload = json.dumps(_event(f"frame_{i:04d}", i)).encode("utf-8")
            self._send(f"media.detection.v1.{_MEDIA_RUN_ID}", "cam-1", payload)

    def finish(self) -> None:
        payload = json.dumps({"schema_version": "run.lifecycle.v1", "event": "run_finished",
                              "media_run_id": _MEDIA_RUN_ID, "status": "succeeded"}).encode("utf-8")
        self._send(f"run.lifecycle.v1.{_MEDIA_RUN_ID}", _MEDIA_RUN_ID, payload)

    def close(self) -> None:
        self._sock.close(linger=0)


def _live_payload(endpoint: str) -> dict:
    return {
        "run": {"id": "live-api-run", "scenario": "EBE", "name": "live_api"},
        "input": {"type": "bus", "bus": {"endpoint": endpoint, "recv_timeout_ms": 200,
                                         "idle_timeout_s": 30}},
        "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
    }


@pytest.mark.integration
def test_live_run_by_api_consumes_every_event_and_closes_on_run_finished(tmp_path) -> None:
    """Prueba el camino feliz completo: 10 eventos, cierre por `run_finished`,
    0 drops, alertas emitidas.

    Lo que NO prueba: el orden entre el 201 y la suscripcion. `wait_for_subscriber()`
    bloquea antes de publicar nada, asi que absorbe cualquier retraso venga del
    hilo que venga (se verifico por mutacion: mover la suscripcion de `start_run`
    a `_execute`, o sea suscribirse DESPUES del 201 en el hilo de fondo, deja este
    test en verde igual). Para esa invariante de orden esta
    `test_the_201_response_implies_the_bus_source_is_already_subscribed`, que
    bloquea el hilo ejecutor y verifica el campo `subscribed` sin depender de
    timing de red.
    """
    endpoint = f"tcp://127.0.0.1:{_free_port()}"
    publisher = _Publisher(endpoint)
    app = create_app(ServiceSettings(runs_dir=tmp_path / "runs"))

    try:
        with TestClient(app) as client:
            # El POST devuelve 201 recien cuando el BusSource ya esta suscripto.
            response = client.post(
                "/api/runs",
                json={"mode": "live", "config": _live_payload(endpoint),
                      "experiment_id": "exp-live"},
            )
            assert response.status_code == 201
            assert response.json()["control_run_id"] == "live-api-run"

            # Si el 201 no garantizara la suscripcion, esto colgaria o llegaria tarde.
            publisher.wait_for_subscriber()
            publisher.publish_events(10)
            publisher.finish()

            app.state.manager.join_active(timeout=60.0)

            state = client.get("/api/runs/live-api-run").json()
            assert state["status"] == "succeeded"
            summary = state["summary"]
            assert summary["source"] == "bus"
            assert summary["media_run_id"] == _MEDIA_RUN_ID
            assert summary["experiment_id"] == "exp-live"
            assert summary["units_processed"] == 10, "se perdieron eventos: la suscripcion llego tarde"
            assert summary["bus_dropped_events"] == 0
            assert summary["degraded"] is False

            alerts = client.get("/api/runs/live-api-run/alerts").json()
            assert len(alerts) >= 1
            assert alerts[0]["media_run_id"] == _MEDIA_RUN_ID
    finally:
        publisher.close()


@pytest.mark.integration
def test_the_201_response_implies_the_bus_source_is_already_subscribed(tmp_path, monkeypatch) -> None:
    """El gate de verdad: la suscripcion ocurre en `start_run`, no en el hilo de la corrida.

    Bloqueamos el ejecutor ANTES de que haga nada. Si la suscripcion viviera ahi,
    `subscribed` seria False al devolver el 201. Es una asercion de ORDEN, no de
    timing: el timing de PUB/SUB no es observable de forma determinista.
    """
    endpoint = f"tcp://127.0.0.1:{_free_port()}"
    release = threading.Event()

    def _blocked_execute(self: RunManager, active: ActiveRun) -> None:
        # No corre el runtime real (nunca llama `run_live_from_config`): solo
        # espera a que el test lo suelte. Asi queda demostrado que la
        # suscripcion (hecha en `start_run`) no depende de que este hilo llegue
        # a correr. Timeout de seguridad para que una asercion fallida del test
        # no cuelgue la suite entera.
        if not release.wait(timeout=10.0):
            raise TimeoutError("el test nunca solto el Event de bloqueo")
        # Replica el orden del `finally` real de `_execute`: soltar el slot
        # ANTES de marcar `finished` (ver comentario ahi mismo).
        active.status = "succeeded"
        with self._lock:
            self._active = None
        active.finished.set()

    monkeypatch.setattr(RunManager, "_execute", _blocked_execute)

    app = create_app(ServiceSettings(runs_dir=tmp_path / "runs"))
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={"mode": "live", "config": _live_payload(endpoint), "experiment_id": "exp-live"},
        )
        assert response.status_code == 201

        current = client.get("/api/runs/current").json()
        assert current["subscribed"] is True
        assert current["status"] == "running"

        # El BusSource ya existe (se construyo en `start_run`, antes del 201).
        # Lo agarramos aca, mientras el hilo ejecutor sigue bloqueado, para
        # cerrarlo despues sin que nadie mas lo este usando.
        with app.state.manager._lock:
            active = app.state.manager._active
        assert active is not None
        source = active.source
        assert source is not None

        release.set()
        app.state.manager.join_active(timeout=15.0)

    # El iterador de la fuente nunca llego a correr (el hilo estuvo bloqueado
    # todo el tiempo en el Event): es seguro cerrar su socket desde este hilo,
    # no hay uso concurrente. Cerrar un socket ZeroMQ mientras otro hilo esta en
    # `recv_multipart` aborta el proceso (SIGABRT); aca no aplica.
    source.close()


@pytest.mark.integration
def test_live_run_reports_dropped_events_when_the_publisher_skips_a_seq(tmp_path) -> None:
    """Un hueco de `seq` degrada la corrida. Nunca se silencia (ADR-003)."""
    endpoint = f"tcp://127.0.0.1:{_free_port()}"
    publisher = _Publisher(endpoint)
    app = create_app(ServiceSettings(runs_dir=tmp_path / "runs"))

    try:
        with TestClient(app) as client:
            client.post("/api/runs", json={"mode": "live", "config": _live_payload(endpoint)})
            publisher.wait_for_subscriber()
            publisher.publish_events(3)
            publisher._seq += 5  # 5 envelopes "perdidos" por HWM del publicador
            publisher.publish_events(2)
            publisher.finish()

            app.state.manager.join_active(timeout=60.0)

            summary = client.get("/api/runs/live-api-run").json()["summary"]
            assert summary["bus_dropped_events"] == 5
            assert summary["degraded"] is True
            assert "bus_dropped_events" in summary["degradation_causes"]
    finally:
        publisher.close()
