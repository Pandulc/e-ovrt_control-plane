"""Esquemas y carga de configuracion del plano de control."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class RunSection(BaseModel):
    id: str | None = None
    scenario: str = "DBE"
    name: str = "control_replay"
    description: str | None = None
    # ADR-004: clave de la corrida paraguas; viaja al summary y a los eventos.
    experiment_id: str | None = None


class BusFinishSection(BaseModel):
    """Como se entera el consumidor de que la corrida termino (spec 41 SS3)."""

    signal: Literal["run_lifecycle"] = "run_lifecycle"
    # Fallback por polling de GET /api/runs/{id} del media-plane.
    poll_url: str | None = None
    poll_interval_s: float = 5.0


class BusInputSection(BaseModel):
    endpoint: str
    topics: list[str] = Field(
        default_factory=lambda: ["media.detection.v1.", "run.lifecycle.v1."]
    )
    hwm: int = 1000
    recv_timeout_ms: int = 1000
    # Corte de seguridad si el publicador muere sin `run_finished` y no hay poll_url.
    idle_timeout_s: float = 300.0
    finish: BusFinishSection = Field(default_factory=BusFinishSection)


class InputSection(BaseModel):
    type: Literal["media_jsonl", "bus"] = "media_jsonl"
    path: str | None = None
    bus: BusInputSection | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "InputSection":
        if self.type == "media_jsonl" and not self.path:
            raise ValueError("input.type='media_jsonl' requiere input.path")
        if self.type == "bus" and self.bus is None:
            raise ValueError("input.type='bus' requiere input.bus")
        return self


class PatternRegionConfig(BaseModel):
    type: str
    y_min_ratio: float = 0.0
    y_max_ratio: float = 1.0
    x_margin_ratio: float = 0.0
    # Pose no erguida: si ancho/alto de la caja del sujeto supera este ratio
    # (persona agachada/inclinada), la banda vertical deja de ser representativa
    # y la region se expande a la altura completa de la caja. None = desactivado.
    full_height_aspect_ratio: float | None = None


class DirectEvidenceClassConfig(BaseModel):
    """Una clase de evidencia DIRECTA aceptada por el patron (spec 41 §6.1).

    `prompt_id` matchea contra el prompt_id o el label de la deteccion. El gating
    por persona (doc 12 §4.2) depende de la forma de la deteccion:
      - `person_iou`: frases persona-centricas ("person without hard hat") — la caja
        ES una persona, se gatea por IoU contra una persona detectada.
      - `region_center`: detecciones de parte (`bare_head`) — caja chica, se gatea
        por centro dentro de la region del patron sobre la persona.
    """

    prompt_id: str
    min_confidence: float = 0.25
    match: Literal["person_iou", "region_center"] = "person_iou"
    iou_threshold: float = 0.5


class PatternEvidenceConfig(BaseModel):
    min_subject_confidence: float = 0.35
    min_absent_class_confidence: float = 0.25
    min_subject_area_px: float = 400.0
    # Estrategia de evidencia del patron (spec 41 §6.2, ADR-001/doc 12 §4):
    #   eind    — ausencia espacial (spatial_absence). Default: los pattern sets
    #             existentes no cambian.
    #   edir    — deteccion directa de la condicion (direct_evidence).
    #   hyb_or  — union: evidencia si cualquiera de las dos la aporta.
    #   hyb_and — corroboracion (factor de ventana): tramo de fusiones, aun no
    #             implementada — se rechaza en validacion para que no falle en
    #             silencio como un eind.
    strategy: Literal["eind", "edir", "hyb_or", "hyb_and"] = "eind"
    direct: list[DirectEvidenceClassConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_strategy(self) -> "PatternEvidenceConfig":
        if self.strategy in ("edir", "hyb_or", "hyb_and") and not self.direct:
            raise ValueError(
                f"strategy '{self.strategy}' requiere al menos una clase en 'direct' "
                "(spec 41 §6.1: lista de prompt_ids E-DIR aceptados)"
            )
        if self.strategy == "hyb_and":
            raise ValueError(
                "strategy 'hyb_and' (corroboracion con factor de ventana) llega con el "
                "tramo de fusiones de la Fase 2 — spec 41 §6.2; usar eind/edir/hyb_or"
            )
        return self


class PatternTimingConfig(BaseModel):
    confirm_after_frames: int = 1
    resolve_after_frames: int = 1
    confirm_after_ms: float | None = None
    resolve_after_ms: float | None = None
    # Expiracion de sujetos que dejan de observarse (opt-in; None desactiva).
    subject_absent_timeout_frames: int | None = None
    subject_absent_timeout_ms: float | None = None
    # Cooldown de re-alerta por (patron, sujeto) tras un ciclo resolved->confirmed.
    realert_cooldown_frames: int | None = None
    realert_cooldown_ms: float | None = None
    # Memoria de cobertura EPP: si el sujeto tuvo el EPP asociado hace menos de
    # esta ventana, se lo sigue tratando como cubierto (amortigua parpadeo del
    # detector y oclusiones breves). None = desactivado.
    coverage_memory_frames: int | None = None
    coverage_memory_ms: float | None = None


class PatternDefinition(BaseModel):
    id: str
    name: str
    description: str | None = None
    enabled: bool = True
    condition_id: str
    severity: str = "medium"
    subject_class: str = "person"
    required_absent_class: str
    # G0 (ADR-002): la escena es el nucleo. `subject` es demostrativa; si un evento
    # no trae track_id, el motor degrada a clave de escena (causa `no_track_id`)
    # en lugar de exigirlo por validacion.
    granularity: Literal["scene", "subject"] = "scene"
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


class AlertBusSection(BaseModel):
    """Publisher de alertas control->distribucion (spec 41 SS8). Apagado por default."""

    enabled: bool = False
    endpoint: str = "tcp://0.0.0.0:5558"
    hwm: int = Field(default=1000, gt=0)
    wait_for_subscriber_ms: int = Field(default=0, ge=0)


class ReplayConfig(BaseModel):
    run: RunSection
    input: InputSection
    patterns: PatternsSection
    outputs: OutputsSection = Field(default_factory=OutputsSection)
    logging: LoggingSection = Field(default_factory=LoggingSection)
    alert_bus: AlertBusSection = Field(default_factory=AlertBusSection)

    config_path: Path | None = Field(default=None, exclude=True)
    patterns_file: PatternsFile | None = Field(default=None, exclude=True)

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


# Rutas que, si la config viene por PAYLOAD, deben ser absolutas: sin `config_path`
# no hay contra que resolver una relativa (caeria contra el CWD del servicio, que
# es ambiguo). Regla anti-ambiguedad de spec 41 SS9 / ADR-009.
_PAYLOAD_PATH_FIELDS = ("patterns.file", "input.path", "outputs.base_dir")


def load_replay_config_data(data: dict[str, Any]) -> ReplayConfig:
    """Carga una config recibida por payload (ADR-009), sin archivo de respaldo."""
    config = ReplayConfig.model_validate(data)
    raw_by_field = {
        "patterns.file": config.patterns.file,
        "input.path": config.input.path,
        "outputs.base_dir": config.outputs.base_dir,
    }
    for field in _PAYLOAD_PATH_FIELDS:
        raw = raw_by_field[field]
        if raw is not None and not Path(raw).is_absolute():
            raise ValueError(
                f"Config por payload: `{field}` debe ser una ruta absoluta, no {raw!r}"
            )
    config.patterns_file = load_patterns_file(config.patterns.file)
    return config
