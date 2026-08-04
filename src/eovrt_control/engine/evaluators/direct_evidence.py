"""Evaluador de evidencia DIRECTA de la condicion (spec 41 §6.1, doc 12 §4.2).

La evidencia del patron es una deteccion que expresa la violacion directamente
("person without hard hat", "bare head"), no la ausencia espacial de una clase EPP.
Habilita la Fase 2 de E-DIR y la via bare_head-como-evidencia (F-84.5 del repo docs).

Gating por persona (doc 12 §4.2): una deteccion directa solo aporta evidencia si
matchea con una persona detectada — sin persona, se cuenta como `ungated_direct_hits`
(diagnostico) y no dispara nada. Esto mantiene la alerta reconstruible ("frase F
sobre la persona en bbox X") y evita que un FP suelto sostenga un episodio.

La salida es el mismo `PatternEvaluationResult` que `spatial_absence`: el motor no
distingue estrategias — toda la logica temporal (confirmacion, histeresis, expiracion)
es comun. La evidencia conserva la semantica de bbox-de-persona en subject/supporting
(aguas abajo se junta por bbox contra person_gt); la frase que disparo va en el
rationale.
"""

from __future__ import annotations

from eovrt_control.config import DirectEvidenceClassConfig, PatternDefinition
from eovrt_control.contracts.media import Detection, DetectionEvent
from eovrt_control.contracts.pattern import PatternEvidence
from eovrt_control.engine.evaluators.spatial_absence import (
    PatternEvaluationResult,
    _bbox_center,
    _evidence_ref,
    _matches_detection,
    _normalize_label,
    _region_bbox,
    state_key,
)


def _iou(a: list[float], b: list[float]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if inter == 0.0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _matching_class(
    detection: Detection, classes: list[DirectEvidenceClassConfig]
) -> DirectEvidenceClassConfig | None:
    label = _normalize_label(detection.label)
    prompt_id = _normalize_label(detection.prompt_id)
    for cls in classes:
        target = _normalize_label(cls.prompt_id)
        if label == target or prompt_id == target:
            return cls
    return None


def _gate_to_subject(
    hit: Detection,
    cls: DirectEvidenceClassConfig,
    subjects: list[Detection],
    pattern: PatternDefinition,
) -> int | None:
    """Indice de la persona que gatea el hit, o None (hit sin persona)."""
    if cls.match == "person_iou":
        best_index, best_iou = None, cls.iou_threshold
        for index, subject in enumerate(subjects):
            value = _iou(hit.bbox_xyxy, subject.bbox_xyxy)
            if value >= best_iou:
                best_index, best_iou = index, value
        return best_index
    # region_center: centro del hit dentro de la region del patron sobre la persona
    # (la misma region que usa spatial_absence — para CR-01, upper_body).
    center = _bbox_center(hit.bbox_xyxy)
    for index, subject in enumerate(subjects):
        x1, y1, x2, y2 = _region_bbox(subject.bbox_xyxy, pattern)
        if x1 <= center[0] <= x2 and y1 <= center[1] <= y2:
            return index
    return None


def evaluate_direct_evidence(
    event: DetectionEvent,
    pattern: PatternDefinition,
) -> PatternEvaluationResult:
    """Evalua evidencia directa gateada por persona, por CLAVE DE ESTADO.

    Misma semantica de agregacion que `evaluate_spatial_absence`: bajo `scene` los
    sujetos con hit gateado se agregan en una evidencia; bajo `subject` una por
    track_id, con degradacion a escena (causa `no_track_id`) si falta identidad.
    """
    subjects = [
        detection
        for detection in event.detections
        if _matches_detection(detection, pattern.subject_class)
        and detection.confidence >= pattern.evidence.min_subject_confidence
        and (detection.area_px or 0.0) >= pattern.evidence.min_subject_area_px
    ]

    classes = pattern.evidence.direct
    hits: list[tuple[Detection, DirectEvidenceClassConfig]] = []
    for detection in event.detections:
        cls = _matching_class(detection, classes)
        if cls is not None and detection.confidence >= cls.min_confidence:
            hits.append((detection, cls))

    source_id = event.source.source_id
    degradation_causes: set[str] = set()
    # Fallback por FUENTE, igual que spatial_absence: una persona sin track_id
    # degrada todo el evento a clave de escena.
    fallback_to_scene = pattern.granularity == "subject" and any(
        subject.track_id is None for subject in subjects
    )
    if fallback_to_scene:
        degradation_causes.add("no_track_id")

    observed_subject_keys: set[str] = set()
    for subject in subjects:
        track_id = None if fallback_to_scene else subject.track_id
        observed_subject_keys.add(state_key(pattern, source_id, track_id))

    ungated = 0
    # clave de estado -> (persona, mejor hit) por sujeto gateado
    hit_by_subject: dict[int, tuple[Detection, Detection]] = {}
    for hit, cls in hits:
        subject_index = _gate_to_subject(hit, cls, subjects, pattern)
        if subject_index is None:
            ungated += 1
            continue
        actual = hit_by_subject.get(subject_index)
        if actual is None or hit.confidence > actual[1].confidence:
            hit_by_subject[subject_index] = (subjects[subject_index], hit)

    by_key: dict[str, list[tuple[Detection, Detection]]] = {}
    for subject_index, (subject, hit) in hit_by_subject.items():
        track_id = None if fallback_to_scene else subject.track_id
        key = state_key(pattern, source_id, track_id)
        by_key.setdefault(key, []).append((subject, hit))

    evidences: list[PatternEvidence] = []
    for key, pares in by_key.items():
        # Representante: la persona cuyo hit directo es el mas confiable.
        pares.sort(key=lambda par: -par[1].confidence)
        subject, hit = pares[0]
        evidences.append(
            PatternEvidence(
                pattern_id=pattern.id,
                condition_id=pattern.condition_id,
                subject_key=key,
                subject=_evidence_ref(subject),
                missing_class=pattern.required_absent_class,
                supporting=[_evidence_ref(otro) for otro, _ in pares[1:]],
                score=hit.confidence,
                subjects_in_evidence=len(pares),
                rationale=(
                    f"Evidencia directa '{hit.prompt_id or hit.label}' "
                    f"(conf {hit.confidence:.2f}) gateada a {len(pares)} sujeto(s)."
                ),
            )
        )

    return PatternEvaluationResult(
        evidences=evidences,
        observed_subject_keys=observed_subject_keys,
        subjects_observed=len(subjects),
        degradation_causes=degradation_causes,
        ungated_direct_hits=ungated,
    )
