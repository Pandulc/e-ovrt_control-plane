"""Alias de compatibilidad. La implementacion vive en `sources/jsonl.py`.

`iter_media_jsonl` se conserva porque hay configs y codigo externo que la
importan por nombre (spec 41 SS3: "renombre ... (compat: alias)").
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from eovrt_control.sources.base import SourceItem
from eovrt_control.sources.jsonl import JsonlSource

__all__ = ["JsonlSource", "iter_media_jsonl"]


def iter_media_jsonl(path: Path, control_run_id: str) -> Iterator[SourceItem]:
    yield from JsonlSource(path, control_run_id)
