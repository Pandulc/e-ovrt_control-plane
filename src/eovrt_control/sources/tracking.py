"""Identidad por sujeto como capacidad del control-plane (decorador de fuente).

ADR-002 planeaba portar el `SimpleIoUTracker` al **media-plane** para que emitiera
`track_id` en `media.detection.v1`. Ese puerto nunca se ejecutó (doc 79: "hoy nadie
produce track_id"), y la campaña G1 (doc 89) midió la capacidad con una herramienta
post-hoc: **F1 0,789 → 0,930 con las MISMAS detecciones**, el mejor resultado del
banco.

Este modulo convierte esa herramienta en capacidad de plataforma, como decorador de
**fuente**, y no como paso del media-plane. Tres consecuencias:

  - **Sirve para DBE y para EBE/live por igual**: decora cualquier `MediaEventSource`
    (`JsonlSource` o `BusSource`), asi que la identidad no depende de que el productor
    la emita. El puerto al media-plane deja de ser necesario para tener G1.
  - **No toca el pipeline congelado** del media-plane.
  - **Es opt-in** (`input.track_persons`, default `false`): ninguna corrida ni config
    existente cambia de comportamiento.

Trade-off declarado: el `track_id` NO queda en `detections.jsonl` (la fuente de verdad
del media-plane), sino en los artefactos del control-plane (`subject_key` de
`pattern_events.jsonl`). La trazabilidad se conserva —el tracker es determinista y el
stream ordenado, asi que un replay reproduce las mismas identidades— y quien necesite
el artefacto con `track_id` embebido lo genera con
`python -m eovrt_control.tools.track_detections`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from eovrt_control.sources.base import MediaEventSource, SourceItem

logger = logging.getLogger(__name__)


class TrackingSource(MediaEventSource):
    """Decora una fuente asignando `track_id` a las detecciones de persona.

    Streaming y en orden de llegada: no reordena ni bufferea (en live no podria).
    Mantiene **un tracker por `source_id`** — con dos camaras en el mismo run, un
    tracker unico continuaria el track de una con las cajas de la otra por
    solapamiento geometrico, que entre fuentes distintas no significa nada.

    Delegacion de ciclo de vida: `close`/`request_stop`/`dropped_events` van a la
    fuente interna. La base define no-ops para los tres, asi que SIN delegar el
    decorador en live silenciaria `bus_dropped_events` (violacion de ADR-003: los
    drops nunca se silencian), no cerraria el socket del bus y anularia la parada
    cooperativa — cuya alternativa es la trampa SIGABRT de libzmq.
    """

    def __init__(self, inner: MediaEventSource, **tracker_kwargs) -> None:
        self._inner = inner
        self._tracker_kwargs = tracker_kwargs
        self._trackers: dict[str, object] = {}
        self._last_frame: dict[str, int] = {}
        self._warned_backwards: set[str] = set()

    @property
    def kind(self) -> str:  # type: ignore[override]
        return self._inner.kind

    def close(self) -> None:
        self._inner.close()

    def request_stop(self) -> None:
        self._inner.request_stop()

    @property
    def dropped_events(self) -> int:
        return self._inner.dropped_events

    def __getattr__(self, name: str):
        """Proxy transparente hacia la fuente decorada.

        El contrato de `MediaEventSource` se delega explicito arriba; esto cubre lo
        que una fuente CONCRETA expone de mas (`BusSource.endpoint`, contadores de
        diagnostico). Sin esto, un live con `track_persons: true` reventaria con
        AttributeError en cualquier consumidor que lea un atributo del bus — y el
        camino live no se puede testear a fondo sin hardware.

        Solo se consulta para atributos que la instancia NO tiene (Python llama
        `__getattr__` recien cuando falla la busqueda normal), asi que no puede
        tapar los metodos delegados de arriba.
        """
        return getattr(self._inner, name)

    def _tracker_for(self, source_id: str):
        from eovrt_labs.perception.tracking import SimpleIoUTracker

        tracker = self._trackers.get(source_id)
        if tracker is None:
            tracker = SimpleIoUTracker(**self._tracker_kwargs)
            self._trackers[source_id] = tracker
        return tracker

    def __iter__(self) -> Iterator[SourceItem]:
        for line_number, event, error, ts_receive_ms in self._inner:
            if event is None:
                yield (line_number, event, error, ts_receive_ms)
                continue
            self._assign(event)
            yield (line_number, event, error, ts_receive_ms)

    def _assign(self, event) -> None:
        persons = [d for d in event.detections if d.label == "person"]
        source_id = event.source.source_id
        frame = event.source.frame_index
        # El decorador procesa en orden de LLEGADA (en live no hay futuro). Un stream
        # con frames hacia atras (p.ej. un jsonl concatenado a mano) degradaria la
        # calidad del tracking en silencio: se avisa una vez por fuente.
        if frame is not None:
            last = self._last_frame.get(source_id)
            if last is not None and frame < last and source_id not in self._warned_backwards:
                self._warned_backwards.add(source_id)
                logger.warning(
                    "TrackingSource: frame_index hacia atras en %r (%d despues de %d); "
                    "el stream no esta en orden y la calidad del tracking se degrada. "
                    "Para archivos, ordenar antes o usar tools.track_detections.",
                    source_id, frame, last,
                )
            self._last_frame[source_id] = max(last or frame, frame)
        tracker = self._tracker_for(source_id)
        if not persons:
            # Un frame sin personas es informacion (envejece los tracks perdidos),
            # no un frame que no ocurrio.
            tracker.assign([], frame_index=event.source.frame_index,
                           timestamp_ms=event.source.timestamp_ms)
            return
        track_ids = tracker.assign(
            [d.bbox_xyxy for d in persons],
            confidences=[d.confidence for d in persons],
            frame_index=event.source.frame_index,
            timestamp_ms=event.source.timestamp_ms,
        )
        for detection, track_id in zip(persons, track_ids, strict=False):
            detection.track_id = track_id


def maybe_track(source: MediaEventSource, enabled: bool, **tracker_kwargs) -> MediaEventSource:
    """Envuelve la fuente si la config lo pide. Punto unico de decision."""
    return TrackingSource(source, **tracker_kwargs) if enabled else source
