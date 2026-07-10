"""Evaluador de ausencia espacial de EPP asociado a una persona."""

from __future__ import annotations

from dataclasses import dataclass, field

from eovrt_control.config import PatternDefinition
from eovrt_control.contracts.media import Detection, DetectionEvent
from eovrt_control.contracts.pattern import EvidenceRef, PatternEvidence


LABEL_ALIASES: dict[str, set[str]] = {
    "person": {"person", "worker", "human", "people"},
    "helmet": {"helmet", "hardhat", "hard_hat", "safety helmet", "protective helmet"},
    "vest": {
        "vest",
        "reflective vest",
        "safety vest",
        "high visibility vest",
        "high-visibility vest",
    },
}


@dataclass(frozen=True)
class PatternEvaluationResult:
    evidences: list[PatternEvidence]
    observed_subject_keys: set[str]
    subjects_observed: int = 0
    degradation_causes: set[str] = field(default_factory=set)


def _normalize_label(value: str | None) -> str:
    return (value or "").strip().lower().replace("_", " ")


def _matches_detection(detection: Detection, target_class: str) -> bool:
    target = _normalize_label(target_class)
    aliases = LABEL_ALIASES.get(target, {target})
    label = _normalize_label(detection.label)
    prompt_id = _normalize_label(detection.prompt_id)
    return label in aliases or prompt_id in aliases


