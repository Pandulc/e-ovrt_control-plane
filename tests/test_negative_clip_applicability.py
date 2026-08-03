"""Clip NEGATIVO (0 episodios): precision/recall/F1 no son evaluables.

Con `expected_alerts_count == 0` el denominador de recall es 0. Reportar
`recall=0.0 / f1=0.0` con `applicability_state="computed"` puntúa como FRACASO
TOTAL un clip donde la plataforma se comportó PERFECTO (no alertó sobre una
escena en cumplimiento). Con 4 de los 34 clips del banco del rodaje negativos,
promediar F1 por clip subestimaría la plataforma en el informe.

El marco de aplicabilidad ya existe para el caso gemelo
(`all_episodes_metric_censored`, A2/ADR-006): sin denominador no hay recall, y
se declara `not_applicable` con causa. Este módulo cubre la rama que faltaba —
`evaluable_episodes == 0` SIN censura.

Lo que sí sigue siendo dato real de un clip negativo, y estos tests lo fijan:
`unexpected_alerts_count` y `far_per_hour` (el control de falsos positivos).
"""

import json

from eovrt_control.contracts.alerts import AlertEvent
from eovrt_control.evaluation import evaluate_temporal_alerts
from eovrt_control.evaluation.temporal import MatchingWindow

WINDOWS = {
    "CR-01": MatchingWindow(persistencia_min_ms=4000.0, t_alert_max_ms=10000.0),
    "CR-02": MatchingWindow(persistencia_min_ms=7000.0, t_alert_max_ms=20000.0),
}


def _mk_alert(*, alert_id: str, condition_id: str = "CR-01",
              source_id: str = "neg", timestamp_ms: float) -> AlertEvent:
    return AlertEvent(
        control_run_id="r", media_run_id="m", unit_id="u", source_id=source_id,
        alert_id=alert_id, pattern_id=condition_id, condition_id=condition_id,
        subject_key=f"{condition_id}:{source_id}", severity="high", state="open",
        evidence={
            "pattern_id": condition_id,
            "condition_id": condition_id,
            "subject_key": f"{condition_id}:{source_id}",
            "subject": {"label": "person", "confidence": 0.9,
                        "bbox_xyxy": [0.0, 0.0, 10.0, 20.0]},
            "missing_class": "helmet",
            "supporting": [],
            "score": 0.9,
            "rationale": "sintetico",
        },
        timestamp_ms=timestamp_ms,
    )


def _negative_gt(tmp_path, duration_ms: float = 18400.0):
    gt = {
        "schema_version": "clip_gt.v2",
        "clip_id": "neg",
        "duration_ms": duration_ms,
        "negative": True,
        "episodes": [],
    }
    p = tmp_path / "gt_neg.json"
    p.write_text(json.dumps(gt), encoding="utf-8")
    return p


def _write_alerts(tmp_path, alerts):
    p = tmp_path / "alerts.jsonl"
    p.write_text("".join(a.model_dump_json() + "\n" for a in alerts), encoding="utf-8")
    return p


def test_negativo_sin_alertas_no_es_evaluable_para_recall(tmp_path) -> None:
    """El caso que importa: comportamiento PERFECTO no puede dar F1=0/computed."""
    ev = evaluate_temporal_alerts(
        _write_alerts(tmp_path, []), _negative_gt(tmp_path), matching_windows=WINDOWS)

    assert ev.expected_alerts_count == 0
    assert ev.observed_alerts_count == 0
    assert ev.unexpected_alerts_count == 0
    assert ev.applicability_state == "not_applicable"
    assert ev.applicability_cause == "negative_clip_no_episodes"


def test_negativo_sin_alertas_deja_far_en_cero(tmp_path) -> None:
    """FAR sí es dato real en un clip negativo: 0 FP sobre tiempo observado."""
    ev = evaluate_temporal_alerts(
        _write_alerts(tmp_path, []), _negative_gt(tmp_path), matching_windows=WINDOWS)

    assert ev.far_per_hour == 0.0
    assert ev.observed_duration_ms == 18400.0


def test_negativo_con_falso_positivo_sigue_no_evaluable_para_recall(tmp_path) -> None:
    """Sin episodios NUNCA hay denominador de recall, haya o no FP."""
    alerts = [_mk_alert(alert_id="fp1", timestamp_ms=9000.0)]
    ev = evaluate_temporal_alerts(
        _write_alerts(tmp_path, alerts), _negative_gt(tmp_path), matching_windows=WINDOWS)

    assert ev.applicability_state == "not_applicable"
    assert ev.applicability_cause == "negative_clip_no_episodes"


def test_negativo_con_falso_positivo_lo_cuenta_y_lo_pone_en_far(tmp_path) -> None:
    """El FP de un clip negativo es una falsa alarma operativa real: se cuenta."""
    alerts = [_mk_alert(alert_id="fp1", timestamp_ms=9000.0)]
    ev = evaluate_temporal_alerts(
        _write_alerts(tmp_path, alerts), _negative_gt(tmp_path), matching_windows=WINDOWS)

    assert ev.observed_alerts_count == 1
    assert ev.unexpected_alerts_count == 1
    assert ev.far_per_hour is not None and ev.far_per_hour > 0
    assert [u.reason for u in ev.unexpected_alerts] == ["outside_all_episode_windows"]


def test_negativo_avisa_por_warning_que_precision_recall_no_aplican(tmp_path) -> None:
    """El artefacto tiene que decirlo en texto: alguien lo va a leer a mano."""
    ev = evaluate_temporal_alerts(
        _write_alerts(tmp_path, []), _negative_gt(tmp_path), matching_windows=WINDOWS)

    assert any("negativo" in w.lower() for w in ev.warnings), ev.warnings


def test_clip_positivo_no_se_ve_afectado(tmp_path) -> None:
    """Regresión: con >=1 episodio evaluable el estado sigue `computed`."""
    gt = {
        "schema_version": "clip_gt.v2", "clip_id": "pos", "duration_ms": 20000.0,
        "episodes": [{"id": "e1", "condition_id": "CR-01", "level": "scene",
                      "source_id": "neg", "start_ms": 1000.0, "end_ms": 15000.0}],
    }
    gt_path = tmp_path / "gt_pos.json"
    gt_path.write_text(json.dumps(gt), encoding="utf-8")
    alerts = [_mk_alert(alert_id="m1", timestamp_ms=6000.0)]

    ev = evaluate_temporal_alerts(
        _write_alerts(tmp_path, alerts), gt_path, matching_windows=WINDOWS)

    assert ev.applicability_state == "computed"
    assert ev.applicability_cause is None
    assert ev.recall == 1.0
