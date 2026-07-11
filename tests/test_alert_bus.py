"""Tests del publisher de alertas al bus (Task 7, espejo de BusPublisher).

Gate de paridad de payload: lo que se publica en `control.alert.v1.<id>` debe
ser byte-identico a la linea que el `JsonlSink` de verdad escribe en
alerts.jsonl (no solo un dict equivalente: un campo opcional en None
sobrevive en la linea JSONL como `null`, y `exclude_none` lo omitiria del
lado del bus, rompiendo la igualdad de bytes sin romper la igualdad de
dicts). Por eso el test compara bytes contra un `JsonlSink` real.
"""

from __future__ import annotations

import json

import msgpack
import pytest
import zmq

from eovrt_control.contracts.alerts import AlertEvent
from eovrt_control.contracts.pattern import EvidenceRef, PatternEvidence
from eovrt_control.sinks.jsonl import JsonlSink
from eovrt_control.transport.alert_bus import AlertBusPublisher


class _FakeSock:
    """Doble de socket XPUB: solo registra lo que se enviaria."""

    def __init__(self) -> None:
        self.sent: list[tuple[bytes, bytes]] = []

    def setsockopt(self, *args: object, **kwargs: object) -> None:
        return None

    def bind(self, *args: object, **kwargs: object) -> None:
        return None

    def send_multipart(self, frames: list[bytes], flags: int = 0) -> None:
        self.sent.append(tuple(frames))

    def close(self, *args: object, **kwargs: object) -> None:
        return None


def _make_publisher(sock: _FakeSock) -> AlertBusPublisher:
    """Construye un AlertBusPublisher sin pasar por __init__ (sin socket real)."""
    pub = AlertBusPublisher.__new__(AlertBusPublisher)
    pub._sock = sock
    pub._seq = 0
    pub._closed = False
    pub.send_failures = 0
    return pub


@pytest.fixture
def _example_alert() -> AlertEvent:
    """Alerta con todos los campos, incluidos los opcionales; `experiment_id`
    queda en None a proposito para ejercitar el caso que `exclude_none`
    rompe (la linea JSONL lo escribe como `null`, no lo omite)."""
    return AlertEvent(
        control_run_id="ctrl-run-1",
        media_run_id="media-run-1",
        unit_id="unit-1",
        source_id="cam-1",
        alert_id="alert-1",
        pattern_id="CR-01",
        condition_id="cond-1",
        subject_key="scene",
        severity="high",
        state="open",
        evidence=PatternEvidence(
            pattern_id="CR-01",
            condition_id="cond-1",
            subject_key="scene",
            subject=EvidenceRef(
                detection_id="det-1", label="person", confidence=0.9, bbox_xyxy=[0.0, 0.0, 1.0, 1.0]
            ),
            missing_class="helmet",
            supporting=[],
            score=0.8,
            rationale="sin casco",
            subjects_in_evidence=1,
        ),
        frame_index=42,
        timestamp_ms=123.0,
        subjects_in_evidence_max=1,
        alert_registered_ms=10.0,
        first_evidence_ms=5.0,
        first_evidence_unit_id="unit-0",
        first_evidence_frame_index=41,
        experiment_id=None,
    )


def test_alert_payload_is_byte_identical_to_jsonl_line(tmp_path, _example_alert) -> None:
    sink_path = tmp_path / "alerts.jsonl"
    with JsonlSink(sink_path) as sink:
        sink.write(_example_alert)
    jsonl_line = sink_path.read_text(encoding="utf-8").splitlines()[0].encode("utf-8")

    # Misma serializacion que usa runtime/core.py antes de publicar (identica
    # a la del JsonlSink real, verificado arriba linea por linea).
    payload = json.dumps(_example_alert.model_dump(mode="json"), ensure_ascii=True).encode("utf-8")
    assert payload == jsonl_line

    fake = _FakeSock()
    pub = _make_publisher(fake)
    seq = pub.publish("control.alert.v1.run-1", _example_alert.source_id, payload)

    assert seq == 0
    topic_frame, envelope_frame = fake.sent[0]
    assert topic_frame == b"control.alert.v1.run-1"
    env = msgpack.unpackb(envelope_frame, raw=False)
    assert env["schema_version"] == "bus.envelope.v1"
    assert env["seq"] == 0
    assert env["payload"] == payload == jsonl_line


def test_seq_is_consumed_even_when_send_is_dropped(_example_alert) -> None:
    fake = _FakeSock()

    def _raise(frames: list[bytes], flags: int = 0) -> None:
        raise zmq.Again()

    fake.send_multipart = _raise  # type: ignore[method-assign]
    pub = _make_publisher(fake)
    s0 = pub.publish("t", "k", b"x")
    s1 = pub.publish("t", "k", b"y")
    assert (s0, s1) == (0, 1)
    assert pub.send_failures == 2
