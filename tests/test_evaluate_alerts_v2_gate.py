"""Gate de fase (spec 41 SS8 item 7 / SS10, gate 7): fixture v1 sigue en verde
+ fixture v2 escena-condicion en verde. Verificado significativo por mutacion
(ver Step 3 de la tarea, reportado en task-5-report.md).

Corre AMBOS caminos en un solo modulo:
  (a) v1 (`TemporalGroundTruth`, frame-based, `control.eval.temporal.v1`): el
      path historico no debe romperse.
  (b) v2 (`ClipGroundTruthV2`, `clip_gt.v2`, episodios en ms): recall 1.0,
      re_alerts distinguidos de FP (ADR-011), FP fuera de toda ventana.

La fixture v2 incluye deliberadamente una alerta justo ANTES del borde
inferior de la ventana (`persistencia_min_ms`): si `_alert_in_episode_window`
pierde el chequeo del borde inferior esa alerta pasa a ser candidata, se
vuelve "primer match" del episodio y desplaza al match real a re_alert,
cambiando `re_alerts_count`/`unexpected_alerts_count` (mutacion 2 del Step 3).
"""

import json

from eovrt_control.contracts.alerts import AlertEvent
from eovrt_control.evaluation import evaluate_temporal_alerts
from eovrt_control.evaluation.temporal import ExpectedAlert, MatchingWindow, TemporalGroundTruth


def _mk_alert(
    *,
    alert_id: str,
    condition_id: str,
    subject_key: str,
    source_id: str = "s1",
    frame_index: int | None = None,
    timestamp_ms: float | None = None,
) -> AlertEvent:
    """Construye un AlertEvent valido (evidence real de PatternEvidence, no un
    placeholder), igual al helper de test_temporal_evaluation.py pero con
    frame_index opcional para poder ejercitar tambien el path v1."""
    return AlertEvent(
        control_run_id="r",
        media_run_id="m",
        unit_id="u",
        source_id=source_id,
        alert_id=alert_id,
        pattern_id=condition_id,
        condition_id=condition_id,
        subject_key=subject_key,
        severity="high",
        evidence={
            "pattern_id": condition_id,
            "condition_id": condition_id,
            "subject_key": subject_key,
            "subject": {"label": "person", "confidence": 0.9, "bbox_xyxy": [0.0, 0.0, 10.0, 10.0]},
            "missing_class": "helmet",
            "supporting": [],
            "score": 0.9,
            "rationale": "test",
        },
        frame_index=frame_index,
        timestamp_ms=timestamp_ms,
    )


def _write_jsonl(path, alerts: list[AlertEvent]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for alert in alerts:
            fh.write(alert.model_dump_json() + "\n")


def test_gate_v1_ground_truth_still_evaluates_with_f1_one(tmp_path) -> None:
    """(a) Path v1 (frame-based, control.eval.temporal.v1) intacto: una
    ExpectedAlert por sujeto matcheada por una unica AlertEvent en su ventana
    de frames da F1 = 1.0, sin missed ni unexpected."""
    gt = TemporalGroundTruth(
        scenario_id="gate_v1",
        expected_alerts=[
            ExpectedAlert(
                id="e1",
                condition_id="CR-01",
                level="subject",
                subject_key="CR-01:s1:w1",
                first_evidence_frame_index=2,
                expected_alert_frame_index=4,
                max_alert_frame_index=6,
                first_evidence_timestamp_ms=1000.0,
            )
        ],
    )
    gt_path = tmp_path / "gt_v1.json"
    gt_path.write_text(gt.model_dump_json(), encoding="utf-8")

    alert = _mk_alert(
        alert_id="a1",
        condition_id="CR-01",
        subject_key="CR-01:s1:w1",
        frame_index=5,
        timestamp_ms=2500.0,
    )
    alerts_path = tmp_path / "alerts_v1.jsonl"
    _write_jsonl(alerts_path, [alert])

    evaluation = evaluate_temporal_alerts(alerts_path, gt_path)

    assert evaluation.schema_version == "control.eval.temporal.v1"
    assert evaluation.matched_alerts_count == 1
    assert evaluation.missed_alerts_count == 0
    assert evaluation.unexpected_alerts_count == 0
    assert evaluation.precision == 1.0
    assert evaluation.recall == 1.0
    assert evaluation.f1 == 1.0
    assert evaluation.warnings == []


def test_gate_v2_scene_condition_episode_recall_and_re_alerts(tmp_path) -> None:
    """(b) Path v2 escena-condicion (clip_gt.v2, ms): un episodio con 4
    alertas candidatas alrededor de su ventana:
      - "early" (3500ms) cae ANTES del borde inferior [4000, 11000] -> no debe
        matchear el episodio; termina siendo FP (fuera de toda ventana).
      - "match" (5000ms) es el primer candidato real -> matched.
      - "extra" (6000ms) cae en la MISMA ventana, ya con el episodio
        consumido -> re_alert, NO FP (ADR-011).
      - "late_fp" (18000ms) cae fuera de toda ventana -> FP.
    Con esto: recall = 1.0 (1/1 episodios), re_alerts_count = 1,
    unexpected_alerts_count = 2 (early + late_fp), missed = 0.
    """
    gt = {
        "schema_version": "clip_gt.v2",
        "clip_id": "gate_v2",
        "duration_ms": 20000.0,
        "episodes": [
            {
                "id": "e1",
                "condition_id": "CR-01",
                "level": "scene",
                "source_id": "s1",
                "start_ms": 1000.0,  # ventana matching: [1000+3000, 1000+10000] = [4000, 11000]
                "end_ms": 15000.0,
            }
        ],
    }
    gt_path = tmp_path / "gt_v2.json"
    gt_path.write_text(json.dumps(gt), encoding="utf-8")

    alerts = [
        _mk_alert(
            alert_id="a_early", condition_id="CR-01", subject_key="CR-01:s1",
            source_id="s1", timestamp_ms=3500.0,
        ),  # justo antes del borde inferior (4000) -> no debe matchear -> FP
        _mk_alert(
            alert_id="a_match", condition_id="CR-01", subject_key="CR-01:s1",
            source_id="s1", timestamp_ms=5000.0,
        ),  # dentro de [4000, 11000] -> match del episodio
        _mk_alert(
            alert_id="a_extra", condition_id="CR-01", subject_key="CR-01:s1",
            source_id="s1", timestamp_ms=6000.0,
        ),  # segunda alerta del mismo episodio -> re_alert (ADR-011), no FP
        _mk_alert(
            alert_id="a_late_fp", condition_id="CR-01", subject_key="CR-01:s1",
            source_id="s1", timestamp_ms=18000.0,
        ),  # fuera de toda ventana -> FP
    ]
    alerts_path = tmp_path / "alerts_v2.jsonl"
    _write_jsonl(alerts_path, alerts)

    windows = {"CR-01": MatchingWindow(persistencia_min_ms=3000.0, t_alert_max_ms=10000.0)}
    evaluation = evaluate_temporal_alerts(alerts_path, gt_path, matching_windows=windows)

    assert evaluation.applicability_state == "computed"
    assert evaluation.observed_alerts_count == 4
    assert evaluation.matched_alerts_count == 1
    assert evaluation.missed_alerts_count == 0
    assert evaluation.recall == 1.0
    assert evaluation.re_alerts_count == 1
    assert evaluation.unexpected_alerts_count == 2
    assert evaluation.sub_threshold_count == 0
    matched_ids = {u.alert_id for u in evaluation.unexpected_alerts}
    assert matched_ids == {"a_early", "a_late_fp"}
