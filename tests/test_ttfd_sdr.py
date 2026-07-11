"""SDR + TTFD (spec 43 SS10, audit fix): tests del criterio de deteccion
positiva reusando `evaluate_spatial_absence` del motor real, y de la
definicion operativa de SDR (tramos por evento positivo, fusion por hueco
corto <= paso nominal, recorte a `[start_ms, end_ms]`)."""

import json
from pathlib import Path
from typing import Any

import pytest

from eovrt_control.evaluation import evaluate_temporal_alerts

REPO_ROOT = Path(__file__).resolve().parents[1]
PATTERNS_PATH = REPO_ROOT / "configs" / "patterns" / "cr01_cr02_v2.yaml"

# Bbox de persona: [100, 100, 300, 500] -> ancho=200, alto=400, area=80000 px
# (>> min_subject_area_px=400 de CR-01). Region CR-01 (upper_body, y in
# [0, 0.45], x_margin=0.12): x en [124, 276], y en [100, 280].
PERSON_BBOX = [100.0, 100.0, 300.0, 500.0]
# Helmet centrado dentro de esa region -> cubre al sujeto (evento NO positivo
# para CR-01, "persona sin casco").
HELMET_BBOX = [150.0, 120.0, 200.0, 160.0]


def _detection(label: str, bbox: list[float]) -> dict[str, Any]:
    return {
        "detection_id": f"{label}-{bbox[0]}",
        "label": label,
        "prompt_id": label,
        "confidence": 0.9,
        "bbox_xyxy": bbox,
    }


