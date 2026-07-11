"""Integracion end-to-end del publisher de alertas (Task 7, hallazgo Importante de revision).

`tests/test_alert_bus.py` ejercita `AlertBusPublisher` aislado (socket fake, sin
`__init__` real). Nada corria el wiring real de `execute_over_source` via
`RunManager` (el camino del servicio) con `config.alert_bus.enabled = True`. Este
test cierra ese hueco: levanta una corrida real de replay por `RunManager`, con un
`SUB` real conectado y suscripto ANTES de arrancar la corrida (evita el
slow-joiner de PUB/SUB), y verifica:

  (a) llega al menos un envelope de alerta por `control.alert.v1.<control_run_id>`,
  (b) su `payload` es byte-identico a la linea que el `JsonlSink` escribio en
      `alerts.jsonl` (paridad de bytes extremo a extremo, no solo dict-equality),
  (c) llega el sentinela `run_finished` por `run.lifecycle.v1.<control_run_id>`
      con `status == "succeeded"`.

Infraestructura tomada de `test_run_manager.py` (RunManager + payload de una sola
unidad => una alerta CR-01) y de `test_bus_parity.py` (puerto libre, SUB real).
"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

import msgpack
import pytest
import zmq

from eovrt_control.service.run_manager import RunManager
from eovrt_control.service.run_request import ControlRunRequest
from eovrt_control.service.settings import ServiceSettings
from eovrt_control.transport.alert_bus import ALERT_TOPIC_PREFIX, LIFECYCLE_TOPIC_PREFIX

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATTERNS = _REPO_ROOT / "configs/patterns/cr01_cr02_v1.yaml"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _event(unit_id: str) -> dict:
    return {
        "run_id": "media-run",
        "unit_id": unit_id,
        "source": {
            "source_id": "cam-1",
            "source_type": "video",
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


def _detections(tmp_path: Path) -> Path:
    path = tmp_path / "detections.jsonl"
    path.write_text(json.dumps(_event("u1")) + "\n", encoding="utf-8")
    return path


@pytest.mark.integration
def test_execute_over_source_publishes_alerts_and_lifecycle_on_the_real_bus(tmp_path) -> None:
    endpoint = f"tcp://127.0.0.1:{_free_port()}"
    run_id = "alert-bus-it"
    alert_topic = f"{ALERT_TOPIC_PREFIX}{run_id}"
    lifecycle_topic = f"{LIFECYCLE_TOPIC_PREFIX}{run_id}"

    # SUB conectado y suscripto a TODO antes de arrancar la corrida: evita el
    # slow-joiner de PUB/SUB. Una unica suscripcion "" alcanza para los dos
    # topicos (alerta + lifecycle), asi el `wait_for_subscriber(expected=1)`
    # por default del publisher basta para sincronizar de forma deterministica.
    sub = zmq.Context.instance().socket(zmq.SUB)
    sub.setsockopt(zmq.LINGER, 0)
    sub.connect(endpoint)
    sub.setsockopt(zmq.SUBSCRIBE, b"")

    manager = RunManager(ServiceSettings(runs_dir=tmp_path / "runs"))
    received: list[tuple[str, dict]] = []
    try:
        payload = {
            "run": {"id": run_id, "scenario": "DBE", "name": "alert-bus-it"},
            "input": {"type": "media_jsonl", "path": str(_detections(tmp_path))},
            "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
            "alert_bus": {
                "enabled": True,
                "endpoint": endpoint,
                # Generoso: el SUB ya esta conectado/suscripto antes de este punto;
                # esto solo cubre el round-trip real de la notificacion XPUB.
                "wait_for_subscriber_ms": 5000,
            },
        }
        manager.start_run(ControlRunRequest(mode="replay", config=payload))
        manager.join_active(timeout=30.0)

        state = manager.get(run_id)
        assert state["status"] == "succeeded"
        assert state["summary"]["alerts_count"] == 1

        # Drena el bus con un timeout total: la corrida ya termino (join_active
        # ya espero el `finally` que publica run_finished y cierra el socket),
        # asi que todo lo publicado ya esta en el buffer de recepcion del SUB.
        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            remaining_ms = max(0.0, (deadline - time.monotonic()) * 1000.0)
            if not dict(poller.poll(timeout=remaining_ms)):
                break
            topic_frame, envelope_frame = sub.recv_multipart()
            envelope = msgpack.unpackb(envelope_frame, raw=False)
            received.append((topic_frame.decode("utf-8"), envelope))
            has_alert = any(t == alert_topic for t, _ in received)
            has_lifecycle = any(t == lifecycle_topic for t, _ in received)
            if has_alert and has_lifecycle:
                break
    finally:
        manager.shutdown()
        manager.join_active(timeout=15.0)
        sub.close(linger=0)

    alert_envelopes = [env for topic, env in received if topic == alert_topic]
    lifecycle_envelopes = [env for topic, env in received if topic == lifecycle_topic]

    assert alert_envelopes, "no llego ningun envelope de alerta por el bus"
    assert lifecycle_envelopes, "no llego el sentinela run_finished por el bus"
    for envelope in alert_envelopes + lifecycle_envelopes:
        assert envelope["schema_version"] == "bus.envelope.v1"

    # (b) paridad de bytes extremo a extremo contra la linea real de alerts.jsonl.
    alerts_path = tmp_path / "runs" / run_id / "alerts.jsonl"
    jsonl_lines = [
        line.encode("utf-8")
        for line in alerts_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert [env["payload"] for env in alert_envelopes] == jsonl_lines

    # (c) sentinela run_finished con el status esperado.
    lifecycle_payload = json.loads(lifecycle_envelopes[0]["payload"])
    assert lifecycle_payload["schema_version"] == "run.lifecycle.v1"
    assert lifecycle_payload["event"] == "run_finished"
    assert lifecycle_payload["control_run_id"] == run_id
    assert lifecycle_payload["status"] == "succeeded"
