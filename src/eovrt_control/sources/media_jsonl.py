"""Lectura de eventos JSONL del plano de medios."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from pydantic import ValidationError

from eovrt_control.contracts.errors import ErrorEvent
from eovrt_control.contracts.media import DetectionEvent


def iter_media_jsonl(
    path: Path,
    control_run_id: str,
) -> Iterator[tuple[int, DetectionEvent | None, ErrorEvent | None]]:
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
                yield line_number, DetectionEvent.model_validate(data), None
            except (json.JSONDecodeError, ValidationError) as exc:
                yield (
                    line_number,
                    None,
                    ErrorEvent(
                        control_run_id=control_run_id,
                        message=str(exc),
                        error_type=type(exc).__name__,
                        line_number=line_number,
                    ),
                )

