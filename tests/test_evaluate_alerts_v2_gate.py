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


# A2 (doc 57 §6.7): un episodio cuyo clip termina antes del borde superior de su
# ventana de matching (`duration_ms < start_ms + t_alert_max_ms`) no puede
# distinguir "no alerto nunca" de "alerta valida truncada por el corte". Si queda
# missed, se CENSURA (sale del denominador de recall) en vez de contarse como
# fallo (`metric_censored`, ADR-006). Un episodio que SI matcheo cuenta normal.
def test_a2_episodio_missed_con_clip_corto_se_censura_no_missed(tmp_path) -> None:
    gt = {
        "schema_version": "clip_gt.v2",
        "clip_id": "a2_censor",
        "duration_ms": 12000.0,
        "episodes": [
            # e1 evaluable: floor = 1000 + 10000 = 11000 <= 12000 -> NO censurado
            {"id": "e1", "condition_id": "CR-01", "level": "scene",
             "source_id": "s1", "start_ms": 1000.0, "end_ms": 11000.0},
            # e2 censurado: floor = 5000 + 10000 = 15000 > 12000; sin alerta -> censored
            {"id": "e2", "condition_id": "CR-01", "level": "scene",
             "source_id": "s1", "start_ms": 5000.0, "end_ms": 12000.0},
        ],
    }
    gt_path = tmp_path / "gt_a2.json"
    gt_path.write_text(json.dumps(gt), encoding="utf-8")

    # una sola alerta, matchea e1 (6000 in [5000, 11000]); e2 no tiene alerta
    alerts = [_mk_alert(alert_id="a1", condition_id="CR-01", subject_key="CR-01:s1",
                        source_id="s1", timestamp_ms=6000.0)]
    alerts_path = tmp_path / "alerts_a2.jsonl"
    _write_jsonl(alerts_path, alerts)

    windows = {"CR-01": MatchingWindow(persistencia_min_ms=3000.0, t_alert_max_ms=10000.0)}
    ev = evaluate_temporal_alerts(alerts_path, gt_path, matching_windows=windows)

    assert ev.matched_alerts_count == 1
    assert ev.missed_alerts_count == 0            # e2 NO cuenta como missed
    assert ev.censored_episodes_count == 1
    assert {c.episode_id for c in ev.censored_episodes} == {"e2"}
    # recall sobre evaluables: 1 / (2 - 1) = 1.0, no 1/2 = 0.5
    assert ev.recall == 1.0


def test_a2_censura_parcial_mantiene_applicability_computed(tmp_path) -> None:
    """Con >=1 episodio evaluable, recall es un numero real sobre los evaluables:
    applicability_state sigue 'computed' (no un estado nuevo que rompa a un
    consumidor que chequea == 'computed'); la censura se declara via
    censored_episodes_count + warning."""
    gt = {
        "schema_version": "clip_gt.v2", "clip_id": "a2_partial",
        "duration_ms": 12000.0,
        "episodes": [
            {"id": "e1", "condition_id": "CR-01", "level": "scene",
             "source_id": "s1", "start_ms": 1000.0, "end_ms": 11000.0},  # evaluable
            {"id": "e2", "condition_id": "CR-01", "level": "scene",
             "source_id": "s1", "start_ms": 5000.0, "end_ms": 12000.0},  # censurado
        ],
    }
    gt_path = tmp_path / "gt_partial.json"
    gt_path.write_text(json.dumps(gt), encoding="utf-8")
    alerts = [_mk_alert(alert_id="a1", condition_id="CR-01", subject_key="CR-01:s1",
                        source_id="s1", timestamp_ms=6000.0)]
    alerts_path = tmp_path / "alerts_partial.jsonl"
    _write_jsonl(alerts_path, alerts)
    windows = {"CR-01": MatchingWindow(persistencia_min_ms=3000.0, t_alert_max_ms=10000.0)}
    ev = evaluate_temporal_alerts(alerts_path, gt_path, matching_windows=windows)
    assert ev.applicability_state == "computed"
    assert ev.censored_episodes_count == 1
    assert any("metric_censored" in w for w in ev.warnings)


