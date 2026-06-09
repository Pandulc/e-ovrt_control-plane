"""Contrato de errores de corrida."""

from __future__ import annotations

from pydantic import BaseModel


class ErrorEvent(BaseModel):
    schema_version: str = "control.error.v1"
    event_type: str = "control_error"
    control_run_id: str
    unit_id: str | None = None
    source_id: str | None = None
    message: str
    error_type: str
    line_number: int | None = None

