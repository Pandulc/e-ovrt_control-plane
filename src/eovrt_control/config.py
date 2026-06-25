"""Esquemas y carga de configuracion del plano de control."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class RunSection(BaseModel):
    id: str | None = None
    scenario: str = "DBE"
    name: str = "control_replay"
    description: str | None = None


class InputSection(BaseModel):
    type: str = "media_jsonl"
    path: str


class PatternRegionConfig(BaseModel):
    type: str
    y_min_ratio: float = 0.0
    y_max_ratio: float = 1.0
    x_margin_ratio: float = 0.0


class PatternEvidenceConfig(BaseModel):
    min_subject_confidence: float = 0.35
    min_absent_class_confidence: float = 0.25
    min_subject_area_px: float = 400.0


class PatternTimingConfig(BaseModel):
    confirm_after_frames: int = 1
    resolve_after_frames: int = 1
    confirm_after_ms: float | None = None
    resolve_after_ms: float | None = None


class PatternDefinition(BaseModel):
    id: str
    name: str
    description: str | None = None
    enabled: bool = True
    condition_id: str
    severity: str = "medium"
    subject_class: str = "person"
    required_absent_class: str
    region: PatternRegionConfig
    evidence: PatternEvidenceConfig = Field(default_factory=PatternEvidenceConfig)
    timing: PatternTimingConfig = Field(default_factory=PatternTimingConfig)


class PatternSet(BaseModel):
    id: str
    description: str | None = None
    patterns: list[PatternDefinition]


class PatternsFile(BaseModel):
    pattern_set: PatternSet

    def active_patterns(self, active_ids: list[str] | None) -> list[PatternDefinition]:
        patterns = [pattern for pattern in self.pattern_set.patterns if pattern.enabled]
        if active_ids is None:
            return patterns
        by_id = {pattern.id: pattern for pattern in patterns}
        missing = [pattern_id for pattern_id in active_ids if pattern_id not in by_id]
        if missing:
            raise ValueError(f"Patrones activos no encontrados o deshabilitados: {missing}")
        return [by_id[pattern_id] for pattern_id in active_ids]


class PatternsSection(BaseModel):
    file: str
    active_ids: list[str] | None = None


class OutputsSection(BaseModel):
    base_dir: str = "runs"
    save_pattern_events_jsonl: bool = True
    save_alerts_jsonl: bool = True
    save_metrics_jsonl: bool = True
    save_errors_jsonl: bool = True
    save_summary_json: bool = True


class LoggingSection(BaseModel):
    level: str = "INFO"


class ReplayConfig(BaseModel):
    run: RunSection
    input: InputSection
    patterns: PatternsSection
    outputs: OutputsSection = Field(default_factory=OutputsSection)
    logging: LoggingSection = Field(default_factory=LoggingSection)

    config_path: Path | None = Field(default=None, exclude=True)
    patterns_file: PatternsFile | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_input_type(self) -> "ReplayConfig":
        if self.input.type != "media_jsonl":
            raise ValueError("Por ahora solo se soporta input.type='media_jsonl'")
        return self

    def resolve_path(self, raw_path: str) -> Path:
        path = Path(raw_path)
        if path.is_absolute() or self.config_path is None:
            return path
        return (self.config_path.parent / path).resolve()


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"El archivo YAML debe contener un objeto: {path}")
    return data


def load_patterns_file(path: str | Path) -> PatternsFile:
    return PatternsFile.model_validate(_load_yaml(Path(path)))


def load_replay_config(path: str | Path) -> ReplayConfig:
    config_path = Path(path).resolve()
    config = ReplayConfig.model_validate(_load_yaml(config_path))
    config.config_path = config_path
    patterns_path = config.resolve_path(config.patterns.file)
    config.patterns_file = load_patterns_file(patterns_path)
    return config
