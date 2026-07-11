import json
import socket
import threading
import time

import msgpack
import pytest
import zmq

from eovrt_control.sources.bus import BusSource


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _event_payload(unit_id: str) -> bytes:
    return json.dumps(
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
            "prompts": {"prompt_set_id": "p1"},
            "detections": [],
        }
    ).encode("utf-8")


class _Publisher:
    """Publicador de prueba: pinea el wire format del media-plane, sin importarlo."""

    def __init__(self, endpoint: str) -> None:
        self._sock = zmq.Context.instance().socket(zmq.XPUB)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.setsockopt(zmq.XPUB_VERBOSE, 1)
        self._sock.bind(endpoint)

    def wait_for_subscriber(self, expected: int = 2, timeout_ms: int = 3000) -> None:
        """Drena exactamente `expected` notificaciones de suscripcion (SUBSCRIBE).

        Un BusSource con los 2 prefijos por default (media.detection.v1. y
        run.lifecycle.v1.) genera 2 notificaciones en el XPUB. Si se drena
        menos de las que corresponden, la notificacion sobrante queda
        colgada en el socket y una llamada posterior (para un segundo
        BusSource) la consume por error, retornando antes de que la
        suscripcion real del segundo SUB haya llegado al publicador
        (ventana de slow-joiner de PUB/SUB).
        """
        poller = zmq.Poller()
        poller.register(self._sock, zmq.POLLIN)
        deadline = time.monotonic() + timeout_ms / 1000.0
        received = 0
        while received < expected:
            remaining_ms = max(0.0, (deadline - time.monotonic()) * 1000.0)
            ready = dict(poller.poll(timeout=remaining_ms))
            assert ready, (
                f"el SUB no genero las {expected} notificaciones de suscripcion a "
                f"tiempo (llegaron {received})"
            )
            self._sock.recv()
            received += 1

    def send(self, topic: str, seq: int, payload: bytes, key: str = "cam-1") -> None:
        envelope = msgpack.packb(
            {
                "schema_version": "bus.envelope.v1",
                "topic": topic,
                "key": key,
                "seq": seq,
                "ts_publish_ms": 0.0,
                "payload": payload,
            },
            use_bin_type=True,
        )
        self._sock.send_multipart([topic.encode("utf-8"), envelope])

    def finish(self, seq: int, status: str = "succeeded") -> None:
        payload = json.dumps(
            {
                "schema_version": "run.lifecycle.v1",
                "event": "run_finished",
                "media_run_id": "media-run",
                "status": status,
            }
        ).encode("utf-8")
        self.send("run.lifecycle.v1.media-run", seq, payload, key="media-run")

    def close(self) -> None:
        self._sock.close(linger=0)


@pytest.fixture()
def wired():
    endpoint = f"tcp://127.0.0.1:{_free_port()}"
    publisher = _Publisher(endpoint)
    source = BusSource(endpoint=endpoint, control_run_id="control-run", recv_timeout_ms=200)
    publisher.wait_for_subscriber()
    yield publisher, source
    source.close()
    publisher.close()


def test_bus_source_yields_events_until_run_finished(wired) -> None:
    publisher, source = wired
    for seq in range(3):
        publisher.send("media.detection.v1.media-run", seq, _event_payload(f"u{seq}"))
    publisher.finish(seq=3)

    items = list(source)

    assert [event.unit_id for _, event, _, _ in items] == ["u0", "u1", "u2"]
    assert source.dropped_events == 0
    assert source.kind == "bus"


def test_bus_source_counts_seq_gaps_as_dropped_events(wired) -> None:
    publisher, source = wired
    publisher.send("media.detection.v1.media-run", 0, _event_payload("u0"))
    # seq 1 y 2 se "perdieron" (HWM del publicador).
    publisher.send("media.detection.v1.media-run", 3, _event_payload("u3"))
    publisher.finish(seq=4)

    items = list(source)

    assert [event.unit_id for _, event, _, _ in items] == ["u0", "u3"]
    assert source.dropped_events == 2


