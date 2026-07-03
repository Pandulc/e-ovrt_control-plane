"""Generacion de evidencia perceptual compatible con media.detection.v1."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["generate_detections_jsonl"]

if TYPE_CHECKING:
    from eovrt_labs.perception.generator import GenerationResult


def generate_detections_jsonl(*args, **kwargs) -> GenerationResult:
    from eovrt_labs.perception.generator import generate_detections_jsonl as _generate

    return _generate(*args, **kwargs)
