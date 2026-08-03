"""Dos artefactos de medición que subestiman la plataforma (hallados 2026-08-03
sobre el banco del rodaje, 34 clips).

**F1 — el evaluador ignoraba `annotation.start_end_tolerance_ms` del GT.** El
borde inferior de la ventana era duro en `start_ms + persistencia_min_ms`. Pero
el modelo detecta la ausencia unos frames ANTES del límite que puso el anotador,
así que el motor confirma unos ms antes del borde: la alerta caía afuera y el
episodio se contaba `missed` **y** la alerta `unexpected` — doble castigo a una
detección correcta. Medido en el banco: desvíos de −67, −100 y −433 ms, los tres
DENTRO de la tolerancia de 500 ms que el propio GT declara. Es la familia F-DR9
(ventana incompatible con el GT ⇒ falsos `missed`).

**F2 — re-confirmación con la infracción activa contada como FP.** ADR-011: "el
evaluador cuenta `re_alerts`, no los penaliza como FP". Pero solo las detectaba
DENTRO de la ventana de matching; una segunda alerta posterior a la ventana pero
todavía dentro del episodio (violación ocurriendo) caía a
`outside_all_episode_windows`. Una alerta emitida mientras la infracción está
activa NO es una falsa alarma por definición.
"""

import json

from eovrt_control.contracts.alerts import AlertEvent
from eovrt_control.evaluation import evaluate_temporal_alerts
from eovrt_control.evaluation.temporal import MatchingWindow

WINDOWS = {"CR-01": MatchingWindow(persistencia_min_ms=4000.0, t_alert_max_ms=10000.0)}
SRC = "clip"


def _mk_alert(*, alert_id: str, timestamp_ms: float, condition_id: str = "CR-01") -> AlertEvent:
    return AlertEvent(
        control_run_id="r", media_run_id="m", unit_id="u", source_id=SRC,
        alert_id=alert_id, pattern_id=condition_id, condition_id=condition_id,
        subject_key=f"{condition_id}:{SRC}", severity="high", state="open",
        evidence={
            "pattern_id": condition_id, "condition_id": condition_id,
            "subject_key": f"{condition_id}:{SRC}",
            "subject": {"label": "person", "confidence": 0.9,
                        "bbox_xyxy": [0.0, 0.0, 10.0, 20.0]},
            "missing_class": "helmet", "supporting": [], "score": 0.9,
            "rationale": "t",
        },
        timestamp_ms=timestamp_ms,
    )


def _gt(tmp_path, *, start_ms: float, end_ms: float, duration_ms: float,
        tolerance_ms: float | None = 500.0, name: str = "gt.json"):
    annotation = {"double_annotated": False}
    if tolerance_ms is not None:
        annotation["start_end_tolerance_ms"] = tolerance_ms
    gt = {
        "schema_version": "clip_gt.v2", "clip_id": SRC, "duration_ms": duration_ms,
        "annotation": annotation,
        "episodes": [{"id": "ep1", "condition_id": "CR-01", "level": "scene",
                      "source_id": SRC, "start_ms": start_ms, "end_ms": end_ms}],
    }
    p = tmp_path / name
    p.write_text(json.dumps(gt), encoding="utf-8")
    return p


def _alerts(tmp_path, alerts, name="alerts.jsonl"):
    p = tmp_path / name
    p.write_text("".join(a.model_dump_json() + "\n" for a in alerts), encoding="utf-8")
    return p


# ---------------------------------------------------------------- F1: tolerancia

def test_alerta_67ms_antes_del_borde_matchea_dentro_de_la_tolerancia(tmp_path) -> None:
    """El caso real de a_p1_c11: ventana [7167,13167], alerta 7100, tol 500."""
    gt = _gt(tmp_path, start_ms=3167.0, end_ms=22200.0, duration_ms=25933.0)
    ap = _alerts(tmp_path, [_mk_alert(alert_id="a1", timestamp_ms=7100.0)])

    ev = evaluate_temporal_alerts(ap, gt, matching_windows=WINDOWS)

    assert ev.matched_alerts_count == 1
    assert ev.missed_alerts_count == 0
    assert ev.unexpected_alerts_count == 0
    assert ev.recall == 1.0


