"""Gate del tramo plataforma: el motor es agnostico de la fuente (spec 40 SS3.4)."""

import json
import socket
import threading
import time
from pathlib import Path

import msgpack
import pytest
import yaml
import zmq

from eovrt_control.runtime.live import run_live
from eovrt_control.runtime.replay import run_replay
from eovrt_control.sources.bus import BusSource

_REPO_ROOT = Path(__file__).resolve().parents[1]
# 12 eventos, un unico run_id ("simulated-media-cr01-cr02-temporal"),
# source_type "video" (NO "video_frame") => pattern_evaluation "computed".
_FIXTURE = _REPO_ROOT / "fixtures/simulated_media/cr01_cr02_temporal/detections.jsonl"
# El pattern set con persistencia/histeresis: confirm_after_frames 3,
# resolve_after_frames 2. Ejercita la maquina de estados a lo largo de frames,
# que es lo que el gate debe probar. `cr01_cr02_v1.yaml` confirma en 1 frame y
# no distinguiria un motor sin memoria de episodio.
_PATTERNS = _REPO_ROOT / "configs/patterns/cr01_cr02_temporal_eval.yaml"

# Campos que dependen del control_run_id o del instante real de ejecucion, no
# del contenido de la fuente. `first_evidence_ms` es el `ts_receive_ms`
# monotonico (Task 2): varia entre corridas (replay vs bus) aunque el
# contenido (unit_id, frame_index) sea identico. `alert_registered_ms` (Task 3)
# es igual de volatil: es `time.monotonic()` al momento de escribir la alerta,
# propio del proceso que corre el motor.
_VOLATILE = ("control_run_id", "alert_id", "first_evidence_ms", "alert_registered_ms")

# Notificaciones de suscripcion que emite un BusSource con topics=None: un
# SUBSCRIBE por prefijo (media.detection.v1. y run.lifecycle.v1.).
_EXPECTED_SUBSCRIPTIONS = 2


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _normalize(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        for field in _VOLATILE:
            record.pop(field, None)
        records.append(record)
    return records


def _write_config(tmp_path: Path, *, name: str, input_section: dict) -> Path:
    config_path = tmp_path / f"{name}.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "run": {"id": name, "scenario": "DBE", "name": name},
                "input": input_section,
                "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01", "CR-02"]},
                "outputs": {"base_dir": str(tmp_path / "runs")},
            }
        ),
        encoding="utf-8",
    )
    return config_path


class _FixturePublisher:
    """Reproduce el fixture por el bus con el wire format de `bus.envelope.v1`.

    Es un publicador propio a proposito: los dos repos no se importan, asi que
    este test tambien pinea el contrato de wire del lado del consumidor.
    """

    def __init__(self, endpoint: str, media_run_id: str) -> None:
        self._media_run_id = media_run_id
        self._sock = zmq.Context.instance().socket(zmq.XPUB)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.setsockopt(zmq.XPUB_VERBOSE, 1)
        self._sock.setsockopt(zmq.SNDHWM, 10_000)
        self._sock.bind(endpoint)
        # Un solo contador de seq compartido entre detecciones y lifecycle,
        # tal como lo hace el publicador real del media-plane. Si el seq del
        # lifecycle no continua la secuencia de detecciones, BusSource lo lee
        # como un hueco y cuenta dropped_events de mas.
        self._seq = 0

    def wait_for_subscriber(
        self, expected: int = _EXPECTED_SUBSCRIPTIONS, timeout_ms: int = 5000
    ) -> None:
        """Drena exactamente `expected` notificaciones de suscripcion (SUBSCRIBE).

        Un BusSource con topics=None (el default) genera dos notificaciones de
        suscripcion en el XPUB, una por prefijo (media.detection.v1. y
        run.lifecycle.v1.). Si se drena solo una, la ventana de slow-joiner de
        PUB/SUB queda abierta: el publicador puede empezar a enviar antes de
        que la segunda suscripcion real haya llegado, y el test se vuelve
        flaky o pierde eventos.
        """
        poller = zmq.Poller()
        poller.register(self._sock, zmq.POLLIN)
        deadline = time.monotonic() + timeout_ms / 1000.0
        received = 0
        while received < expected:
            remaining_ms = max(0.0, (deadline - time.monotonic()) * 1000.0)
            ready = dict(poller.poll(timeout=remaining_ms))
            assert ready, (
                f"el BusSource no genero las {expected} notificaciones de suscripcion "
                f"a tiempo (llegaron {received})"
            )
            self._sock.recv()
            received += 1

    def _send(self, topic: str, key: str, payload: bytes) -> None:
        envelope = msgpack.packb(
            {
                "schema_version": "bus.envelope.v1",
                "topic": topic,
                "key": key,
                "seq": self._seq,
                "ts_publish_ms": 0.0,
                "payload": payload,
            },
            use_bin_type=True,
        )
        self._seq += 1
        self._sock.send_multipart([topic.encode("utf-8"), envelope])

    def replay_file(self, path: Path) -> None:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            # Byte-compatible: el payload es la linea tal cual (spec 40 SS3.1).
            self._send(
                f"media.detection.v1.{self._media_run_id}",
                event["source"]["source_id"],
                line.encode("utf-8"),
            )

    def finish(self) -> None:
        payload = json.dumps(
            {
                "schema_version": "run.lifecycle.v1",
                "event": "run_finished",
                "media_run_id": self._media_run_id,
                "status": "succeeded",
            }
        ).encode("utf-8")
        self._send(f"run.lifecycle.v1.{self._media_run_id}", self._media_run_id, payload)

    def close(self) -> None:
        self._sock.close(linger=0)


