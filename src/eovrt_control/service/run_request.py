"""Contrato del request de corrida del control-plane (spec 41 SS5, ADR-009)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


class ControlRunRequest(BaseModel):
    # extra="forbid": un campo desconocido en el body -> 422, no se ignora en silencio.
    model_config = ConfigDict(extra="forbid")

    mode: Literal["replay", "live"]
    # ADR-009: la config llega por referencia (path a un YAML) o por payload completo.
    config_path: str | None = None
    config: dict[str, Any] | None = None
    # ADR-004: pisa el `run.experiment_id` de la config si viene.
    experiment_id: str | None = None

    @model_validator(mode="after")
    def exactly_one_config_source(self) -> "ControlRunRequest":
        if (self.config_path is None) == (self.config is None):
            raise ValueError(
                "Exactamente uno de `config_path` (por referencia) o `config` (por payload)"
            )
        return self