def test_bus_source_reports_unparseable_payload_as_unit_error(wired) -> None:
    publisher, source = wired
    publisher.send("media.detection.v1.media-run", 0, b"{no es json}")
    publisher.finish(seq=1)

    items = list(source)

    assert len(items) == 1
    (index, event, error, _) = items[0]
    assert event is None
    assert index == 0
    # Una unidad que llego pero no valida ES una unidad fallida.
    assert error.line_number == 0
    assert error.error_type == "JSONDecodeError"


def test_bus_source_reports_bad_envelope_as_source_error(wired) -> None:
    publisher, source = wired
    bad = msgpack.packb({"schema_version": "bus.envelope.v9", "topic": "media.detection.v1.x"})
    publisher._sock.send_multipart([b"media.detection.v1.media-run", bad])
    publisher.finish(seq=0)

    items = list(source)

    (_, event, error, _) = items[0]
    assert event is None
    assert error.error_type == "EnvelopeSchemaMismatch"
    # Error de FUENTE: no cuenta como unidad fallida.
    assert error.line_number is None


def test_bus_source_closes_by_polling_fallback_when_lifecycle_is_lost(wired, monkeypatch) -> None:
    publisher, _unused = wired
    endpoint = publisher._sock.getsockopt_string(zmq.LAST_ENDPOINT)
    source = BusSource(
        endpoint=endpoint,
        control_run_id="control-run",
        recv_timeout_ms=100,
        poll_url="http://localhost:9/api/runs/media-run",
        poll_interval_s=0.05,
        idle_timeout_s=30.0,
    )
    publisher.wait_for_subscriber()
    publisher.send("media.detection.v1.media-run", 0, _event_payload("u0"))
    # El run_finished NUNCA se publica: solo el polling puede cerrar la corrida.
    monkeypatch.setattr(
        "eovrt_control.sources.bus.BusSource._run_finished_by_polling", lambda self: True
    )

    items = list(source)
    source.close()

    assert [event.unit_id for _, event, _, _ in items] == ["u0"]


def test_bus_source_closes_on_idle_timeout_with_a_source_error(wired) -> None:
    publisher, _unused = wired
    endpoint = publisher._sock.getsockopt_string(zmq.LAST_ENDPOINT)
    source = BusSource(
        endpoint=endpoint,
        control_run_id="control-run",
        recv_timeout_ms=50,
        idle_timeout_s=0.15,
    )
    publisher.wait_for_subscriber()

    items = list(source)
    source.close()

    assert len(items) == 1
    (_, event, error, _) = items[0]
    assert event is None
    assert error.error_type == "BusIdleTimeout"
    assert error.line_number is None


# --- code review: hallazgos 1-6 ---


def test_bus_source_reports_envelope_missing_topic_as_source_error(wired) -> None:
    """Hallazgo 1: KeyError al indexar `envelope["topic"]` no debe matar la corrida."""
    publisher, source = wired
    bad = msgpack.packb(
        {"schema_version": "bus.envelope.v1", "seq": 0, "payload": _event_payload("u0")},
        use_bin_type=True,
    )
    publisher._sock.send_multipart([b"media.detection.v1.media-run", bad])
    publisher.finish(seq=1)

    items = list(source)

    assert len(items) == 1
    (_, event, error, _) = items[0]
    assert event is None
    assert error.error_type == "EnvelopeDecodeError"
    # Error de FUENTE: no cuenta como unidad fallida.
    assert error.line_number is None


