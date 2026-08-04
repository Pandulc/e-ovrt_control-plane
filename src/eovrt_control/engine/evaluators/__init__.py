"""Evaluadores de patrones.

`evaluate_pattern` es el punto de despacho por estrategia (spec 41 §6.2): el motor
llama esta funcion y no conoce evaluadores concretos. La estrategia vive en la config
del patron (`evidence.strategy`), no en el motor ni en un pattern set aparte.
"""

from __future__ import annotations

from eovrt_control.config import PatternDefinition
from eovrt_control.contracts.media import DetectionEvent
from eovrt_control.engine.evaluators.direct_evidence import evaluate_direct_evidence
from eovrt_control.engine.evaluators.spatial_absence import (
    PatternEvaluationResult,
    evaluate_spatial_absence,
)


def _merge_or(
    spatial: PatternEvaluationResult,
    direct: PatternEvaluationResult,
) -> PatternEvaluationResult:
    """hyb_or (spec 41 §6.2): evidencia de la unidad si CUALQUIERA la aporta.

    Una evidencia por clave de estado: si ambas estrategias disparan sobre la misma
    clave, manda la espacial (senal primaria, doc 12 §4.3) — la union no puede
    duplicar el episodio. Los sujetos observados y las causas de degradacion se unen;
    `subjects_observed` no se suma (ambas cuentan a las mismas personas).
    """
    by_key = {evidence.subject_key: evidence for evidence in direct.evidences}
    by_key.update({evidence.subject_key: evidence for evidence in spatial.evidences})
    return PatternEvaluationResult(
        evidences=list(by_key.values()),
        observed_subject_keys=spatial.observed_subject_keys | direct.observed_subject_keys,
        subjects_observed=max(spatial.subjects_observed, direct.subjects_observed),
        degradation_causes=spatial.degradation_causes | direct.degradation_causes,
        ungated_direct_hits=direct.ungated_direct_hits,
    )


def evaluate_pattern(
    event: DetectionEvent,
    pattern: PatternDefinition,
) -> PatternEvaluationResult:
    """Despacha al evaluador que la config del patron declara.

    `hyb_and` se rechaza en la validacion de config (aun no implementada), asi que
    aca no puede llegar.
    """
    strategy = pattern.evidence.strategy
    if strategy == "eind":
        return evaluate_spatial_absence(event, pattern)
    if strategy == "edir":
        return evaluate_direct_evidence(event, pattern)
    if strategy == "hyb_or":
        return _merge_or(
            evaluate_spatial_absence(event, pattern),
            evaluate_direct_evidence(event, pattern),
        )
    raise ValueError(f"Estrategia de evidencia desconocida: {strategy!r}")


__all__ = [
    "PatternEvaluationResult",
    "evaluate_direct_evidence",
    "evaluate_pattern",
    "evaluate_spatial_absence",
]