def _event(
    *, unit_id: str, source_id: str, timestamp_ms: float | None, with_helmet: bool
) -> dict[str, Any]:
    detections = [_detection("person", PERSON_BBOX)]
    if with_helmet:
        detections.append(_detection("helmet", HELMET_BBOX))
    return {
        "schema_version": "media.detection.v1",
        "event_type": "detection_event",
        "run_id": "r1",
        "unit_id": unit_id,
        "source": {
            "source_id": source_id,
            "source_type": "video",
            "frame_index": None,
            "timestamp_ms": timestamp_ms,
            "width": 640,
            "height": 480,
        },
        "model": {"name": "test-model", "device": "cpu"},
        "prompts": {"prompt_set_id": "test"},
        "detections": detections,
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _gt_v2(*, source_id: str, start_ms: float, end_ms: float, level: str = "scene") -> dict:
    episode: dict[str, Any] = {
        "id": "e1",
        "condition_id": "CR-01",
        "level": level,
        "start_ms": start_ms,
        "end_ms": end_ms,
    }
    if level == "scene":
        episode["source_id"] = source_id
    else:
        episode["subject_key"] = "CR-01:w1"
        if source_id is not None:
            episode["source_id"] = source_id
    return {
        "schema_version": "clip_gt.v2",
        "clip_id": "c1",
        "duration_ms": end_ms,
        "episodes": [episode],
    }


def _write_gt(tmp_path: Path, gt: dict) -> Path:
    path = tmp_path / "gt.json"
    path.write_text(json.dumps(gt), encoding="utf-8")
    return path


def _write_empty_alerts(tmp_path: Path) -> Path:
    path = tmp_path / "alerts.jsonl"
    path.write_text("", encoding="utf-8")
    return path


def test_no_detections_path_is_not_applicable(tmp_path) -> None:
    gt_path = _write_gt(tmp_path, _gt_v2(source_id="s1", start_ms=0.0, end_ms=10000.0))
    alerts_path = _write_empty_alerts(tmp_path)

    ev = evaluate_temporal_alerts(alerts_path, gt_path)

    assert ev.ttfd_sdr_applicability == "not_applicable:no_detections_provided"
    assert ev.avg_ttfd_ms is None
    assert ev.avg_sdr is None
    assert ev.ttfd_by_episode == []
    assert ev.sdr_by_episode == []
    assert ev.positive_criterion is None
    assert ev.detections_path is None


def test_non_temporal_detections_not_applicable(tmp_path) -> None:
    gt_path = _write_gt(tmp_path, _gt_v2(source_id="s1", start_ms=0.0, end_ms=10000.0))
    alerts_path = _write_empty_alerts(tmp_path)
    detections_path = tmp_path / "detections.jsonl"
    _write_jsonl(
        detections_path,
        [_event(unit_id="u0", source_id="s1", timestamp_ms=None, with_helmet=False)],
    )

    ev = evaluate_temporal_alerts(
        alerts_path, gt_path, detections_path=detections_path, patterns_path=PATTERNS_PATH
    )

    assert ev.ttfd_sdr_applicability == "not_applicable:non_temporal_source"


def test_ttfd_computed_with_mixed_positive_negative_events(tmp_path) -> None:
    """Primeros dos eventos con casco (negativos), tercero sin casco (positivo)
    a los 2500ms -> TTFD = 2500 - start_ms(0) = 2500."""
    gt_path = _write_gt(tmp_path, _gt_v2(source_id="s1", start_ms=0.0, end_ms=10000.0))
    alerts_path = _write_empty_alerts(tmp_path)
    detections_path = tmp_path / "detections.jsonl"
    _write_jsonl(
        detections_path,
        [
            _event(unit_id="u0", source_id="s1", timestamp_ms=0.0, with_helmet=True),
            _event(unit_id="u1", source_id="s1", timestamp_ms=1000.0, with_helmet=True),
            _event(unit_id="u2", source_id="s1", timestamp_ms=2500.0, with_helmet=False),
            _event(unit_id="u3", source_id="s1", timestamp_ms=5000.0, with_helmet=False),
        ],
    )

    ev = evaluate_temporal_alerts(
        alerts_path, gt_path, detections_path=detections_path, patterns_path=PATTERNS_PATH
    )

    assert ev.ttfd_sdr_applicability == "computed"
    assert len(ev.ttfd_by_episode) == 1
    assert ev.ttfd_by_episode[0].episode_id == "e1"
    assert ev.ttfd_by_episode[0].ttfd_ms == 2500.0
    assert ev.ttfd_by_episode[0].cause is None
    assert ev.avg_ttfd_ms == 2500.0
    assert ev.positive_criterion == "spatial_absence(cr01_cr02_v2) >=1 evidencia"
    assert ev.detections_path == str(detections_path)


def test_ttfd_none_without_any_positive(tmp_path) -> None:
    gt_path = _write_gt(tmp_path, _gt_v2(source_id="s1", start_ms=0.0, end_ms=10000.0))
    alerts_path = _write_empty_alerts(tmp_path)
    detections_path = tmp_path / "detections.jsonl"
    _write_jsonl(
        detections_path,
        [
            _event(unit_id="u0", source_id="s1", timestamp_ms=0.0, with_helmet=True),
            _event(unit_id="u1", source_id="s1", timestamp_ms=5000.0, with_helmet=True),
        ],
    )

    ev = evaluate_temporal_alerts(
        alerts_path, gt_path, detections_path=detections_path, patterns_path=PATTERNS_PATH
    )

    assert ev.ttfd_by_episode[0].ttfd_ms is None
    assert ev.ttfd_by_episode[0].cause == "no_positive_detected"
    assert ev.avg_ttfd_ms is None


def test_sdr_full_coverage_is_one(tmp_path) -> None:
    """11 eventos cada 1000ms, todos positivos, cubriendo [0, 10000] -> SDR=1.0."""
    gt_path = _write_gt(tmp_path, _gt_v2(source_id="s1", start_ms=0.0, end_ms=10000.0))
    alerts_path = _write_empty_alerts(tmp_path)
    detections_path = tmp_path / "detections.jsonl"
    events = [
        _event(unit_id=f"u{i}", source_id="s1", timestamp_ms=float(i * 1000), with_helmet=False)
        for i in range(11)
    ]
    _write_jsonl(detections_path, events)

    ev = evaluate_temporal_alerts(
        alerts_path, gt_path, detections_path=detections_path, patterns_path=PATTERNS_PATH
    )

    assert ev.sdr_by_episode[0].sdr == pytest.approx(1.0)
    assert ev.avg_sdr == pytest.approx(1.0)


def test_sdr_half_coverage_is_approximately_half(tmp_path) -> None:
    """Eventos cada 1000ms de 0 a 10000ms (11 eventos); solo los primeros 5
    (0..4000) son positivos -> cobertura [0, 5000], SDR = 5000/10000 = 0.5."""
    gt_path = _write_gt(tmp_path, _gt_v2(source_id="s1", start_ms=0.0, end_ms=10000.0))
    alerts_path = _write_empty_alerts(tmp_path)
    detections_path = tmp_path / "detections.jsonl"
    events = []
    for i in range(11):
        ts = float(i * 1000)
        with_helmet = ts >= 5000.0  # positivo (sin casco) para ts < 5000
        events.append(
            _event(unit_id=f"u{i}", source_id="s1", timestamp_ms=ts, with_helmet=with_helmet)
        )
    _write_jsonl(detections_path, events)

    ev = evaluate_temporal_alerts(
        alerts_path, gt_path, detections_path=detections_path, patterns_path=PATTERNS_PATH
    )

    assert ev.sdr_by_episode[0].sdr == pytest.approx(0.5)


def test_sdr_bridges_short_gap_within_nominal_step(tmp_path) -> None:
    """Cadencia nominal=1000ms. Positivo en t=0 (cubre [0,1000)); negativo en
    t=1000; positivo en t=1900 (hueco de 900ms <= paso nominal) -> se funden
    en un solo tramo sostenido [0, end_del_segundo_positivo]."""
    gt_path = _write_gt(tmp_path, _gt_v2(source_id="s1", start_ms=0.0, end_ms=3000.0))
    alerts_path = _write_empty_alerts(tmp_path)
    detections_path = tmp_path / "detections.jsonl"
    events = [
        _event(unit_id="u0", source_id="s1", timestamp_ms=0.0, with_helmet=False),
        _event(unit_id="u1", source_id="s1", timestamp_ms=1000.0, with_helmet=True),
        _event(unit_id="u2", source_id="s1", timestamp_ms=1900.0, with_helmet=False),
        _event(unit_id="u3", source_id="s1", timestamp_ms=2900.0, with_helmet=True),
    ]
    _write_jsonl(detections_path, events)

    ev = evaluate_temporal_alerts(
        alerts_path, gt_path, detections_path=detections_path, patterns_path=PATTERNS_PATH
    )

    # Tramo fusionado [0, 2900] (el positivo en 1900 cubre hasta el proximo
    # evento en 2900) recortado a [0, 3000] -> covered = 2900, SDR = 2900/3000.
    assert ev.sdr_by_episode[0].sdr == pytest.approx(2900.0 / 3000.0)


def test_source_id_filtering_ignores_other_sources(tmp_path) -> None:
    """Eventos de una fuente distinta a la del episodio no cuentan ni para
    TTFD ni para SDR."""
    gt_path = _write_gt(tmp_path, _gt_v2(source_id="s1", start_ms=0.0, end_ms=10000.0))
    alerts_path = _write_empty_alerts(tmp_path)
    detections_path = tmp_path / "detections.jsonl"
    _write_jsonl(
        detections_path,
        [
            _event(unit_id="u0", source_id="OTHER", timestamp_ms=500.0, with_helmet=False),
            _event(unit_id="u1", source_id="OTHER", timestamp_ms=1500.0, with_helmet=False),
        ],
    )

    ev = evaluate_temporal_alerts(
        alerts_path, gt_path, detections_path=detections_path, patterns_path=PATTERNS_PATH
    )

    assert ev.ttfd_by_episode[0].ttfd_ms is None
    assert ev.ttfd_by_episode[0].cause == "no_detections_for_source"
    assert ev.sdr_by_episode[0].sdr == 0.0
    assert ev.sdr_by_episode[0].cause == "no_detections_for_source"


def test_subject_level_episode_without_source_id_is_not_resolvable(tmp_path) -> None:
    gt = _gt_v2(source_id=None, start_ms=0.0, end_ms=10000.0, level="subject")
    gt_path = _write_gt(tmp_path, gt)
    alerts_path = _write_empty_alerts(tmp_path)
    detections_path = tmp_path / "detections.jsonl"
    _write_jsonl(
        detections_path,
        [_event(unit_id="u0", source_id="s1", timestamp_ms=0.0, with_helmet=False)],
    )

    ev = evaluate_temporal_alerts(
        alerts_path, gt_path, detections_path=detections_path, patterns_path=PATTERNS_PATH
    )

    assert ev.ttfd_by_episode[0].cause == "subject_level_identity"
    assert ev.sdr_by_episode[0].cause == "subject_level_identity"


def test_subject_level_episode_with_source_id_is_resolved_by_source(tmp_path) -> None:
    gt = _gt_v2(source_id="s1", start_ms=0.0, end_ms=3000.0, level="subject")
    gt_path = _write_gt(tmp_path, gt)
    alerts_path = _write_empty_alerts(tmp_path)
    detections_path = tmp_path / "detections.jsonl"
    _write_jsonl(
        detections_path,
        [_event(unit_id="u0", source_id="s1", timestamp_ms=500.0, with_helmet=False)],
    )

    ev = evaluate_temporal_alerts(
        alerts_path, gt_path, detections_path=detections_path, patterns_path=PATTERNS_PATH
    )

    assert ev.ttfd_by_episode[0].ttfd_ms == 500.0
    assert ev.ttfd_by_episode[0].cause is None


def test_default_patterns_path_used_when_not_passed(tmp_path) -> None:
    """Sin --patterns explicito, se usa el default del repo
    (configs/patterns/cr01_cr02_v2.yaml) si existe."""
    gt_path = _write_gt(tmp_path, _gt_v2(source_id="s1", start_ms=0.0, end_ms=3000.0))
    alerts_path = _write_empty_alerts(tmp_path)
    detections_path = tmp_path / "detections.jsonl"
    _write_jsonl(
        detections_path,
        [_event(unit_id="u0", source_id="s1", timestamp_ms=0.0, with_helmet=False)],
    )

    ev = evaluate_temporal_alerts(alerts_path, gt_path, detections_path=detections_path)

    assert ev.ttfd_sdr_applicability == "computed"
    assert ev.positive_criterion == "spatial_absence(cr01_cr02_v2) >=1 evidencia"


def test_v1_ground_truth_is_not_applicable_for_ttfd_sdr(tmp_path) -> None:
    """Desvio documentado: GT v1 (frame-based, sin episodios en ms) no tiene
    `[start_ms, end_ms]` sobre el que definir TTFD/SDR."""
    from eovrt_control.evaluation.temporal import ExpectedAlert, TemporalGroundTruth

    gt = TemporalGroundTruth(
        scenario_id="v1",
        expected_alerts=[
            ExpectedAlert(
                id="e1",
                condition_id="CR-01",
                level="subject",
                subject_key="CR-01:s1:w1",
                first_evidence_frame_index=2,
                expected_alert_frame_index=4,
                max_alert_frame_index=6,
            )
        ],
    )
    gt_path = tmp_path / "gt_v1.json"
    gt_path.write_text(gt.model_dump_json(), encoding="utf-8")
    alerts_path = _write_empty_alerts(tmp_path)
    detections_path = tmp_path / "detections.jsonl"
    _write_jsonl(
        detections_path,
        [_event(unit_id="u0", source_id="s1", timestamp_ms=0.0, with_helmet=False)],
    )

    ev = evaluate_temporal_alerts(
        alerts_path, gt_path, detections_path=detections_path, patterns_path=PATTERNS_PATH
    )

    assert ev.ttfd_sdr_applicability == "not_applicable:non_v2_ground_truth"


def test_end_to_end_snippet_precision_recall_ttfd_sdr(tmp_path) -> None:
    """Snippet sintetico end-to-end pedido en la verificacion: GT minimo
    escena + alerts.jsonl + detections.jsonl fabricados -> P/R/F1 + avg_ttfd_ms
    + avg_sdr computados en una sola corrida."""
    from eovrt_control.contracts.alerts import AlertEvent

    gt_path = _write_gt(tmp_path, _gt_v2(source_id="s1", start_ms=0.0, end_ms=10000.0))

    alert = AlertEvent(
        control_run_id="r",
        media_run_id="m",
        unit_id="u",
        source_id="s1",
        alert_id="a1",
        pattern_id="CR-01",
        condition_id="CR-01",
        subject_key="CR-01:s1",
        severity="high",
        evidence={
            "pattern_id": "CR-01",
            "condition_id": "CR-01",
            "subject_key": "CR-01:s1",
            "subject": {"label": "person", "confidence": 0.9, "bbox_xyxy": [0.0, 0.0, 10.0, 10.0]},
            "missing_class": "helmet",
            "supporting": [],
            "score": 0.9,
            "rationale": "test",
        },
        timestamp_ms=5000.0,  # dentro de la ventana default CR-01 [4000, 10000]
    )
    alerts_path = tmp_path / "alerts.jsonl"
    alerts_path.write_text(alert.model_dump_json() + "\n", encoding="utf-8")

    detections_path = tmp_path / "detections.jsonl"
    _write_jsonl(
        detections_path,
        [
            _event(unit_id="u0", source_id="s1", timestamp_ms=0.0, with_helmet=True),
            _event(unit_id="u1", source_id="s1", timestamp_ms=2000.0, with_helmet=False),
            _event(unit_id="u2", source_id="s1", timestamp_ms=6000.0, with_helmet=False),
        ],
    )

    ev = evaluate_temporal_alerts(
        alerts_path, gt_path, detections_path=detections_path, patterns_path=PATTERNS_PATH
    )

    assert ev.precision == 1.0
    assert ev.recall == 1.0
    assert ev.f1 == 1.0
    assert ev.avg_ttfd_ms == 2000.0
    assert ev.avg_sdr is not None
    print(
        f"P={ev.precision} R={ev.recall} F1={ev.f1} "
        f"avg_ttfd_ms={ev.avg_ttfd_ms} avg_sdr={ev.avg_sdr}"
    )
