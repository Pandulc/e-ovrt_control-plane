"""Gestion de artefactos de corrida."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import BaseModel

from eovrt_control.config import ReplayConfig


class RunArtifacts:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)

    @property
    def pattern_events_path(self) -> Path:
        return self.run_dir / "pattern_events.jsonl"

    @property
    def alerts_path(self) -> Path:
        return self.run_dir / "alerts.jsonl"

    @property
    def metrics_path(self) -> Path:
        return self.run_dir / "metrics.jsonl"

    @property
    def errors_path(self) -> Path:
        return self.run_dir / "errors.jsonl"

    @property
    def summary_path(self) -> Path:
        return self.run_dir / "summary.json"

    @property
    def effective_config_path(self) -> Path:
        return self.run_dir / "effective_config.yaml"

    def write_effective_config(self, config: ReplayConfig) -> None:
        payload = config.model_dump(mode="json", exclude={"config_path", "patterns_file"})
        with self.effective_config_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=False)

    def write_summary(self, summary: BaseModel) -> None:
        with self.summary_path.open("w", encoding="utf-8") as fh:
            json.dump(summary.model_dump(mode="json"), fh, ensure_ascii=True, indent=2)
            fh.write("\n")