@pytest.mark.integration
def test_replay_and_stream_produce_identical_pattern_events_and_alerts(tmp_path) -> None:
    assert _FIXTURE.exists(), f"falta el fixture temporal: {_FIXTURE}"
    media_run_id = json.loads(_FIXTURE.read_text(encoding="utf-8").splitlines()[0])["run_id"]

    # (a) Replay desde archivo.
    replay_summary = run_replay(
        _write_config(
            tmp_path, name="replay", input_section={"type": "media_jsonl", "path": str(_FIXTURE)}
        )
    )

    # (b) Stream por el bus. Orden obligatorio: publicador bind -> BusSource
    # suscripto -> recien entonces se publican los eventos.
    endpoint = f"tcp://127.0.0.1:{_free_port()}"
    publisher = _FixturePublisher(endpoint, media_run_id)
    live_config = _write_config(
        tmp_path,
        name="live",
        input_section={
            "type": "bus",
            "bus": {"endpoint": endpoint, "recv_timeout_ms": 200, "idle_timeout_s": 30},
        },
    )
    source = BusSource(endpoint=endpoint, control_run_id="live", recv_timeout_ms=200,
                       idle_timeout_s=30.0)
    publisher.wait_for_subscriber()

    live_summary: dict = {}

    def _consume() -> None:
        live_summary["value"] = run_live(live_config, source=source)

    consumer = threading.Thread(target=_consume, name="live-consumer")
    consumer.start()
    publisher.replay_file(_FIXTURE)
    publisher.finish()
    consumer.join(timeout=60.0)
    publisher.close()

    assert not consumer.is_alive(), "run_live no cerro con run_finished"
    stream_summary = live_summary["value"]

    # Sin perdidas, no hay corrida degradada por el bus.
    assert stream_summary.bus_dropped_events == 0
    assert "bus_dropped_events" not in stream_summary.degradation_causes
    assert stream_summary.source == "bus"
    assert replay_summary.source == "jsonl"

    # Mismos contadores.
    assert stream_summary.units_processed == replay_summary.units_processed
    assert stream_summary.alerts_count == replay_summary.alerts_count
    assert stream_summary.pattern_events_count == replay_summary.pattern_events_count
    assert stream_summary.alerts_count > 0, "el fixture debe producir al menos una alerta"

    # Mismos artefactos, modulo control_run_id/alert_id.
    runs = tmp_path / "runs"
    for artifact in ("pattern_events.jsonl", "alerts.jsonl"):
        assert _normalize(runs / "live" / artifact) == _normalize(runs / "replay" / artifact), (
            f"{artifact} difiere entre replay y stream"
        )
