"""Consumidor del bus media->control: ZeroMQ SUB + envelope `bus.envelope.v1`.

Obligaciones (ADR-003, spec 41 SS3, spec 40 SS3.2):
- Suscripcion ANTES del disparo del run: construir este objeto ES suscribirse.
- Huecos en `seq` -> `dropped_events` (la corrida se marca degradada aguas arriba).
- Cierre por `run.lifecycle.v1/run_finished`, con fallback por polling del estado
  del run en el media-plane.
- Deserializacion por la MISMA ruta de validacion que la linea JSONL: paridad por
  construccion con `JsonlSource` (spec 40 SS3.4).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Generator, Iterator

import msgpack
import zmq
from pydantic import ValidationError

from eovrt_control.contracts.errors import ErrorEvent
from eovrt_control.contracts.media import DetectionEvent
from eovrt_control.sources.base import MediaEventSource, SourceItem

logger = logging.getLogger(__name__)

ENVELOPE_SCHEMA_VERSION = "bus.envelope.v1"
DETECTION_TOPIC_PREFIX = "media.detection.v1."
LIFECYCLE_TOPIC_PREFIX = "run.lifecycle.v1."
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "stopped"})
# Cota del drenaje no bloqueante (hallazgo 3): sin esto, un publicador que no
# para de emitir hace que _drain() nunca retorne y el cierre por polling/idle
# deja de garantizar que la corrida termine.
_MAX_DRAIN_ITEMS = 5000


class BusSource(MediaEventSource):
    kind = "bus"

    def __init__(
        self,
        *,
        endpoint: str,
        control_run_id: str,
        topics: list[str] | None = None,
        hwm: int = 1000,
        recv_timeout_ms: int = 1000,
        idle_timeout_s: float = 300.0,
        poll_url: str | None = None,
        poll_interval_s: float = 5.0,
    ) -> None:
        self.endpoint = endpoint
        self._control_run_id = control_run_id
        self._recv_timeout_ms = recv_timeout_ms
        self._idle_timeout_s = idle_timeout_s
        self._poll_url = poll_url
        self._poll_interval_s = poll_interval_s
        self._expected_seq: dict[str, int] = {}
        self._dropped = 0
        self._closed = False
        self._stop = threading.Event()

        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.SUB)
        try:
            self._sock.setsockopt(zmq.RCVHWM, hwm)
            self._sock.setsockopt(zmq.LINGER, 0)
            if topics is None:
                topics = [DETECTION_TOPIC_PREFIX, LIFECYCLE_TOPIC_PREFIX]
            for topic in topics:
                self._sock.setsockopt_string(zmq.SUBSCRIBE, topic)
            self._sock.connect(endpoint)
        except Exception:
            # Si algo de esto falla a mitad de camino (endpoint malformado, etc.)
            # el socket queda creado pero sin dueno: se cierra aca para no filtrarlo.
            self._sock.close(linger=0)
            raise
        self._poller = zmq.Poller()
        self._poller.register(self._sock, zmq.POLLIN)

    @property
    def dropped_events(self) -> int:
        return self._dropped

    def close(self) -> None:
        if not self._closed:
            self._poller.unregister(self._sock)
            self._sock.close(linger=0)
            self._closed = True

    def request_stop(self) -> None:
        """Parada cooperativa: el hilo que itera la ve entre polls y sale solo.

        Nunca toca el socket: cerrarlo desde otro hilo mientras `recv_multipart`
        esta en vuelo aborta el proceso (asercion de libzmq en session_base.cpp).
        """
        self._stop.set()

    def __iter__(self) -> Iterator[SourceItem]:
        last_message = time.monotonic()
        last_poll = time.monotonic()
        while True:
            if self._stop.is_set():
                logger.info("bus: parada solicitada; se cierra la corrida")
                # Revision final, hallazgo 2: un `return` limpio no deja rastro de
                # que la corrida fue interrumpida (a diferencia del cierre por
                # idle-timeout, que si emite su propio ErrorEvent). Sin esto,
                # `summary.json` queda indistinguible de un cierre normal y, al
                # reiniciar el servicio, `get()` la reporta `succeeded` sin marca.
                yield self._source_error(
                    "BusStopRequested",
                    "Parada solicitada (request_stop); cierre de la corrida",
                )
                return
            if dict(self._poller.poll(timeout=self._recv_timeout_ms)):
                frames = self._sock.recv_multipart()
                last_message = time.monotonic()
                item, finished = self._decode_frames(frames, last_message)
                if item is not None:
                    yield item
                if finished:
                    return
                continue

            now = time.monotonic()
            if self._poll_url and now - last_poll >= self._poll_interval_s:
                last_poll = now
                if self._run_finished_by_polling():
                    # La llamada de polling es bloqueante (HTTP); mientras estuvo
                    # en vuelo, ZMQ pudo haber seguido encolando mensajes en el
                    # socket SUB en background. Si no los drenamos aca, se
                    # pierden sin dejar rastro (violaria la unica senal de
                    # perdida del sistema, el hueco de seq).
                    finished_while_draining = yield from self._drain()
                    if finished_while_draining:
                        return
                    logger.warning(
                        "bus: cierre por fallback de polling (%s); el run_finished no llego",
                        self._poll_url,
                    )
                    return
            if now - last_message >= self._idle_timeout_s:
                # Mismo razonamiento que en el cierre por polling: drenar antes
                # de declarar la corrida terminada.
                finished_while_draining = yield from self._drain()
                if finished_while_draining:
                    return
                yield self._source_error(
                    "BusIdleTimeout",
                    f"Sin eventos del bus por {self._idle_timeout_s}s; cierre por timeout",
                )
                return

    def _drain(self, *, max_drain_s: float = 2.0) -> Generator[SourceItem, None, bool]:
        """Vacia sin bloquear lo que haya quedado encolado en el socket.

        Acotado por tiempo (`max_drain_s`, default 2.0s) y por cantidad de
        items (`_MAX_DRAIN_ITEMS`): sin esta cota, un publicador que sigue
        emitiendo mas rapido de lo que se puede drenar hace que este metodo
        nunca retorne, lo que anula la garantia de cierre del fallback de
        polling/idle-timeout (hallazgo 3). Si se alcanza la cota, los
        mensajes que hayan quedado sin leer en el socket se pierden: no se
        pueden contar en `dropped_events` porque no se llegan a decodificar
        (no se conoce su `seq`), pero se loguea un warning explicito para que
        la perdida no pase en silencio.

        Devuelve (via StopIteration.value, por eso el `yield from` en el
        llamador) si entre lo drenado aparecio un run_finished.
        """
        deadline = time.monotonic() + max_drain_s
        drained = 0
        while True:
            if drained >= _MAX_DRAIN_ITEMS or time.monotonic() >= deadline:
                logger.warning(
                    "bus: drenaje cortado por cota (%d items drenados, limite %.1fs); "
                    "puede haber mensajes sin leer en el socket que se pierden sin "
                    "contarse en dropped_events",
                    drained,
                    max_drain_s,
                )
                return False
            try:
                frames = self._sock.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                return False
            ts_receive_monotonic = time.monotonic()
            drained += 1
            item, finished = self._decode_frames(frames, ts_receive_monotonic)
            if item is not None:
                yield item
            if finished:
                return True

    # --- interno ---

    def _decode_frames(
        self, frames: list[bytes], ts_receive_monotonic: float
    ) -> tuple[SourceItem | None, bool]:
        """Valida la cantidad de frames antes de desempaquetar (hallazgo 1).

        Un mensaje multipart que no tiene exactamente 2 frames (topic +
        envelope) no debe hacer explotar el desempaquetado con ValueError: se
        reporta como error de FUENTE y la corrida sigue consumiendo.
        """
        if len(frames) != 2:
            return (
                self._source_error(
                    "EnvelopeFrameError",
                    f"se esperaban 2 frames (topic + envelope), llegaron {len(frames)}",
                ),
                False,
            )
        wire_topic, raw = frames
        return self._decode(wire_topic, raw, ts_receive_monotonic)

    def _source_error(self, error_type: str, message: str) -> SourceItem:
        # `line_number=None` => error de FUENTE: no cuenta como unidad fallida.
        return (
            -1,
            None,
            ErrorEvent(
                control_run_id=self._control_run_id,
                message=message,
                error_type=error_type,
            ),
            None,
        )

    def _decode(
        self, wire_topic: bytes, raw: bytes, ts_receive_monotonic: float
    ) -> tuple[SourceItem | None, bool]:
        """Devuelve (item a emitir o None, se termino la corrida).

        `wire_topic` es el frame de wire, el que realmente paso el filtro
        `SUBSCRIBE` de ZMQ; nunca se confia en el `topic` reportado dentro del
        envelope sin verificarlo contra este frame (hallazgo 5).
        `ts_receive_monotonic` es el instante monotonico (segundos) en que se
        recibio el mensaje crudo; se convierte a ms para el item de deteccion.
        """
        try:
            envelope = msgpack.unpackb(raw, raw=False)
        except Exception as exc:  # noqa: BLE001 — msgpack levanta varios tipos
            return self._source_error("EnvelopeDecodeError", str(exc)), False
        if not isinstance(envelope, dict):
            return self._source_error("EnvelopeDecodeError", "el envelope no es un mapa"), False
        if envelope.get("schema_version") != ENVELOPE_SCHEMA_VERSION:
            return (
                self._source_error(
                    "EnvelopeSchemaMismatch",
                    f"schema_version inesperado: {envelope.get('schema_version')!r}",
                ),
                False,
            )

        # Cualquier envelope malformado (clave faltante, tipo inesperado, seq no
        # entero) es un error de FUENTE, no una excepcion que mate la corrida:
        # el publicador dropea en silencio, el consumidor no puede darse el
        # lujo de morir por un mensaje individual corrupto.
        try:
            topic = envelope["topic"]
            seq = int(envelope["seq"])
            payload = envelope["payload"]
            if not isinstance(topic, str):
                raise TypeError(f"topic no es str: {type(topic)!r}")
        except (KeyError, TypeError, ValueError) as exc:
            return self._source_error("EnvelopeDecodeError", f"envelope invalido: {exc}"), False

        wire_topic_str = wire_topic.decode("utf-8", errors="replace")
        if topic != wire_topic_str:
            # El topic interno del payload no coincide con el frame que paso el
            # filtro SUBSCRIBE: publicador que miente o bug de wire. No se
            # procesa ni se usa para enrutar/calcular el run_key.
            return (
                self._source_error(
                    "EnvelopeTopicMismatch",
                    f"topic interno {topic!r} no coincide con el frame de wire "
                    f"{wire_topic_str!r}",
                ),
                False,
            )

        # `media.detection.v1.<id>` y `run.lifecycle.v1.<id>` comparten publicador
        # y contador, asi que el sufijo del topic identifica la secuencia.
        self._check_seq(topic.split(".v1.", 1)[-1], seq)

        if topic.startswith(LIFECYCLE_TOPIC_PREFIX):
            try:
                control = json.loads(payload)
            except (json.JSONDecodeError, TypeError) as exc:
                # Lifecycle ilegible: error de FUENTE, no termina la corrida (no
                # sabemos si era el run_finished). Sigue consumiendo y cierra
                # por idle timeout o polling.
                return self._source_error("LifecycleDecodeError", str(exc)), False
            if not isinstance(control, dict):
                return (
                    self._source_error(
                        "LifecycleDecodeError", "el payload de lifecycle no es un mapa"
                    ),
                    False,
                )
            if control.get("event") == "run_finished":
                logger.info(
                    "bus: run_finished media_run_id=%s status=%s",
                    control.get("media_run_id"),
                    control.get("status"),
                )
                return None, True
            return None, False

        try:
            # Misma ruta de validacion que la linea JSONL: paridad por construccion.
            event = DetectionEvent.model_validate(json.loads(payload))
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            return (
                (
                    seq,
                    None,
                    ErrorEvent(
                        control_run_id=self._control_run_id,
                        message=str(exc),
                        error_type=type(exc).__name__,
                        line_number=seq,
                    ),
                    None,
                ),
                False,
            )
        ts_receive_ms = ts_receive_monotonic * 1000.0
        return (seq, event, None, ts_receive_ms), False

    def _check_seq(self, run_key: str, seq: int) -> None:
        expected = self._expected_seq.get(run_key)
        if expected is None:
            if seq > 0:
                # Primer seq visto para este run_key y no arranca en 0: la
                # suscripcion llego tarde (o se reconecto) y esos eventos
                # previos son irrecuperables.
                self._dropped += seq
                logger.warning(
                    "bus: primer seq visto en %s es %d (se esperaba 0 al "
                    "suscribirse): %d eventos perdidos antes de suscribirse",
                    run_key,
                    seq,
                    seq,
                )
        elif seq > expected:
            gap = seq - expected
            self._dropped += gap
            logger.warning(
                "bus: hueco de seq en %s (esperado %d, recibido %d): %d eventos perdidos",
                run_key,
                expected,
                seq,
                gap,
            )
        # seq <= expected es un duplicado o un mensaje reordenado: no se cuenta
        # como drop y, clave, el puntero NO se mueve hacia atras (si lo
        # hiciera, el proximo mensaje legitimo se leeria como un hueco falso).
        if expected is None or seq >= expected:
            self._expected_seq[run_key] = seq + 1

    def _run_finished_by_polling(self) -> bool:
        """Fallback de cierre: GET /api/runs/{id} del media-plane (spec 41 SS3)."""
        try:
            with urllib.request.urlopen(self._poll_url, timeout=5.0) as response:
                body = json.loads(response.read())
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            logger.warning("bus: polling de %s fallo: %s", self._poll_url, exc)
            return False
        return body.get("status") in TERMINAL_STATUSES