def test_a2a4_censura_no_depende_del_orden_del_gt(tmp_path) -> None:
    """Interaccion A2xA4: una alerta disputada por un episodio EVALUABLE y uno
    CENSURABLE (ventanas solapadas, P8). El match debe ir al evaluable (TP
    conocible) y el censurable quedar fuera del denominador — sin importar en
    que orden esten listados en el GT. Aca el censurable va PRIMERO: el greedy
    por orden de slot lo matchearia a el y dejaria al evaluable como missed
    (recall 0.5). El correcto es recall 1.0 en ambos ordenes."""
    gt = {
        "schema_version": "clip_gt.v2", "clip_id": "a2a4_order",
        "duration_ms": 12000.0,
        "episodes": [
            # CENSURABLE, listado primero: floor 5000+10000=15000 > 12000
            {"id": "e_cens", "condition_id": "CR-01", "level": "scene",
             "source_id": "s1", "start_ms": 5000.0, "end_ms": 12000.0},
            # EVALUABLE: floor 1000+10000=11000 <= 12000
            {"id": "e_ok", "condition_id": "CR-01", "level": "scene",
             "source_id": "s1", "start_ms": 1000.0, "end_ms": 11000.0},
        ],
    }
    gt_path = tmp_path / "gt_order.json"
    gt_path.write_text(json.dumps(gt), encoding="utf-8")
    # alerta en 10000: candidata de e_ok [4000,11000] y de e_cens [8000,15000]
    alerts = [_mk_alert(alert_id="a1", condition_id="CR-01", subject_key="CR-01:s1",
                        source_id="s1", timestamp_ms=10000.0)]
    alerts_path = tmp_path / "alerts_order.jsonl"
    _write_jsonl(alerts_path, alerts)
    windows = {"CR-01": MatchingWindow(persistencia_min_ms=3000.0, t_alert_max_ms=10000.0)}
    ev = evaluate_temporal_alerts(alerts_path, gt_path, matching_windows=windows)
    assert ev.matched_alerts_count == 1
    assert ev.missed_alerts_count == 0          # e_ok NO debe quedar missed
    assert ev.censored_episodes_count == 1
    assert {c.episode_id for c in ev.censored_episodes} == {"e_cens"}
    assert ev.recall == 1.0


def test_a2_todos_censurados_es_not_applicable(tmp_path) -> None:
    """Si TODOS los episodios quedan censurados, no hay recall evaluable:
    applicability_state='not_applicable' con causa, en vez de un recall=0 falso."""
    gt = {
        "schema_version": "clip_gt.v2", "clip_id": "a2_all",
        "duration_ms": 12000.0,
        "episodes": [
            {"id": "e1", "condition_id": "CR-02", "level": "scene",
             "source_id": "s1", "start_ms": 3000.0, "end_ms": 12000.0},  # floor 23000 > 12000
        ],
    }
    gt_path = tmp_path / "gt_all.json"
    gt_path.write_text(json.dumps(gt), encoding="utf-8")
    alerts_path = tmp_path / "alerts_all.jsonl"
    _write_jsonl(alerts_path, [])  # sin alertas
    windows = {"CR-02": MatchingWindow(persistencia_min_ms=7000.0, t_alert_max_ms=20000.0)}
    ev = evaluate_temporal_alerts(alerts_path, gt_path, matching_windows=windows)
    assert ev.applicability_state == "not_applicable"
    assert ev.applicability_cause == "all_episodes_metric_censored"
    assert ev.censored_episodes_count == 1
    assert ev.missed_alerts_count == 0