def _bbox_center(bbox_xyxy: list[float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox_xyxy
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _region_bbox(subject_bbox: list[float], pattern: PatternDefinition) -> list[float]:
    x1, y1, x2, y2 = subject_bbox
    width = max(0.0, x2 - x1)
    height = max(0.0, y2 - y1)
    margin_x = width * pattern.region.x_margin_ratio

    y_min_ratio = pattern.region.y_min_ratio
    y_max_ratio = pattern.region.y_max_ratio
    aspect_limit = pattern.region.full_height_aspect_ratio
    if aspect_limit is not None and height > 0.0 and (width / height) >= aspect_limit:
        # Sujeto agachado/inclinado: la banda vertical pensada para una persona
        # erguida no cubre donde queda el EPP (p. ej. chaleco arriba de la caja).
        y_min_ratio, y_max_ratio = 0.0, 1.0

    return [
        x1 + margin_x,
        y1 + height * y_min_ratio,
        x2 - margin_x,
        y1 + height * y_max_ratio,
    ]


def _center_inside_region(detection: Detection, region: list[float]) -> bool:
    cx, cy = _bbox_center(detection.bbox_xyxy)
    x1, y1, x2, y2 = region
    return x1 <= cx <= x2 and y1 <= cy <= y2


def _region_center(region: list[float]) -> tuple[float, float]:
    x1, y1, x2, y2 = region
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _sq_distance(point_a: tuple[float, float], point_b: tuple[float, float]) -> float:
    return (point_a[0] - point_b[0]) ** 2 + (point_a[1] - point_b[1]) ** 2


def _match_epp_to_subjects(
    regions: list[list[float]],
    required_items: list[Detection],
) -> set[int]:
    """Asigna cada EPP a lo sumo a una persona y devuelve los indices de sujeto cubiertos.

    Usa matching bipartito de cardinalidad maxima (caminos aumentantes) en lugar de
    greedy puro: con cajas de persona superpuestas, el greedy podia "robarle" el casco
    a su dueno real y disparar una alerta falsa aunque hubiera otro EPP disponible.
    La adyacencia se ordena por cercania al centro de la region para preferir, entre
    matchings del mismo tamano, las asociaciones espacialmente mas plausibles.
    """

    adjacency: list[list[int]] = []
    for region in regions:
        region_center = _region_center(region)
        candidates: list[tuple[float, int]] = []
        for item_index, item in enumerate(required_items):
            if not _center_inside_region(item, region):
                continue
            distance = _sq_distance(_bbox_center(item.bbox_xyxy), region_center)
            candidates.append((distance, item_index))
        candidates.sort()
        adjacency.append([item_index for _, item_index in candidates])

    item_owner: dict[int, int] = {}

    def _try_assign(subject_index: int, visited: set[int]) -> bool:
        for item_index in adjacency[subject_index]:
            if item_index in visited:
                continue
            visited.add(item_index)
            owner = item_owner.get(item_index)
            if owner is None or _try_assign(owner, visited):
                item_owner[item_index] = subject_index
                return True
        return False

    covered: set[int] = set()
    for subject_index in range(len(regions)):
        if _try_assign(subject_index, set()):
            covered.add(subject_index)
    return covered


def _evidence_ref(detection: Detection) -> EvidenceRef:
    return EvidenceRef(
        detection_id=detection.detection_id,
        label=detection.label,
        confidence=detection.confidence,
        bbox_xyxy=detection.bbox_xyxy,
    )


def state_key(pattern: PatternDefinition, source_id: str, track_id: str | None) -> str:
    """Clave de estado del motor (spec 41 §2.1).

    Bajo `scene` la identidad es la fuente. Bajo `subject` es el track_id,
    pero si `track_id` es `None` degrada a clave de escena (fallback, causa
    `no_track_id`): no hay identidad de sujeto sin `track_id`.
    `detection_id` NO se usa como identidad, nunca.
    """
    if pattern.granularity == "subject" and track_id is not None:
        return f"{pattern.id}:{source_id}:{track_id}"
    return f"{pattern.id}:{source_id}"


def evaluate_spatial_absence(
    event: DetectionEvent,
    pattern: PatternDefinition,
) -> PatternEvaluationResult:
    """Evalua si cada persona carece de una clase EPP asociada espacialmente.

    Emite evidencias por CLAVE DE ESTADO, no por persona: bajo `scene` agrega los
    sujetos en una unica evidencia ("la escena esta en evidencia si >=1 sujeto la
    aporta", spec 41 §2.1); bajo `subject` emite una por track_id.
    """

    subjects = [
        detection
        for detection in event.detections
        if _matches_detection(detection, pattern.subject_class)
        and detection.confidence >= pattern.evidence.min_subject_confidence
        and (detection.area_px or 0.0) >= pattern.evidence.min_subject_area_px
    ]
    required_items = [
        detection
        for detection in event.detections
        if _matches_detection(detection, pattern.required_absent_class)
        and detection.confidence >= pattern.evidence.min_absent_class_confidence
    ]

    regions = [_region_bbox(subject.bbox_xyxy, pattern) for subject in subjects]
    covered = _match_epp_to_subjects(regions, required_items)

    source_id = event.source.source_id
    observed_subject_keys: set[str] = set()
    # clave de estado -> sujetos descubiertos que la respaldan
    uncovered_by_key: dict[str, list[Detection]] = {}

    degradation_causes: set[str] = set()
    # Fallback por FUENTE (spec 41 §2.1), no por deteccion: si una sola persona
    # viene sin track_id, todo el evento de esta fuente se clavea a escena.
    fallback_to_scene = pattern.granularity == "subject" and any(
        subject.track_id is None for subject in subjects
    )
    if fallback_to_scene:
        degradation_causes.add("no_track_id")

    for index, subject in enumerate(subjects):
        track_id = None if fallback_to_scene else subject.track_id
        key = state_key(pattern, source_id, track_id)
        observed_subject_keys.add(key)
        if index in covered:
            continue
        uncovered_by_key.setdefault(key, []).append(subject)

    evidences: list[PatternEvidence] = []
    for key, uncovered in uncovered_by_key.items():
        # Representante: el sujeto mas confiable. Bajo `subject` hay exactamente uno.
        representative = max(uncovered, key=lambda detection: detection.confidence)
        evidences.append(
            PatternEvidence(
                pattern_id=pattern.id,
                condition_id=pattern.condition_id,
                subject_key=key,
                subject=_evidence_ref(representative),
                missing_class=pattern.required_absent_class,
                # Los demas sujetos descubiertos NO se pierden al agregar a escena:
                # la evaluacion por persona del BENCH (person_gt) junta por bbox.
                supporting=[
                    _evidence_ref(subject)
                    for subject in uncovered
                    if subject is not representative
                ],
                score=representative.confidence,
                subjects_in_evidence=len(uncovered),
                rationale=(
                    f"No se encontro evidencia '{pattern.required_absent_class}' "
                    f"en region '{pattern.region.type}' de {len(uncovered)} sujeto(s)."
                ),
            )
        )

    return PatternEvaluationResult(
        evidences=evidences,
        observed_subject_keys=observed_subject_keys,
        subjects_observed=len(subjects),
        degradation_causes=degradation_causes,
    )

