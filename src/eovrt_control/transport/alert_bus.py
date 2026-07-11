"""Publisher de alertas al bus (espejo del BusPublisher del media-plane).

Mismo envelope bus.envelope.v1 y las mismas garantias: seq monotono consumido
aunque el envio se descarte; nunca bloquea ni propaga excepciones al runtime;
el JSONL es la verdad, el bus solo transporta (ADR-003).

Copia deliberada de la estructura de
`e-ovrt_media-plane/src/eovrt_media/transport/bus.py` (XPUB, seq
pre-incrementado, NOBLOCK, close idempotente, fuga de socket protegida en
__init__), con el topico y el prefijo de lifecycle propios del control-plane.
"""

from __future__ import annotations

import json
import logging
import time

import msgpack
import zmq

logger = logging.getLogger(__name__)

ENVELOPE_SCHEMA_VERSION = "bus.envelope.v1"
ALERT_TOPIC_PREFIX = "control.alert.v1."
LIFECYCLE_TOPIC_PREFIX = "run.lifecycle.v1."
LIFECYCLE_SCHEMA_VERSION = "run.lifecycle.v1"


def encode_envelope(
    *, topic: str, key: str, seq: int, payload: bytes, ts_publish_ms: float
) -> bytes:
    """Serializa el envelope. `payload` son los bytes del evento tal cual van al JSONL."""
    return msgpack.packb(
        {
            "schema_version": ENVELOPE_SCHEMA_VERSION,
            "topic": topic,
            "key": key,
            "seq": seq,
            "ts_publish_ms": ts_publish_ms,
            "payload": payload,
        },
        use_bin_type=True,
    )


class AlertBusPublisher:
    """Socket XPUB con contador de secuencia por corrida.

    XPUB en vez de PUB: del lado del envio son equivalentes, pero XPUB entrega una
    notificacion cuando un SUB se suscribe, lo que permite implementar la regla de
    "consumidor suscripto antes del disparo" sin dormir a ciegas.

    `send_failures` cuenta unicamente errores de envio (excepciones atrapadas en
    `publish`, por ejemplo `zmq.Again` cuando se alcanza el HWM). NO cuenta las
    perdidas silenciosas de PUB/XPUB: sin `ZMQ_XPUB_NODROP` (que no se activa aqui,
    porque cambiaria la semantica de bloqueo/exito de `send_multipart`), libzmq
    descarta mensajes en silencio al llegar al HWM y `send()` devuelve exito. La
    unica senal fiable de perdida es el hueco de `seq` que ve el consumidor del
    lado del bus (ver docstring equivalente en el media-plane).
    """

    def __init__(
        self, endpoint: str, *, hwm: int = 1000, wait_for_subscriber_ms: int = 0
    ) -> None:
        self.endpoint = endpoint
        # Contador de errores de envio (no de perdidas por HWM, ver docstring de la clase).
        self.send_failures = 0
        self._seq = 0
        self._closed = False
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.XPUB)
        try:
            self._sock.setsockopt(zmq.SNDHWM, hwm)
            self._sock.setsockopt(zmq.RCVHWM, 16)
            self._sock.setsockopt(zmq.LINGER, 0)
            self._sock.setsockopt(zmq.XPUB_VERBOSE, 1)
            self._sock.bind(endpoint)
        except Exception:
            # Sin esto, un bind/setsockopt fallido deja el socket abierto sin
            # referencia (fuga): nadie mas lo va a cerrar. Se cierra y se
            # relanza para que el llamador vea el fallo real.
            self._sock.close(linger=0)
            raise
        if wait_for_subscriber_ms > 0:
            self.wait_for_subscriber(wait_for_subscriber_ms)

    def wait_for_subscriber(self, timeout_ms: int, expected: int = 1) -> bool:
        """Espera notificaciones de suscripcion del XPUB (spec 40 SS3.2 regla 1).

        Drena notificaciones hasta juntar `expected` o hasta que venza el
        `timeout_ms` total (no por notificacion individual). Si el deadline vence
        habiendo drenado menos de `expected`, devuelve False: la corrida sigue
        igual, sin bus (el JSONL es la verdad). Nunca levanta.
        """
        poller = zmq.Poller()
        poller.register(self._sock, zmq.POLLIN)
        deadline = time.monotonic() + timeout_ms / 1000.0
        received = 0
        try:
            while received < expected:
                remaining_ms = (deadline - time.monotonic()) * 1000.0
                if remaining_ms <= 0:
                    break
                if not dict(poller.poll(timeout=remaining_ms)):
                    break
                self._sock.recv()  # b"\x01<topic>"
                received += 1
            if received < expected:
                logger.warning(
                    "alert_bus: %d/%d suscripciones en %d ms; se publica igual "
                    "(el JSONL es la verdad)",
                    received,
                    expected,
                    timeout_ms,
                )
                return False
            return True
        finally:
            poller.unregister(self._sock)

    def publish(self, topic: str, key: str, payload: bytes) -> int:
        """Publica y devuelve el `seq` asignado. Nunca bloquea, nunca levanta hacia el pipeline.

        Si el publicador ya esta cerrado, es un no-op seguro: el `seq` igual se
        consume (el consumidor vera el hueco) pero no se intenta enviar nada.
        Cualquier error de codificacion o de envio (incluido `zmq.Again` por HWM)
        se atrapa y se loguea; nunca se propaga.
        """
        seq = self._seq
        self._seq += 1

        if self._closed:
            logger.debug("alert_bus: publish() sobre publicador cerrado, seq=%d descartado", seq)
            return seq

        try:
            envelope = encode_envelope(
                topic=topic, key=key, seq=seq, payload=payload, ts_publish_ms=time.time() * 1000.0
            )
            self._sock.send_multipart([topic.encode("utf-8"), envelope], flags=zmq.NOBLOCK)
        except zmq.Again:
            # HWM alcanzado. El `seq` ya se consumio: el consumidor vera el hueco.
            self.send_failures += 1
            logger.warning("alert_bus: HWM alcanzado, envelope seq=%d descartado", seq)
        except zmq.ZMQError:
            # Error real de socket (p.ej. socket cerrado bajo carrera, ENOTSOCK).
            self.send_failures += 1
            logger.warning(
                "alert_bus: error de envio, envelope seq=%d descartado", seq, exc_info=True
            )
        except Exception:
            # Fallo de serializacion (msgpack) u otro error inesperado: nunca escapa.
            self.send_failures += 1
            logger.warning(
                "alert_bus: fallo al codificar/enviar, envelope seq=%d descartado",
                seq,
                exc_info=True,
            )
        return seq

    def publish_run_finished(self, control_run_id: str, status: str) -> None:
        """Sentinela END de la corrida (ADR-007): se emite pase lo que pase."""
        payload = json.dumps(
            {
                "schema_version": LIFECYCLE_SCHEMA_VERSION,
                "event": "run_finished",
                "control_run_id": control_run_id,
                "status": status,
            }
        ).encode("utf-8")
        self.publish(f"{LIFECYCLE_TOPIC_PREFIX}{control_run_id}", control_run_id, payload)

    def close(self) -> None:
        """Cierra el socket subyacente. Idempotente y nunca levanta hacia el pipeline.

        Si `self._sock.close()` falla, se loguea un warning y el publicador se
        marca como cerrado igual: `publish()` debe seguir siendo el no-op seguro
        que ya es, pase lo que pase con el cierre real del socket.
        """
        if not self._closed:
            try:
                self._sock.close(linger=0)
            except Exception:
                logger.warning("alert_bus: fallo al cerrar el socket", exc_info=True)
            finally:
                self._closed = True