def test_a2_episodio_con_clip_corto_pero_con_match_no_se_censura(tmp_path) -> None:
    """La censura solo aplica a lo que seria missed: si el sistema alerto a
    tiempo (dentro del clip), es un TP legitimo aunque el clip fuese corto."""
    gt = {
        "schema_version": "clip_gt.v2",
        "clip_id": "a2_match",
        "duration_ms": 12000.0,
        "episodes": [
            # floor = 5000 + 10000 = 15000 > 12000 (elegible a censura) PERO hay match
            {"id": "e1", "condition_id": "CR-01", "level": "scene",
             "source_id": "s1", "start_ms": 5000.0, "end_ms": 12000.0},
        ],
    }
    gt_path = tmp_path / "gt_a2m.json"
    gt_path.write_text(json.dumps(gt), encoding="utf-8")

    # alerta a 10000 cae en [5000+3000, 5000+10000] = [8000, 15000] y < duration
    alerts = [_mk_alert(alert_id="a1", condition_id="CR-01", subject_key="CR-01:s1",
                        source_id="s1", timestamp_ms=10000.0)]
    alerts_path = tmp_path / "alerts_a2m.jsonl"
    _write_jsonl(alerts_path, alerts)

    windows = {"CR-01": MatchingWindow(persistencia_min_ms=3000.0, t_alert_max_ms=10000.0)}
    ev = evaluate_temporal_alerts(alerts_path, gt_path, matching_windows=windows)

    assert ev.matched_alerts_count == 1
    assert ev.censored_episodes_count == 0
    assert ev.recall == 1.0


# A3 (doc 57 §3.2 G1): FAR/hora sobre clips negativos = FP / horas observadas.
# Expone tambien observed_duration_ms para que el reporte agregue Sigma FP /
# Sigma horas entre varios clips soak (no promedio de tasas por clip).
def test_a3_far_por_hora_en_clip_negativo(tmp_path) -> None:
    gt = {
        "schema_version": "clip_gt.v2",
        "clip_id": "a3_soak",
        "duration_ms": 3_600_000.0,   # 1 hora exacta
        "negative": True,
        "episodes": [],
    }
    gt_path = tmp_path / "gt_a3.json"
    gt_path.write_text(json.dumps(gt), encoding="utf-8")

    # 2 alertas: sin episodios ni sub_threshold, ambas son FP verdaderos
    alerts = [
        _mk_alert(alert_id="fp1", condition_id="CR-01", subject_key="CR-01:s1",
                  source_id="s1", timestamp_ms=100000.0),
        _mk_alert(alert_id="fp2", condition_id="CR-02", subject_key="CR-02:s1",
                  source_id="s1", timestamp_ms=200000.0),
    ]
    alerts_path = tmp_path / "alerts_a3.jsonl"
    _write_jsonl(alerts_path, alerts)

    ev = evaluate_temporal_alerts(alerts_path, gt_path)

    assert ev.unexpected_alerts_count == 2
    assert ev.observed_duration_ms == 3_600_000.0
    assert ev.far_per_hour == 2.0    # 2 FP / 1 h


# A4 (doc 52 deuda A): dos episodios de misma condicion+clave con ventanas
# solapadas (P8, entrada/salida) y 2 alertas, cada una en AMBAS ventanas. El
# greedy le daba las dos al primer episodio (match + re_alert) y dejaba el 2do
# como missed -> recall 0.5. El matching bipartito optimo asigna una alerta a
# cada episodio -> recall 1.0, 0 re_alerts, 0 missed.
def test_a4_matching_bipartito_no_deflaciona_recall_en_p8(tmp_path) -> None:
    gt = {
        "schema_version": "clip_gt.v2",
        "clip_id": "a4_p8",
        "duration_ms": 20000.0,   # >= 15000 (floor de e2): ninguno censurado
        "episodes": [
            # e1 window [3000+4000, 3000+10000] = [7000, 13000]
            {"id": "e1", "condition_id": "CR-01", "level": "scene",
             "source_id": "s1", "start_ms": 3000.0, "end_ms": 6000.0},
            # e2 window [5000+4000, 5000+10000] = [9000, 15000]; solapa [9000,13000]
            {"id": "e2", "condition_id": "CR-01", "level": "scene",
             "source_id": "s1", "start_ms": 5000.0, "end_ms": 8000.0},
        ],
    }
    gt_path = tmp_path / "gt_a4.json"
    gt_path.write_text(json.dumps(gt), encoding="utf-8")

    # ambas alertas caen en las dos ventanas (10000, 11000 estan en [9000,13000])
    alerts = [
        _mk_alert(alert_id="a1", condition_id="CR-01", subject_key="CR-01:s1",
                  source_id="s1", timestamp_ms=10000.0),
        _mk_alert(alert_id="a2", condition_id="CR-01", subject_key="CR-01:s1",
                  source_id="s1", timestamp_ms=11000.0),
    ]
    alerts_path = tmp_path / "alerts_a4.jsonl"
    _write_jsonl(alerts_path, alerts)

    windows = {"CR-01": MatchingWindow(persistencia_min_ms=4000.0, t_alert_max_ms=10000.0)}
    ev = evaluate_temporal_alerts(alerts_path, gt_path, matching_windows=windows)

    assert ev.matched_alerts_count == 2      # greedy daba 1
    assert ev.missed_alerts_count == 0       # greedy dejaba e2 missed
    assert ev.re_alerts_count == 0           # greedy contaba 1 re_alert
    assert ev.censored_episodes_count == 0
    assert ev.recall == 1.0                  # greedy daba 0.5