def test_bus_source_reports_corrupt_lifecycle_payload_as_source_error(wired) -> None:
    """Hallazgo 1: JSON corrupto en el payload de lifecycle no debe matar la corrida."""
    publisher, source = wired
    publisher.send("run.lifecycle.v1.media-run", 0, b"{no es json}", key="media-run")
    publisher.send("media.detection.v1.media-run", 1, _event_payload("u1"))
    publisher.finish(seq=2)

    items = list(source)

    errors = [error for _, _, error, _ in items if error is not None]
    assert any(
        error.error_type == "LifecycleDecodeError" and error.line_number is None
        for error in errors
    )
    # La corrida siguio consumiendo despues del lifecycle ilegible y cerro por
    # la via normal (run_finished), no por la excepcion.
    assert [event.unit_id for _, event, _, _ in items if event is not None] == ["u1"]


def test_check_seq_does_not_regress_pointer_on_duplicate_or_reordered_seq() -> None:
    """Hallazgo 2: un seq duplicado/reordenado no debe correr el puntero hacia atras."""
    endpoint = f"tcp://127.0.0.1:{_free_port()}"
    source = BusSource(endpoint=endpoint, control_run_id="control-run")
    try:
        for seq in [0, 1, 2, 3, 4]:
            source._check_seq("media-run", seq)
        source._check_seq("media-run", 2)  # duplicado
        source._check_seq("media-run", 5)  # legitimo, no deberia leerse como hueco

        assert source.dropped_events == 0
    finally:
        source.close()


def test_bus_source_drains_queued_messages_before_closing_by_polling(wired, monkeypatch) -> None:
    """Hallazgo 3: el cierre por polling no debe descartar mensajes ya encolados."""
    publisher, _unused = wired
    endpoint = publisher._sock.getsockopt_string(zmq.LAST_ENDPOINT)
    source = BusSource(
        endpoint=endpoint,
        control_run_id="control-run",
        recv_timeout_ms=50,
        poll_url="http://localhost:9/api/runs/media-run",
        poll_interval_s=0.05,
        idle_timeout_s=30.0,
    )
    publisher.wait_for_subscriber()

    def _fake_poll(self):
        # Simula mensajes que el publicador siguio encolando en el SUB mientras
        # el GET bloqueante de polling estaba en vuelo.
        publisher.send("media.detection.v1.media-run", 0, _event_payload("u0"))
        publisher.send("media.detection.v1.media-run", 1, _event_payload("u1"))
        time.sleep(0.05)  # le da tiempo a ZMQ de entregarlos al SUB
        return True

    monkeypatch.setattr(
        "eovrt_control.sources.bus.BusSource._run_finished_by_polling", _fake_poll
    )

    items = list(source)
    source.close()

    # Ninguno de los dos mensajes encolados durante el polling desaparece.
    assert [event.unit_id for _, event, _, _ in items if event is not None] == ["u0", "u1"]


def test_check_seq_counts_first_seq_gap_as_dropped() -> None:
    """Hallazgo 4: el primer seq de un run_key tambien se valida contra 0."""
    endpoint = f"tcp://127.0.0.1:{_free_port()}"
    source = BusSource(endpoint=endpoint, control_run_id="control-run")
    try:
        source._check_seq("media-run", 3)

        assert source.dropped_events == 3
    finally:
        source.close()


# --- code review de seguimiento: hallazgos finales 1-4 ---


def test_bus_source_reports_single_frame_message_as_source_error(wired) -> None:
    """Hallazgo 1: un mensaje de un solo frame no debe matar la corrida con ValueError.

    Los bytes del unico frame arrancan con el prefijo suscripto, asi que pasan el
    filtro SUBSCRIBE de ZMQ, pero el desempaquetado de 2 frames (topic + envelope)
    no aplica.
    """
    publisher, source = wired
    publisher._sock.send_multipart([b"media.detection.v1.media-run"])
    publisher.finish(seq=0)

    items = list(source)

    assert len(items) == 1
    (_, event, error, _) = items[0]
    assert event is None
    assert error.error_type == "EnvelopeFrameError"
    # Error de FUENTE: no cuenta como unidad fallida.
    assert error.line_number is None


