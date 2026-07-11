"""Fuente en memoria: tests deterministas del motor sin tocar disco ni red."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from eovrt_control.contracts.media import DetectionEvent
from eovrt_control.sources.base import MediaEventSource, SourceItem


class MemorySource(MediaEventSource):
    kind = "memory"

    def __init__(self, events: Iterable[DetectionEvent]) -> None:
        self._events = list(events)

    def __iter__(self) -> Iterator[SourceItem]:
        for index, event in enumerate(self._events, start=1):
            # Fuente sintetica de tests: no hay recepcion real, ts_receive_ms=None.
            yield index, event, None, None