def test_a3_alerta_sin_timestamp_no_infla_far_y_razon_honesta(tmp_path) -> None:
    """Review #2: una alerta sin timestamp_ms (fuente temporal anomala, doc 52
    deuda C) no cae en ninguna ventana por no tener tiempo — no es 'fuera de las
    ventanas' sino 'sin timestamp', y NO debe inflar FAR (rate sobre tiempo)."""
    gt = {"schema_version": "clip_gt.v2", "clip_id": "a3_mixed",
          "duration_ms": 3_600_000.0, "negative": True, "episodes": []}
    gt_path = tmp_path / "gt_mixed.json"
    gt_path.write_text(json.dumps(gt), encoding="utf-8")
    alerts = [
        _mk_alert(alert_id="fp_ts", condition_id="CR-01", subject_key="CR-01:s1",
                  source_id="s1", timestamp_ms=100000.0),        # FP con tiempo
        _mk_alert(alert_id="fp_none", condition_id="CR-01", subject_key="CR-01:s1",
                  source_id="s1", timestamp_ms=None),            # sin timestamp
    ]
    alerts_path = tmp_path / "alerts_mixed.jsonl"
    _write_jsonl(alerts_path, alerts)
    ev = evaluate_temporal_alerts(alerts_path, gt_path)
    assert ev.unexpected_alerts_count == 2
    reasons = {u.alert_id: u.reason for u in ev.unexpected_alerts}
    assert reasons["fp_none"] == "missing_timestamp"
    assert reasons["fp_ts"] == "outside_all_episode_windows"
    assert ev.far_per_hour == 1.0    # solo la FP con timestamp: 1 / 1 h


def test_a3_observed_duration_en_early_return_no_temporal(tmp_path) -> None:
    """Review #3: la corrida no-temporal (todas las alertas sin timestamp)
    conserva observed_duration_ms (dato real) aunque far quede None."""
    gt = {"schema_version": "clip_gt.v2", "clip_id": "a3_nt",
          "duration_ms": 1_800_000.0, "negative": True, "episodes": []}
    gt_path = tmp_path / "gt_nt.json"
    gt_path.write_text(json.dumps(gt), encoding="utf-8")
    alerts = [_mk_alert(alert_id="a", condition_id="CR-01", subject_key="CR-01:s1",
                        source_id="s1", timestamp_ms=None)]
    alerts_path = tmp_path / "alerts_nt.jsonl"
    _write_jsonl(alerts_path, alerts)
    ev = evaluate_temporal_alerts(alerts_path, gt_path)
    assert ev.applicability_state == "not_applicable"
    assert ev.applicability_cause == "non_temporal_source"
    assert ev.observed_duration_ms == 1_800_000.0
    assert ev.far_per_hour is None


def test_a3_far_none_sin_duration(tmp_path) -> None:
    gt = {"schema_version": "clip_gt.v2", "clip_id": "a3_nodur",
          "negative": True, "episodes": []}
    gt_path = tmp_path / "gt_a3n.json"
    gt_path.write_text(json.dumps(gt), encoding="utf-8")
    alerts_path = tmp_path / "alerts_a3n.jsonl"
    _write_jsonl(alerts_path, [_mk_alert(alert_id="fp1", condition_id="CR-01",
                subject_key="CR-01:s1", source_id="s1", timestamp_ms=1000.0)])
    ev = evaluate_temporal_alerts(alerts_path, gt_path)
    assert ev.far_per_hour is None
    assert ev.observed_duration_ms is None