def test_bus_source_reports_non_string_detection_payload_as_unit_error(wired) -> None:
    """Hallazgo 2: TypeError en json.loads (payload no str/bytes) es una unidad
    fallida, no una excepcion que mate la corrida."""
    publisher, source = wired
    envelope = msgpack.packb(
        {
            "schema_version": "bus.envelope.v1",
            "topic": "media.detection.v1.media-run",
            "key": "cam-1",
            "seq": 0,
            "ts_publish_ms": 0.0,
            "payload": 12345,
        },
        use_bin_type=True,
    )
    publisher._sock.send_multipart([b"media.detection.v1.media-run", envelope])
    publisher.finish(seq=1)

    items = list(source)

    assert len(items) == 1
    (index, event, error, _) = items[0]
    assert event is None
    assert index == 0
    # Unidad que llego pero no se pudo parsear: ES una unidad fallida.
    assert error.line_number == 0
    assert error.error_type == "TypeError"


def test_bus_source_drain_is_bounded_and_terminates(wired, monkeypatch) -> None:
    """Hallazgo 3: _drain() debe tener cota aunque el publicador no pare de emitir.

    Un hilo publica en loop, tan rapido como puede (como en el repro original,
    ~2000 msg/s). Se frena un poco el lado del consumo (sleep chico en
    `_decode`) para garantizar que la produccion le gana con margen a la
    decodificacion sin depender de la suerte del scheduling del GIL entre
    threads Python: en este proceso, produccion y consumo sin ese margen
    quedan practicamente empatados y el backlog nunca llega a crecer, lo que
    haria que el test no ejercite la cota aunque el bug siga presente. El
    `poll()` real tambien se fuerza a no reportar nunca "listo" para forzar el
    camino de polling/drenaje sin esperar una ventana de carrera natural.

    Corre `list(source)` en un thread aparte con `join(timeout=...)`: si la
    cota no existe, `_drain()` no vuelve nunca (el publicador tampoco para), y
    no queremos colgar la suite entera esperandolo.
    """
    publisher, _unused = wired
    endpoint = publisher._sock.getsockopt_string(zmq.LAST_ENDPOINT)
    source = BusSource(
        endpoint=endpoint,
        control_run_id="control-run",
        recv_timeout_ms=50,
        poll_url="http://localhost:9/api/runs/media-run",
        poll_interval_s=0.01,
        idle_timeout_s=30.0,
    )
    publisher.wait_for_subscriber()

    stop = threading.Event()

    def _flood() -> None:
        seq = 0
        while not stop.is_set():
            publisher.send("media.detection.v1.media-run", seq, _event_payload(f"u{seq}"))
            seq += 1

    def _fake_poll(*args, **kwargs):
        time.sleep(0.01)
        return {}

    real_decode = BusSource._decode

    def _slow_decode(self, wire_topic, raw, ts_receive_monotonic):
        time.sleep(0.001)
        return real_decode(self, wire_topic, raw, ts_receive_monotonic)

    monkeypatch.setattr(BusSource, "_decode", _slow_decode)
    monkeypatch.setattr(source._poller, "poll", _fake_poll)
    monkeypatch.setattr(
        "eovrt_control.sources.bus.BusSource._run_finished_by_polling", lambda self: True
    )

    thread = threading.Thread(target=_flood, daemon=True)
    thread.start()

    result: list = []

    def _run() -> None:
        result.append(list(source))

    worker = threading.Thread(target=_run, daemon=True)
    start = time.monotonic()
    worker.start()
    worker.join(timeout=5.0)
    elapsed = time.monotonic() - start
    finished = not worker.is_alive()

    # Solo se toca el socket si el worker realmente termino: cerrarlo
    # mientras el thread de list(source) todavia esta adentro de un
    # recv_multipart() en vuelo corrompe el socket (no es thread-safe) y
    # puede tirar abajo el proceso entero, no solo este test.
    stop.set()
    thread.join(timeout=2.0)
    if finished:
        source.close()
        # Le da un respiro a los threads de I/O internos de libzmq (el
        # contexto es un singleton compartido con el resto de la suite) para
        # que terminen de asentarse despues de un socket de alto volumen,
        # antes de que el proximo test cree sockets nuevos sobre el mismo
        # contexto.
        time.sleep(0.1)

    assert finished, f"list(source) no termino en {elapsed:.1f}s: el drenaje sin cota nunca corta"
    # El publicador nunca deja de emitir; la corrida tiene que cerrar igual.
    assert elapsed < 5.0
    assert len(result[0]) > 0