def test_alerta_mas_alla_de_la_tolerancia_no_matchea(tmp_path) -> None:
    """La tolerancia no puede volverse una ventana elástica: 567 ms > 500."""
    gt = _gt(tmp_path, start_ms=3167.0, end_ms=22200.0, duration_ms=25933.0)
    ap = _alerts(tmp_path, [_mk_alert(alert_id="a1", timestamp_ms=6600.0)])

    ev = evaluate_temporal_alerts(ap, gt, matching_windows=WINDOWS)

    assert ev.matched_alerts_count == 0


def test_gt_sin_tolerancia_declarada_conserva_el_borde_duro(tmp_path) -> None:
    """Retrocompatibilidad: sin el campo, no se inventa tolerancia."""
    gt = _gt(tmp_path, start_ms=3167.0, end_ms=22200.0, duration_ms=25933.0,
             tolerance_ms=None)
    ap = _alerts(tmp_path, [_mk_alert(alert_id="a1", timestamp_ms=7100.0)])

    ev = evaluate_temporal_alerts(ap, gt, matching_windows=WINDOWS)

    assert ev.matched_alerts_count == 0


# ------------------------------------------------------- F2: re_alert vs FP

def test_realerta_con_la_infraccion_activa_no_es_falso_positivo(tmp_path) -> None:
    """El caso real de a_p1_c08: match 7300, segunda alerta 14667, episodio
    activo hasta 19933 — fuera de la ventana pero DENTRO de la violación."""
    gt = _gt(tmp_path, start_ms=3000.0, end_ms=19933.0, duration_ms=47200.0)
    ap = _alerts(tmp_path, [_mk_alert(alert_id="a1", timestamp_ms=7300.0),
                            _mk_alert(alert_id="a2", timestamp_ms=14667.0)])

    ev = evaluate_temporal_alerts(ap, gt, matching_windows=WINDOWS)

    assert ev.matched_alerts_count == 1
    assert ev.re_alerts_count == 1
    assert ev.unexpected_alerts_count == 0
    assert ev.precision == 1.0


def test_alerta_despues_de_que_el_episodio_cerro_si_es_falso_positivo(tmp_path) -> None:
    """La condición ya se resolvió: alertar ahí SÍ es una falsa alarma real."""
    gt = _gt(tmp_path, start_ms=3000.0, end_ms=19933.0, duration_ms=47200.0)
    ap = _alerts(tmp_path, [_mk_alert(alert_id="a1", timestamp_ms=7300.0),
                            _mk_alert(alert_id="a2", timestamp_ms=25000.0)])

    ev = evaluate_temporal_alerts(ap, gt, matching_windows=WINDOWS)

    assert ev.matched_alerts_count == 1
    assert ev.unexpected_alerts_count == 1
    assert [u.reason for u in ev.unexpected_alerts] == ["outside_all_episode_windows"]


def test_alerta_prematura_sigue_siendo_falso_positivo(tmp_path) -> None:
    """Distinción que se me pasó al primer intento del fix F2 y que rompió dos
    gates: una alerta ANTERIOR al match no es una repetición, es *prematura* —
    el motor confirmó sin haber acumulado la persistencia. Sigue siendo FP
    aunque la infracción esté activa en ese instante.
    """
    # tolerancia 0 para aislar el efecto de F2 del de F1
    gt = _gt(tmp_path, start_ms=1000.0, end_ms=15000.0, duration_ms=20000.0,
             tolerance_ms=None)
    ap = _alerts(tmp_path, [_mk_alert(alert_id="prematura", timestamp_ms=3500.0),
                            _mk_alert(alert_id="match", timestamp_ms=6000.0)])

    ev = evaluate_temporal_alerts(ap, gt, matching_windows=WINDOWS)

    assert ev.matched_alerts_count == 1
    assert ev.unexpected_alerts_count == 1
    assert [u.alert_id for u in ev.unexpected_alerts] == ["prematura"]


def test_realerta_de_otra_condicion_no_se_absorbe(tmp_path) -> None:
    """Una alerta CR-02 dentro de un episodio CR-01 no es re_alert de ese
    episodio: no hay episodio CR-02 en el GT, así que es FP."""
    gt = _gt(tmp_path, start_ms=3000.0, end_ms=19933.0, duration_ms=47200.0)
    ap = _alerts(tmp_path, [_mk_alert(alert_id="a1", timestamp_ms=7300.0),
                            _mk_alert(alert_id="a2", timestamp_ms=14000.0,
                                      condition_id="CR-02")])

    ev = evaluate_temporal_alerts(ap, gt, matching_windows=WINDOWS)

    assert ev.matched_alerts_count == 1
    assert ev.unexpected_alerts_count == 1
