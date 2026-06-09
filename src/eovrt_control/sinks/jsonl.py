"""Sink JSONL simple para eventos Pydantic."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class JsonlSink:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8")

    def write(self, event: BaseModel | dict[str, Any]) -> None:
        if isinstance(event, BaseModel):
            payload = event.model_dump(mode="json")
        else:
            payload = event
        self._fh.write(json.dumps(payload, ensure_ascii=True) + "\n")

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "JsonlSink":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