def test_bus_source_idle_timeout_drain_finds_run_finished_and_closes_clean(
    wired, monkeypatch
) -> None:
    """Hallazgo 4: el drenaje por idle-timeout tambien puede encontrar el
    run_finished a mitad de camino y cerrar limpio, sin emitir el ErrorEvent
    de BusIdleTimeout.

    Se fuerza al poller a reportar "nada listo" (simulando la ventana de
    carrera real: mensajes que llegan justo cuando el poll de idle-timeout ya
    devolvio vacio) mientras los mensajes ya estan encolados en el socket, asi
    que solo el `_drain()` no bloqueante del cierre por idle los puede ver.
    """
    publisher, source = wired
    publisher.send("media.detection.v1.media-run", 0, _event_payload("u0"))
    publisher.finish(seq=1)
    time.sleep(0.05)  # le da tiempo a ZMQ de entregar los mensajes al socket SUB

    monkeypatch.setattr(source._poller, "poll", lambda *args, **kwargs: {})
    source._idle_timeout_s = 0.05

    items = list(source)

    assert [event.unit_id for _, event, _, _ in items if event is not None] == ["u0"]
    errors = [error for _, _, error, _ in items if error is not None]
    assert not any(error.error_type == "BusIdleTimeout" for error in errors)


def test_bus_source_request_stop_emits_a_source_error_and_stops(wired) -> None:
    """Revision final, hallazgo 2: `request_stop()` debe dejar rastro (un ErrorEvent
    de FUENTE), no un cierre mudo que despues se lee como `succeeded` sin marca."""
    publisher, source = wired

    def _stop_soon() -> None:
        time.sleep(0.05)
        source.request_stop()

    threading.Thread(target=_stop_soon, daemon=True).start()

    items = list(source)

    assert len(items) == 1
    (_, event, error, _) = items[0]
    assert event is None
    assert error.error_type == "BusStopRequested"
    # Error de FUENTE: no cuenta como unidad fallida.
    assert error.line_number is None


def test_bus_source_stamps_monotonic_ts_receive(wired) -> None:
    """Task 1: cada item trae el instante monotonico de recepcion en ms."""
    publisher, source = wired
    publisher.send("media.detection.v1.media-run", 0, _event_payload("u-1"))
    publisher.finish(seq=1)

    items = list(source)
    detection_items = [it for it in items if it[1] is not None]

    assert len(detection_items) == 1
    index, event, error, ts_receive_ms = detection_items[0]
    assert error is None
    assert event.unit_id == "u-1"
    assert isinstance(ts_receive_ms, float) and ts_receive_ms > 0


def test_bus_source_reports_topic_mismatch_as_source_error(wired) -> None:
    """Hallazgo 5: el topic interno del payload debe coincidir con el frame de wire."""
    publisher, source = wired
    envelope = msgpack.packb(
        {
            "schema_version": "bus.envelope.v1",
            "topic": "media.detection.v1.other-run",
            "key": "cam-1",
            "seq": 0,
            "ts_publish_ms": 0.0,
            "payload": _event_payload("u0"),
        },
        use_bin_type=True,
    )
    # El frame de wire (el que paso el filtro SUBSCRIBE) no coincide con el
    # topic que el payload dice tener.
    publisher._sock.send_multipart([b"media.detection.v1.media-run", envelope])
    publisher.finish(seq=1)

    items = list(source)

    assert len(items) == 1
    (_, event, error, _) = items[0]
    assert event is None
    assert error.error_type == "EnvelopeTopicMismatch"
    assert error.line_number is None
