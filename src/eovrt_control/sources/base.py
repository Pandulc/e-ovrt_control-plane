"""Interfaz unica de fuentes de eventos del plano de medios (spec 41 SS3).

Una fuente es un iterador de `event | error | END`, donde END es el agotamiento
del iterador. Tres implementaciones: `JsonlSource` (DBE/replay), `MemorySource`
(tests deterministas) y `BusSource` (EBE/live).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from eovrt_control.contracts.errors import ErrorEvent
from eovrt_control.contracts.media import DetectionEvent

# (indice, evento, error, ts_receive_ms): exactamente uno de evento/error no es None.
# El indice es el numero de linea (JSONL) o el `seq` del envelope (bus).
# ts_receive_ms es el instante monotonico de recepcion en ms (None si la fuente no lo da).
SourceItem = tuple[int, DetectionEvent | None, ErrorEvent | None, float | None]


class MediaEventSource(ABC):
    """Iterador de eventos del plano de medios.

    Convencion de errores: un `ErrorEvent` con `line_number is None` es un error
    de FUENTE (archivo inexistente, transporte caido) y no representa una unidad
    perdida; con `line_number` presente es una unidad que fallo el parseo.
    """

    kind: str = "unknown"

    @abstractmethod
    def __iter__(self) -> Iterator[SourceItem]:
        """Emite items hasta END (agotamiento)."""

    def close(self) -> None:
        """Libera recursos. Idempotente. Default: nada que liberar."""
        return None

    def request_stop(self) -> None:
        """Pide a la fuente que termine su iteracion cuanto antes.

        Es la forma SEGURA de desbloquear una fuente que espera en red desde otro
        hilo: cerrarle el socket por debajo es un uso multi-hilo no soportado por
        libzmq. Default: nada que interrumpir (las fuentes finitas se agotan solas).
        """
        return None

    @property
    def dropped_events(self) -> int:
        """Eventos que la fuente sabe que se perdieron en transito."""
        return 0
