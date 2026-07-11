"""Lectura de eventos JSONL del plano de medios (DBE / replay)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

from pydantic import ValidationError

from eovrt_control.contracts.errors import ErrorEvent
from eovrt_control.contracts.media import DetectionEvent
from eovrt_control.sources.base import MediaEventSource, SourceItem


class JsonlSource(MediaEventSource):
    """Fuente sobre `detections.jsonl` del media-plane."""

    kind = "jsonl"

    def __init__(self, path: Path, control_run_id: str) -> None:
        self.path = Path(path)
        self.control_run_id = control_run_id

    def __iter__(self) -> Iterator[SourceItem]:
        if not self.path.exists():
            # Error de FUENTE: sin `line_number`, para que el runtime lo cuente
            # en errors_count pero no en units_failed (no hubo unidad).
            yield (
                0,
                None,
                ErrorEvent(
                    control_run_id=self.control_run_id,
                    message=f"Archivo de entrada no encontrado: {self.path}",
                    error_type="FileNotFoundError",
                ),
                None,
            )
            return

        with self.path.open("r", encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                    # Estampa en el instante de lectura: paridad con BusSource, que
                    # estampa en el instante de recepcion del socket.
                    yield (
                        line_number,
                        DetectionEvent.model_validate(data),
                        None,
                        time.monotonic() * 1000.0,
                    )
                except (json.JSONDecodeError, ValidationError) as exc:
                    yield (
                        line_number,
                        None,
                        ErrorEvent(
                            control_run_id=self.control_run_id,
                            message=str(exc),
                            error_type=type(exc).__name__,
                            line_number=line_number,
                        ),
                        None,
                    )
