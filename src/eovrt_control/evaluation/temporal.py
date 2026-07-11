"""Evaluacion temporal de alertas contra ground truth debil."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from statistics import mean, median
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eovrt_control.config import PatternDefinition, load_patterns_file
from eovrt_control.contracts.alerts import AlertEvent
from eovrt_control.contracts.media import DetectionEvent
from eovrt_control.engine.evaluators.spatial_absence import evaluate_spatial_absence

logger = logging.getLogger(__name__)

# Repo root para localizar un pattern set default cuando se pasa --detections
# sin --patterns (spec del audit fix SDR/TTFD): src/eovrt_control/evaluation/
# -> parents[3] es la raiz del repo (donde vive configs/).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PATTERNS_PATH = _REPO_ROOT / "configs" / "patterns" / "cr01_cr02_v2.yaml"


class ExpectedAlert(BaseModel):
    id: str
    condition_id: str
    # `level` gobierna el matching de forma EXPLICITA. Nunca se re-infiere de la
    # presencia de `subject_key`: un GT de sujeto al que le falta la clave debe
    # fallar ruidosamente, no caer a matching por escena e inflar el F1.
    level: Literal["scene", "subject"] = "subject"
    subject_key: str | None = None
    source_id: str | None = None
    first_evidence_frame_index: int
    expected_alert_frame_index: int
    max_alert_frame_index: int | None = None
    first_evidence_timestamp_ms: float | None = None

    @model_validator(mode="after")
    def _require_key_for_level(self) -> ExpectedAlert:
        if self.level == "subject" and self.subject_key is None:
            raise ValueError(
                f"Alerta esperada {self.id!r}: level='subject' exige `subject_key`."
            )
        if self.level == "scene" and self.source_id is None:
            raise ValueError(
                f"Alerta esperada {self.id!r}: level='scene' exige `source_id` "
                "(el matching de escena es estructurado por condition_id + source_id)."
            )
        return self


class TemporalGroundTruth(BaseModel):
    scenario_id: str
    description: str | None = None
    expected_alerts: list[ExpectedAlert] = Field(default_factory=list)


class SubThresholdEvent(BaseModel):
    """Evento real pero no alertable (spec 43 SS4.1): permite distinguir un FP
    verdadero de una alerta a un evento sub-umbral."""

    condition_id: str
    start_ms: float
    end_ms: float
    reason: str | None = None


class ClipEpisodeV2(BaseModel):
    """Episodio escena-condicion (o subject) del schema clip_gt.v2 (spec 43 SS4), en ms."""

    id: str
    condition_id: str
    level: Literal["scene", "subject"] = "scene"
    source_id: str | None = None
    subject_key: str | None = None
    start_ms: float  # inicio anotado de la condicion (t0 oficial, spec 43 SS4.1)
    end_ms: float
    subjects_in_evidence: int | None = None

    @model_validator(mode="after")
    def _require_key_for_level(self) -> ClipEpisodeV2:
        if self.level == "subject" and self.subject_key is None:
            raise ValueError(f"Episodio {self.id!r}: level='subject' exige `subject_key`.")
        if self.level == "scene" and self.source_id is None:
            raise ValueError(f"Episodio {self.id!r}: level='scene' exige `source_id`.")
        if self.end_ms < self.start_ms:
            raise ValueError(f"Episodio {self.id!r}: end_ms < start_ms.")
        return self


class ClipGtProvenance(BaseModel):
    """Procedencia del `clip_gt.v2` (video-gt-lab `derive_clip_gt`, hito I3 del
    repo `e-ovrt_datasets`). `pattern_set_ms` versiona la persistencia minima
    por `condition_id` con la que se derivaron los episodios del clip (p.ej.
    `{"CR-01": 4000, "CR-02": 7000}`); es la fuente de verdad de esos umbrales,
    no `DEFAULT_MATCHING_WINDOWS` (hallazgo de auditoria: doble fuente de
    verdad). `extra="allow"` porque el productor puede sumar campos de
    procedencia (hash, herramienta, version) sin que este consumidor rompa.
    """

    model_config = ConfigDict(extra="allow")

    xml_sha256: str | None = None
    pattern_set_ms: dict[str, float] | None = None
    tool: str | None = None


class ClipGroundTruthV2(BaseModel):
    schema_version: Literal["clip_gt.v2"]
    clip_id: str
    source_file: str | None = None
    block: str | None = None
    scenario: str | None = None
    fps_nominal: float | None = None
    duration_ms: float | None = None
    recording: dict[str, Any] | None = None
    annotation: dict[str, Any] | None = None
    negative: bool = False
    episodes: list[ClipEpisodeV2] = Field(default_factory=list)
    sub_threshold_events: list[SubThresholdEvent] = Field(default_factory=list)
    # Opcional y aditivo: GTs historicos sin este bloque siguen validando
    # (antes quedaba silenciosamente descartado por extra="ignore" del default
    # de pydantic). Lo usa `_resolve_matching_windows` para no depender de
    # `DEFAULT_MATCHING_WINDOWS` cuando el GT trae su propio pattern set.
    provenance: ClipGtProvenance | None = None

    @model_validator(mode="after")
    def _internal_consistency(self) -> ClipGroundTruthV2:
        if self.negative and self.episodes:
            raise ValueError(f"Clip {self.clip_id!r}: negative=True pero trae episodios.")
        if self.duration_ms is not None:
            for ep in self.episodes:
                if ep.end_ms > self.duration_ms:
                    raise ValueError(
                        f"Clip {self.clip_id!r}: episodio {ep.id!r} excede duration_ms."
                    )
        return self


class MatchingWindow(BaseModel):
    """Ventana de matching alerta<->episodio (spec 43 SS4.1). La alerta legitima
    cae en [start_ms + persistencia_min, start_ms + t_alert_max]."""

    persistencia_min_ms: float
    t_alert_max_ms: float


# Persistencias alineadas al pattern set oficial del motor
# (`configs/patterns/cr01_cr02_v2.yaml`, que cita la Tabla 24/D.4 del informe):
# CR-01 confirm_after_ms=4000, CR-02 confirm_after_ms=7000. Los techos
# t_alert_max_ms si son Tabla D.4 (spec 43 SS4.1 / SS10) y no se tocan.
# Parametrizable: el fixture sintetico usa timings comprimidos y el evaluador
# acepta un override por corrida/pattern set.
DEFAULT_MATCHING_WINDOWS: dict[str, MatchingWindow] = {
    "CR-01": MatchingWindow(persistencia_min_ms=4000.0, t_alert_max_ms=10000.0),
    "CR-02": MatchingWindow(persistencia_min_ms=7000.0, t_alert_max_ms=20000.0),
}


class EffectiveMatchingWindow(BaseModel):
    """Ventana de matching REALMENTE usada para una condicion en una corrida,
    con su origen. Aditivo en `TemporalAlertEvaluation`: deja rastro auditable
    de si la ventana salio del caller, del `provenance` del GT o de la tabla
    default, para que una evaluacion nunca reporte metricas silenciosamente
    calculadas contra un umbral que no coincide con el que genero el GT."""

    persistencia_min_ms: float
    t_alert_max_ms: float
    origin: Literal["caller", "gt_provenance", "defaults"]


def _resolve_matching_windows(
    ground_truth: ClipGroundTruthV2,
    matching_windows: dict[str, MatchingWindow] | None,
) -> dict[str, EffectiveMatchingWindow]:
    """Resuelve, por `condition_id` presente en los episodios del GT, la
    ventana de matching efectiva y su origen (hallazgo de auditoria: doble
    fuente de verdad de umbrales de persistencia entre el `provenance` del GT
    y `DEFAULT_MATCHING_WINDOWS` hardcodeado).

    Prioridad:
      1. `matching_windows` explicito del caller: absoluta. Un `matching_windows`
         vacio (`{}`/falsy) se trata igual que `None` -- mismo comportamiento
         que el `matching_windows or DEFAULT_MATCHING_WINDOWS` previo a este
         fix. Si el caller cubre solo algunas condiciones, las demas siguen
         la prioridad 2/3.
      2. `ground_truth.provenance.pattern_set_ms[condition_id]`: la persistencia
         minima con la que se DERIVARON los episodios de este GT especifico
         (video-gt-lab `derive_clip_gt`, I3). Solo se consulta si el caller no
         paso ventanas. El `t_alert_max_ms` se toma igual del default de esa
         condicion: provenance versiona persistencia, nunca el techo de
         latencia (spec 43 SS4.1 no lo versiona por clip).
      3. `DEFAULT_MATCHING_WINDOWS` (Tabla D.4): si no hay caller ni provenance
         para esa condicion.

    Si provenance trae una `condition_id` sin entrada en
    `DEFAULT_MATCHING_WINDOWS` no hay forma de derivar su `t_alert_max_ms` sin
    inventar un numero: esa condicion queda simplemente sin ventana resuelta.
    El llamador (`_evaluate_v2`) ya sabe declarar eso via el warning
    "sin matching_window" y contar el episodio como missed, en vez de fallar
    en silencio con un techo de latencia adivinado.
    """

    conditions = {episode.condition_id for episode in ground_truth.episodes}
    caller_windows = matching_windows or None
    pattern_set_ms = (
        ground_truth.provenance.pattern_set_ms
        if ground_truth.provenance is not None and caller_windows is None
        else None
    )

    resolved: dict[str, EffectiveMatchingWindow] = {}
    for condition_id in conditions:
        if caller_windows is not None and condition_id in caller_windows:
            window = caller_windows[condition_id]
            resolved[condition_id] = EffectiveMatchingWindow(
                persistencia_min_ms=window.persistencia_min_ms,
                t_alert_max_ms=window.t_alert_max_ms,
                origin="caller",
            )
            continue

        if pattern_set_ms and condition_id in pattern_set_ms:
            default = DEFAULT_MATCHING_WINDOWS.get(condition_id)
            if default is not None:
                resolved[condition_id] = EffectiveMatchingWindow(
                    persistencia_min_ms=float(pattern_set_ms[condition_id]),
                    t_alert_max_ms=default.t_alert_max_ms,
                    origin="gt_provenance",
                )
                continue
            # Sin t_alert_max conocido para esta condicion: no se arma ventana,
            # ver docstring.

        default = DEFAULT_MATCHING_WINDOWS.get(condition_id)
        if default is not None:
            resolved[condition_id] = EffectiveMatchingWindow(
                persistencia_min_ms=default.persistencia_min_ms,
                t_alert_max_ms=default.t_alert_max_ms,
                origin="defaults",
            )

    return resolved


def _episode_key_matches(alert: AlertEvent, episode: ClipEpisodeV2) -> bool:
    if episode.level == "subject":
        return alert.subject_key == episode.subject_key
    return alert.source_id == episode.source_id


def _alert_in_episode_window(
    alert: AlertEvent, episode: ClipEpisodeV2, window: MatchingWindow | EffectiveMatchingWindow
) -> bool:
    if alert.timestamp_ms is None:
        return False
    lo = episode.start_ms + window.persistencia_min_ms
    hi = episode.start_ms + window.t_alert_max_ms
    return lo <= alert.timestamp_ms <= hi


class AlertMatch(BaseModel):
    expected_id: str
    alert_id: str
    condition_id: str
    subject_key: str
    expected_alert_frame_index: int
    alert_frame_index: int | None
    latency_frames_from_first_evidence: int | None
    latency_ms_from_first_evidence: float | None = None


class MissedAlert(BaseModel):
    expected_id: str
    condition_id: str
    # `None` en episodios de escena: ahi no existe una clave de sujeto y
    # fabricar una sintetica solo confundiria a quien la cruce con las claves
    # reales del motor. `source_id` es el identificador del episodio de escena.
    subject_key: str | None = None
    source_id: str | None = None
    expected_alert_frame_index: int
    max_alert_frame_index: int | None = None


class UnexpectedAlert(BaseModel):
    alert_id: str
    condition_id: str
    subject_key: str
    frame_index: int | None
    reason: str


class TtfdEpisodeResult(BaseModel):
    """TTFD (time-to-first-detection) de un episodio (spec 43 SS10).

    `ttfd_ms` es `None` cuando el episodio no tuvo deteccion positiva (o no es
    resoluble, ver `cause`); en ese caso el episodio cuenta como no-detectado
    para TTFD, nunca como 0.0."""

    episode_id: str
    ttfd_ms: float | None = None
    cause: str | None = None


class SdrEpisodeResult(BaseModel):
    """SDR (sustained detection ratio) de un episodio (spec 43 SS10), en [0, 1]."""

    episode_id: str
    sdr: float | None = None
    cause: str | None = None


class TemporalAlertEvaluation(BaseModel):
    schema_version: str = "control.eval.temporal.v1"
    scenario_id: str
    alerts_path: str
    ground_truth_path: str
    expected_alerts_count: int
    observed_alerts_count: int
    matched_alerts_count: int
    missed_alerts_count: int
    unexpected_alerts_count: int
    duplicate_alerts_count: int
    precision: float
    recall: float
    f1: float
    avg_latency_frames_from_first_evidence: float | None = None
    avg_latency_ms_from_first_evidence: float | None = None
    # Campos v2 (episodio, ADR-011/ADR-006). Aditivos: schema_version no cambia.
    re_alerts_count: int = 0
    sub_threshold_count: int = 0
    applicability_state: str | None = None
    applicability_cause: str | None = None
    avg_latency_ms_from_episode_start: float | None = None
    # Rastro auditable: ventana de matching efectivamente usada por condicion y
    # su origen (caller / gt_provenance / defaults). Aditivo, solo se llena en
    # el path v2 (episodio en ms); el path v1 no tiene ventanas de matching.
    effective_matching_windows: dict[str, EffectiveMatchingWindow] = Field(default_factory=dict)
    # Senal ruidosa ante un GT y unas alertas de granularidades incompatibles.
    warnings: list[str] = Field(default_factory=list)
    matches: list[AlertMatch] = Field(default_factory=list)
    missed_alerts: list[MissedAlert] = Field(default_factory=list)
    unexpected_alerts: list[UnexpectedAlert] = Field(default_factory=list)
    # --- SDR + TTFD (spec 43 SS10). Aditivos: no rompen el schema ni el gate
    # F1=1.0. Se llenan solo si `evaluate_temporal_alerts` recibe
    # `detections_path`; sin eso quedan en sus defaults (None/[]) con
    # `ttfd_sdr_applicability="not_applicable:no_detections_provided"`. ---
    avg_ttfd_ms: float | None = None
    avg_sdr: float | None = None
    ttfd_by_episode: list[TtfdEpisodeResult] = Field(default_factory=list)
    sdr_by_episode: list[SdrEpisodeResult] = Field(default_factory=list)
    # Formato "<estado>" o "<estado>:<causa>" (ADR-006). Un solo campo string
    # porque asi lo pide el deliverable; las causas por-episodio (mas finas,
    # p.ej. `subject_level_identity`) viven en `cause` de cada entrada de
    # `ttfd_by_episode`/`sdr_by_episode`.
    ttfd_sdr_applicability: str | None = None
    # Criterio de "deteccion positiva valida" auditable, p.ej.
    # "spatial_absence(cr01_cr02_v2) >=1 evidencia". `None` si no se computo.
    positive_criterion: str | None = None
    detections_path: str | None = None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


def _load_ground_truth(path: Path) -> TemporalGroundTruth | ClipGroundTruthV2:
    """Carga el ground truth SIN aplanar: v1 (`TemporalGroundTruth`, expected_alerts
    por frame) y v2 (`ClipGroundTruthV2`, episodios en ms, spec 43 SS4) son schemas
    distintos; el dispatch de `evaluate_temporal_alerts` decide segun el tipo."""
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") == "clip_gt.v2":
        return ClipGroundTruthV2.model_validate(raw)
    return TemporalGroundTruth.model_validate(raw)


def _load_alerts(path: Path) -> list[AlertEvent]:
    return [AlertEvent.model_validate(row) for row in _read_jsonl(path)]


def _load_detections(path: Path) -> list[DetectionEvent]:
    return [DetectionEvent.model_validate(row) for row in _read_jsonl(path)]


def _resolve_patterns_path(patterns_path: str | Path | None) -> Path:
    """Resuelve el pattern set a usar para el criterio de deteccion positiva.

    Prioridad: `patterns_path` explicito > default razonable del repo
    (`configs/patterns/cr01_cr02_v2.yaml`, el pattern set oficial citado en
    `DEFAULT_MATCHING_WINDOWS`). Si no hay explicito y el default no existe en
    disco, se exige `--patterns` en vez de adivinar."""

    if patterns_path is not None:
        return Path(patterns_path)
    if _DEFAULT_PATTERNS_PATH.exists():
        return _DEFAULT_PATTERNS_PATH
    raise ValueError(
        "Se paso detections_path sin patterns_path y no se encontro el pattern set "
        f"default ({_DEFAULT_PATTERNS_PATH}). Pase patterns_path explicitamente."
    )


def _nominal_step_ms(events_sorted: list[DetectionEvent]) -> float:
    """Paso nominal de la cadencia de una fuente: mediana de gaps entre eventos
    consecutivos (TODOS los eventos de la fuente, positivos o no -- es la
    cadencia del feed, no de las detecciones positivas). Usado para (a) el
    tramo de cobertura del ultimo evento positivo y (b) el umbral de tolerancia
    de hueco corto al fusionar tramos de SDR."""

    timestamps = [event.source.timestamp_ms for event in events_sorted]
    gaps = [b - a for a, b in zip(timestamps, timestamps[1:]) if b is not None and a is not None]
    return median(gaps) if gaps else 0.0


def _positive_flags_for_source(
    events_sorted: list[DetectionEvent], pattern: PatternDefinition
) -> list[bool]:
    """Criterio de 'deteccion positiva valida' (item 2 del audit fix): reusa el
    evaluador real del motor (`evaluate_spatial_absence`). Un evento es
    positivo para la condicion de `pattern` si el evaluador produce >=1
    evidencia (sujeto person que pasa los gates de confianza/area y carece de
    la clase requerida) -- NO se inventa una tercera fuente de verdad."""

    return [bool(evaluate_spatial_absence(event, pattern).evidences) for event in events_sorted]


def _ttfd_for_episode(
    events_sorted: list[DetectionEvent], flags: list[bool], episode: ClipEpisodeV2
) -> tuple[float | None, str | None]:
    """TTFD de un episodio (spec 43 SS10): t0 = `episode.start_ms`, t1 = timestamp
    de la primera deteccion positiva dentro de `[start_ms, end_ms]`. `None` (con
    causa) si no hay ninguna -- el episodio cuenta como no-detectado."""

    positive_ts = [
        event.source.timestamp_ms
        for event, is_positive in zip(events_sorted, flags)
        if is_positive
        and event.source.timestamp_ms is not None
        and episode.start_ms <= event.source.timestamp_ms <= episode.end_ms
    ]
    if not positive_ts:
        return None, "no_positive_detected"
    return min(positive_ts) - episode.start_ms, None


def _sdr_for_episode(
    events_sorted: list[DetectionEvent],
    flags: list[bool],
    episode: ClipEpisodeV2,
    nominal_step_ms: float,
) -> tuple[float | None, str | None]:
    """SDR de un episodio (spec 43 SS10): proporcion de `[start_ms, end_ms]`
    cubierta por deteccion 'sostenida'.

    Definicion operativa (criterio declarado por estrategia, item 5 del audit
    fix): cada evento positivo cubre `[timestamp, siguiente_evento)`, donde
    "siguiente evento" es el proximo evento (positivo o no) de la MISMA
    fuente; el ultimo evento de la fuente (o el ultimo positivo sin evento
    posterior) cubre hasta `timestamp + paso_nominal`. Los tramos de cobertura
    resultantes se funden si el hueco entre ellos es `<= paso_nominal`
    ("sostenida" tolera parpadeo/oclusion breve del detector); huecos mayores
    quedan sin cubrir. El resultado se recorta a `[start_ms, end_ms]` del
    episodio y se normaliza por su duracion, clampeado a `[0, 1]`."""

    duration = episode.end_ms - episode.start_ms
    if duration <= 0:
        return None, "zero_duration_episode"

    n = len(events_sorted)
    raw_intervals: list[tuple[float, float]] = []
    for index, (event, is_positive) in enumerate(zip(events_sorted, flags)):
        if not is_positive or event.source.timestamp_ms is None:
            continue
        start = event.source.timestamp_ms
        if index + 1 < n and events_sorted[index + 1].source.timestamp_ms is not None:
            end = events_sorted[index + 1].source.timestamp_ms
        else:
            end = start + nominal_step_ms
        if end > start:
            raw_intervals.append((start, end))

    if not raw_intervals:
        return 0.0, "no_positive_detected"

    raw_intervals.sort()
    merged: list[list[float]] = []
    for start, end in raw_intervals:
        if merged and start - merged[-1][1] <= nominal_step_ms:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    covered_ms = 0.0
    for start, end in merged:
        clipped_start = max(start, episode.start_ms)
        clipped_end = min(end, episode.end_ms)
        if clipped_end > clipped_start:
            covered_ms += clipped_end - clipped_start

    sdr = max(0.0, min(1.0, covered_ms / duration))
    return sdr, None


def _compute_ttfd_sdr(
    ground_truth: TemporalGroundTruth | ClipGroundTruthV2,
    detections_path: str | Path | None,
    patterns_path: str | Path | None,
) -> dict[str, Any]:
    """Calcula SDR + TTFD (spec 43 SS10) sobre `ground_truth.episodes`.

    Aplicabilidad (ADR-006):
      - sin `detections_path`: `not_applicable:no_detections_provided`.
      - detecciones sin `timestamp_ms` (fuente no temporal): `not_applicable:non_temporal_source`.
      - ground truth v1 (frame-based, sin episodios en ms): `not_applicable:non_v2_ground_truth`
        (desvio documentado: la spec no cubre este caso explicitamente, pero TTFD/SDR
        estan definidos sobre `[start_ms, end_ms]` de un episodio v2).
      - en otro caso: `computed` (aunque episodios individuales puedan traer su
        propia `cause` de no-aplicabilidad, ver `TtfdEpisodeResult`/`SdrEpisodeResult`).

    Filtrado por identidad (item 3/7): solo eventos con `source.source_id ==
    episode.source_id`. Los episodios `level='subject'` no son resolubles
    contra las claves del motor (`pattern_id:source_id:track_id` vs GT
    `subject_key`); si el episodio trae `source_id` se filtra igual por fuente
    (aproximacion honesta: TTFD/SDR de la fuente completa, no del sujeto
    puntual) y si no lo trae se marca `subject_level_identity` por episodio.
    """

    empty = {
        "avg_ttfd_ms": None,
        "avg_sdr": None,
        "ttfd_by_episode": [],
        "sdr_by_episode": [],
        "ttfd_sdr_applicability": "not_applicable:no_detections_provided",
        "positive_criterion": None,
        "detections_path": None,
    }

    if detections_path is None:
        return empty

    detections_path = Path(detections_path)
    detections = _load_detections(detections_path)
    result = dict(empty)
    result["detections_path"] = str(detections_path)

    if not detections or all(event.source.timestamp_ms is None for event in detections):
        result["ttfd_sdr_applicability"] = "not_applicable:non_temporal_source"
        return result

    if not isinstance(ground_truth, ClipGroundTruthV2):
        result["ttfd_sdr_applicability"] = "not_applicable:non_v2_ground_truth"
        return result

    patterns_file = load_patterns_file(_resolve_patterns_path(patterns_path))
    patterns_by_condition = {
        pattern.condition_id: pattern for pattern in patterns_file.active_patterns(None)
    }
    positive_criterion = f"spatial_absence({patterns_file.pattern_set.id}) >=1 evidencia"

    events_by_source: dict[str, list[DetectionEvent]] = {}
    for event in detections:
        if event.source.timestamp_ms is None:
            continue
        events_by_source.setdefault(event.source.source_id, []).append(event)
    for events in events_by_source.values():
        events.sort(key=lambda event: event.source.timestamp_ms)

    nominal_steps = {
        source_id: _nominal_step_ms(events) for source_id, events in events_by_source.items()
    }
    flags_cache: dict[tuple[str, str], list[bool]] = {}

    def _flags_for(condition_id: str, source_id: str, events: list[DetectionEvent]) -> list[bool]:
        key = (condition_id, source_id)
        if key not in flags_cache:
            pattern = patterns_by_condition[condition_id]
            flags_cache[key] = _positive_flags_for_source(events, pattern)
        return flags_cache[key]

    ttfd_results: list[TtfdEpisodeResult] = []
    sdr_results: list[SdrEpisodeResult] = []

    for episode in ground_truth.episodes:
        source_id = episode.source_id
        if source_id is None:
            ttfd_results.append(
                TtfdEpisodeResult(episode_id=episode.id, cause="subject_level_identity")
            )
            sdr_results.append(
                SdrEpisodeResult(episode_id=episode.id, cause="subject_level_identity")
            )
            continue

        pattern = patterns_by_condition.get(episode.condition_id)
        if pattern is None:
            ttfd_results.append(
                TtfdEpisodeResult(episode_id=episode.id, cause="condition_not_in_pattern_set")
            )
            sdr_results.append(
                SdrEpisodeResult(episode_id=episode.id, cause="condition_not_in_pattern_set")
            )
            continue

        events = events_by_source.get(source_id, [])
        if not events:
            ttfd_results.append(
                TtfdEpisodeResult(episode_id=episode.id, cause="no_detections_for_source")
            )
            sdr_results.append(
                SdrEpisodeResult(episode_id=episode.id, sdr=0.0, cause="no_detections_for_source")
            )
            continue

        flags = _flags_for(episode.condition_id, source_id, events)
        ttfd_ms, ttfd_cause = _ttfd_for_episode(events, flags, episode)
        sdr, sdr_cause = _sdr_for_episode(events, flags, episode, nominal_steps.get(source_id, 0.0))
        ttfd_results.append(TtfdEpisodeResult(episode_id=episode.id, ttfd_ms=ttfd_ms, cause=ttfd_cause))
        sdr_results.append(SdrEpisodeResult(episode_id=episode.id, sdr=sdr, cause=sdr_cause))

    ttfd_values = [r.ttfd_ms for r in ttfd_results if r.ttfd_ms is not None]
    sdr_values = [r.sdr for r in sdr_results if r.sdr is not None]

    result.update(
        avg_ttfd_ms=round(mean(ttfd_values), 6) if ttfd_values else None,
        avg_sdr=round(mean(sdr_values), 6) if sdr_values else None,
        ttfd_by_episode=ttfd_results,
        sdr_by_episode=sdr_results,
        ttfd_sdr_applicability="computed",
        positive_criterion=positive_criterion,
    )
    return result


def _is_scene_key(subject_key: str) -> bool:
    """Una clave de escena es `{pattern_id}:{source_id}`; una de sujeto agrega
    `:{track_id}` (spatial_absence.state_key)."""
    return subject_key.count(":") == 1


def _expected_key_matches(alert: AlertEvent, expected: ExpectedAlert) -> bool:
    if expected.level == "subject":
        return alert.subject_key == expected.subject_key
    # Episodio de escena: matching estructurado por source_id, inmune a que la
    # clave de estado del motor use pattern_id != condition_id (spec 41 §2.1).
    return alert.source_id == expected.source_id


def _granularity_warnings(
    ground_truth: TemporalGroundTruth, alerts: list[AlertEvent], matched: int
) -> list[str]:
    """Detecta el caso silencioso: un GT por sujeto evaluado contra alertas de
    escena (o al reves). Sin esto el resultado es F1 = 0 sin ninguna senal, y
    quien re-corra una evaluacion historica contra el motor G0 lo leeria como
    'el motor no detecta nada'."""
    if matched or not alerts or not ground_truth.expected_alerts:
        return []
    expected_levels = {expected.level for expected in ground_truth.expected_alerts}
    observed_scene = all(_is_scene_key(alert.subject_key) for alert in alerts)
    if expected_levels == {"subject"} and observed_scene:
        return [
            "granularity_mismatch: el ground truth es por sujeto pero todas las alertas "
            "son de escena (granularity: scene). Ningun match es posible. Migra el GT a "
            "clip_gt.v2 con episodios level='scene', o corre el motor con "
            "granularity: subject."
        ]
    if expected_levels == {"scene"} and not observed_scene:
        return [
            "granularity_mismatch: el ground truth es de escena pero las alertas traen "
            "clave de sujeto (granularity: subject)."
        ]
    return []


def _frame_in_window(alert: AlertEvent, expected: ExpectedAlert) -> bool:
    if alert.frame_index is None:
        return False
    max_frame = expected.max_alert_frame_index or expected.expected_alert_frame_index
    return expected.expected_alert_frame_index <= alert.frame_index <= max_frame


def _safe_div(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def evaluate_temporal_alerts(
    alerts_path: str | Path,
    ground_truth_path: str | Path,
    output_path: str | Path | None = None,
    matching_windows: dict[str, MatchingWindow] | None = None,
    detections_path: str | Path | None = None,
    patterns_path: str | Path | None = None,
) -> TemporalAlertEvaluation:
    """Compara alertas emitidas por replay contra expectativas temporales.

    Dispatcha por el tipo de ground truth ya cargado: `TemporalGroundTruth` (v1,
    frame-based, `control.eval.temporal.v1`) va a `_evaluate_v1`; `ClipGroundTruthV2`
    (v2, episodios en ms, spec 43 SS4) va a `_evaluate_v2` con matching por ventana
    (ADR-011: re_alerts, sub_threshold, aplicabilidad ADR-006).

    Las ventanas de matching del path v2 se resuelven con `_resolve_matching_windows`:
    si `matching_windows` no llega explicito del caller, se prioriza la persistencia
    minima versionada en `ground_truth.provenance.pattern_set_ms` (cuando el GT la
    trae) por sobre `DEFAULT_MATCHING_WINDOWS`, para no evaluar en silencio contra un
    umbral distinto del que derivo los episodios del GT.

    `detections_path`/`patterns_path` (opcionales, spec 43 SS10) habilitan SDR +
    TTFD sobre `detections.jsonl` (`media.detection.v1`): ver `_compute_ttfd_sdr`
    para el criterio de deteccion positiva y la definicion operativa de SDR. Sin
    `detections_path` el comportamiento es identico al previo a este fix y
    `ttfd_sdr_applicability` queda en `not_applicable:no_detections_provided`.
    """

    alerts_path = Path(alerts_path)
    ground_truth_path = Path(ground_truth_path)
    ground_truth = _load_ground_truth(ground_truth_path)
    alerts = _load_alerts(alerts_path)

    if isinstance(ground_truth, ClipGroundTruthV2):
        effective_windows = _resolve_matching_windows(ground_truth, matching_windows)
        evaluation = _evaluate_v2(
            alerts,
            ground_truth,
            alerts_path,
            ground_truth_path,
            effective_windows,
        )
    else:
        evaluation = _evaluate_v1(alerts, ground_truth, alerts_path, ground_truth_path)

    ttfd_sdr = _compute_ttfd_sdr(ground_truth, detections_path, patterns_path)
    evaluation = evaluation.model_copy(update=ttfd_sdr)

    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(evaluation.model_dump_json(indent=2) + "\n", encoding="utf-8")

    return evaluation


def _evaluate_v1(
    alerts: list[AlertEvent],
    ground_truth: TemporalGroundTruth,
    alerts_path: Path,
    ground_truth_path: Path,
) -> TemporalAlertEvaluation:
    """Evaluacion v1 (frame-based, `control.eval.temporal.v1`). Logica sin cambios
    respecto de la version previa a Task 3: solo recibe los objetos ya cargados."""

    matched_alert_ids: set[str] = set()
    matched_keys: set[tuple[str, str]] = set()
    matches: list[AlertMatch] = []
    missed: list[MissedAlert] = []

    for expected in ground_truth.expected_alerts:
        candidates = [
            alert
            for alert in alerts
            if alert.alert_id not in matched_alert_ids
            and alert.condition_id == expected.condition_id
            and _expected_key_matches(alert, expected)
            and _frame_in_window(alert, expected)
        ]
        candidates.sort(key=lambda alert: alert.frame_index if alert.frame_index is not None else 10**9)
        if not candidates:
            missed.append(
                MissedAlert(
                    expected_id=expected.id,
                    condition_id=expected.condition_id,
                    source_id=expected.source_id,
                    subject_key=expected.subject_key,
                    expected_alert_frame_index=expected.expected_alert_frame_index,
                    max_alert_frame_index=expected.max_alert_frame_index,
                )
            )
            continue

        alert = candidates[0]
        matched_alert_ids.add(alert.alert_id)
        matched_keys.add((alert.condition_id, alert.subject_key))
        latency_frames = (
            alert.frame_index - expected.first_evidence_frame_index
            if alert.frame_index is not None
            else None
        )
        latency_ms = (
            alert.timestamp_ms - expected.first_evidence_timestamp_ms
            if alert.timestamp_ms is not None and expected.first_evidence_timestamp_ms is not None
            else None
        )
        matches.append(
            AlertMatch(
                expected_id=expected.id,
                alert_id=alert.alert_id,
                condition_id=alert.condition_id,
                subject_key=alert.subject_key,
                expected_alert_frame_index=expected.expected_alert_frame_index,
                alert_frame_index=alert.frame_index,
                latency_frames_from_first_evidence=latency_frames,
                latency_ms_from_first_evidence=latency_ms,
            )
        )

    unexpected: list[UnexpectedAlert] = []
    duplicate_count = 0
    for alert in alerts:
        if alert.alert_id in matched_alert_ids:
            continue
        key = (alert.condition_id, alert.subject_key)
        if key in matched_keys:
            duplicate_count += 1
            reason = "duplicate_alert_for_matched_subject"
        else:
            reason = "no_matching_expected_alert"
        unexpected.append(
            UnexpectedAlert(
                alert_id=alert.alert_id,
                condition_id=alert.condition_id,
                subject_key=alert.subject_key,
                frame_index=alert.frame_index,
                reason=reason,
            )
        )

    matched_count = len(matches)
    observed_count = len(alerts)
    precision = _safe_div(matched_count, observed_count)
    recall = _safe_div(matched_count, len(ground_truth.expected_alerts))
    f1 = round((2 * precision * recall) / (precision + recall), 6) if precision + recall else 0.0

    frame_latencies = [
        match.latency_frames_from_first_evidence
        for match in matches
        if match.latency_frames_from_first_evidence is not None
    ]
    ms_latencies = [
        match.latency_ms_from_first_evidence
        for match in matches
        if match.latency_ms_from_first_evidence is not None
    ]

    warnings = _granularity_warnings(ground_truth, alerts, matched_count)
    for message in warnings:
        logger.warning(message)

    evaluation = TemporalAlertEvaluation(
        scenario_id=ground_truth.scenario_id,
        alerts_path=str(alerts_path),
        ground_truth_path=str(ground_truth_path),
        expected_alerts_count=len(ground_truth.expected_alerts),
        observed_alerts_count=observed_count,
        matched_alerts_count=matched_count,
        missed_alerts_count=len(missed),
        unexpected_alerts_count=len(unexpected),
        duplicate_alerts_count=duplicate_count,
        warnings=warnings,
        precision=precision,
        recall=recall,
        f1=f1,
        avg_latency_frames_from_first_evidence=(
            round(mean(frame_latencies), 6) if frame_latencies else None
        ),
        avg_latency_ms_from_first_evidence=round(mean(ms_latencies), 6) if ms_latencies else None,
        matches=matches,
        missed_alerts=missed,
        unexpected_alerts=unexpected,
        applicability_state="computed",
        applicability_cause=None,
    )

    return evaluation


def _evaluate_v2(
    alerts: list[AlertEvent],
    ground_truth: ClipGroundTruthV2,
    alerts_path: Path,
    ground_truth_path: Path,
    effective_windows: dict[str, EffectiveMatchingWindow],
) -> TemporalAlertEvaluation:
    """Evaluacion v2 a nivel episodio (spec 43 SS4, ADR-011, ADR-006).

    Aplicabilidad primero: una fuente sin timestamps (todas las alertas con
    `timestamp_ms is None`) no es evaluable temporalmente y se declara
    `not_applicable` en vez de contarse silenciosamente como missed/FP.

    Por episodio: candidatos = alertas con `condition_id` igual, misma clave
    (escena o sujeto) y dentro de la ventana de matching. El primer candidato
    por `timestamp_ms` es el match; el resto de candidatos del MISMO episodio
    son `re_alerts` (ADR-011: no son FP). Las alertas no consumidas por ningun
    episodio caen a `sub_threshold` (si estan dentro de un `sub_threshold_event`
    de su condicion) o a `unexpected` (FP verdadero) en caso contrario.

    `effective_windows` ya llega resuelto por `_resolve_matching_windows`
    (caller > `ground_truth.provenance.pattern_set_ms` > `DEFAULT_MATCHING_WINDOWS`);
    esta funcion no vuelve a consultar `DEFAULT_MATCHING_WINDOWS` por su cuenta.
    """

    warnings: list[str] = []

    if alerts and all(alert.timestamp_ms is None for alert in alerts):
        return TemporalAlertEvaluation(
            scenario_id=ground_truth.clip_id,
            alerts_path=str(alerts_path),
            ground_truth_path=str(ground_truth_path),
            expected_alerts_count=len(ground_truth.episodes),
            observed_alerts_count=len(alerts),
            matched_alerts_count=0,
            missed_alerts_count=0,
            unexpected_alerts_count=0,
            duplicate_alerts_count=0,
            re_alerts_count=0,
            sub_threshold_count=0,
            precision=0.0,
            recall=0.0,
            f1=0.0,
            applicability_state="not_applicable",
            applicability_cause="non_temporal_source",
            warnings=warnings,
            effective_matching_windows=effective_windows,
        )

    consumed_alert_ids: set[str] = set()
    matched_count = 0
    re_alerts_count = 0
    missed_count = 0
    latencies: list[float] = []

    for episode in ground_truth.episodes:
        window = effective_windows.get(episode.condition_id)
        if window is None:
            warnings.append(
                f"sin matching_window para condition_id={episode.condition_id!r} "
                f"(episodio {episode.id!r}): no se puede evaluar, se cuenta como missed."
            )
            missed_count += 1
            continue

        candidates = sorted(
            (
                alert
                for alert in alerts
                # Una alerta ya consumida (match o re_alert) por un episodio previo
                # no puede volver a matchear otro episodio: dos episodios del mismo
                # condition_id + clave con ventanas solapadas (t_alert_max_ms llega
                # a 10-20s) comparten alertas candidatas, y sin esta exclusion una
                # sola alerta infla matched_alerts_count/recall en ambos episodios.
                if alert.alert_id not in consumed_alert_ids
                and alert.condition_id == episode.condition_id
                and _episode_key_matches(alert, episode)
                and _alert_in_episode_window(alert, episode, window)
            ),
            key=lambda alert: alert.timestamp_ms,
        )
        if not candidates:
            missed_count += 1
            continue

        first = candidates[0]
        consumed_alert_ids.add(first.alert_id)
        matched_count += 1
        latencies.append(first.timestamp_ms - episode.start_ms)
        for extra in candidates[1:]:
            consumed_alert_ids.add(extra.alert_id)
            re_alerts_count += 1

    unexpected: list[UnexpectedAlert] = []
    sub_threshold_count = 0
    for alert in alerts:
        if alert.alert_id in consumed_alert_ids:
            continue
        in_sub_threshold = alert.timestamp_ms is not None and any(
            event.condition_id == alert.condition_id and event.start_ms <= alert.timestamp_ms <= event.end_ms
            for event in ground_truth.sub_threshold_events
        )
        if in_sub_threshold:
            sub_threshold_count += 1
            continue
        unexpected.append(
            UnexpectedAlert(
                alert_id=alert.alert_id,
                condition_id=alert.condition_id,
                subject_key=alert.subject_key,
                frame_index=alert.frame_index,
                reason="outside_all_episode_windows",
            )
        )

    unexpected_count = len(unexpected)
    # ADR-011: re_alerts y sub_threshold NO son FP, no entran al denominador de precision.
    precision = _safe_div(matched_count, matched_count + unexpected_count)
    recall = _safe_div(matched_count, len(ground_truth.episodes))
    f1 = round((2 * precision * recall) / (precision + recall), 6) if precision + recall else 0.0

    for message in warnings:
        logger.warning(message)

    return TemporalAlertEvaluation(
        scenario_id=ground_truth.clip_id,
        alerts_path=str(alerts_path),
        ground_truth_path=str(ground_truth_path),
        expected_alerts_count=len(ground_truth.episodes),
        observed_alerts_count=len(alerts),
        matched_alerts_count=matched_count,
        missed_alerts_count=missed_count,
        unexpected_alerts_count=unexpected_count,
        duplicate_alerts_count=0,
        re_alerts_count=re_alerts_count,
        sub_threshold_count=sub_threshold_count,
        precision=precision,
        recall=recall,
        f1=f1,
        avg_latency_ms_from_episode_start=round(mean(latencies), 6) if latencies else None,
        applicability_state="computed",
        applicability_cause=None,
        effective_matching_windows=effective_windows,
        warnings=warnings,
        unexpected_alerts=unexpected,
    )
