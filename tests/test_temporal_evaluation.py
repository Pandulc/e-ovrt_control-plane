import json
from pathlib import Path

import pytest
import yaml

from eovrt_control.contracts.alerts import AlertEvent
from eovrt_control.evaluation import evaluate_temporal_alerts
from eovrt_control.evaluation.temporal import TemporalGroundTruth
from eovrt_control.runtime.replay import run_replay

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENE_FIXTURE = REPO_ROOT / "fixtures/simulated_media/cr01_cr02_temporal"
TRACKED_FIXTURE = REPO_ROOT / "fixtures/simulated_media/cr01_cr02_temporal_tracked"
PATTERNS = REPO_ROOT / "configs/patterns/cr01_cr02_temporal_eval.yaml"


def _write_replay_config(tmp_path, run_id, fixture_dir, patterns_path):
    config_path = tmp_path / f"{run_id}.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "run": {"id": run_id, "scenario": "DBE", "name": run_id},
                "input": {
                    "type": "media_jsonl",
                    "path": str(fixture_dir / "detections.jsonl"),
                },
                "patterns": {
                    "file": str(patterns_path),
                    "active_ids": ["CR-01", "CR-02"],
                },
                "outputs": {"base_dir": str(tmp_path / "runs")},
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _mk_alert(
    *,
    alert_id: str,
    condition_id: str,
    subject_key: str,
    source_id: str = "s1",
    timestamp_ms: float | None,
) -> AlertEvent:
    """Construye un AlertEvent valido (evidence real de PatternEvidence, no un
    placeholder). Task 3 reutiliza este mismo patron para sus fixtures."""
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
        timestamp_ms=timestamp_ms,
    )


def _scene_config(tmp_path):
    # El pattern set de eval usa granularidad por defecto (scene): la escena
    # se clavea por (pattern_id, source_id), sin identidad de sujeto.
    return _write_replay_config(tmp_path, "scene-eval", SCENE_FIXTURE, PATTERNS)


def _subject_patterns(tmp_path):
    raw = yaml.safe_load(PATTERNS.read_text(encoding="utf-8"))
    for pattern in raw["pattern_set"]["patterns"]:
        pattern["granularity"] = "subject"
    path = tmp_path / "subject_patterns.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def _subject_config(tmp_path):
    # Granularidad subject sobre el fixture tracked (con track_id): no degrada.
    return _write_replay_config(
        tmp_path, "subject-eval", TRACKED_FIXTURE, _subject_patterns(tmp_path)
    )


def test_scene_granularity_matches_expected_alerts_with_f1_one(tmp_path) -> None:
    """El GT v2 (episodios en ms) detecta ambos episodios escena-condicion.
    Alertas reales del replay verificadas por separado: CR-01 en frame 4
    (2000 ms), CR-02 en frame 5 (2500 ms). Los episodios del fixture arrancan
    en start_ms=1000.0; la ventana [1500, 4000] contiene a ambas."""
    from eovrt_control.evaluation.temporal import MatchingWindow

    summary = run_replay(_scene_config(tmp_path))
    windows = {
        "CR-01": MatchingWindow(persistencia_min_ms=500.0, t_alert_max_ms=3000.0),
        "CR-02": MatchingWindow(persistencia_min_ms=500.0, t_alert_max_ms=3000.0),
    }
    evaluation = evaluate_temporal_alerts(
        alerts_path=summary.output_files["alerts"],
        ground_truth_path=str(SCENE_FIXTURE / "ground_truth_v2.json"),
        matching_windows=windows,
    )
    # Exactamente los dos episodios persistentes detectados; nada espurio del
    # transitorio (worker_b no genera episodio propio).
    assert evaluation.observed_alerts_count == 2
    assert evaluation.matched_alerts_count == 2
    assert evaluation.missed_alerts_count == 0
    assert evaluation.unexpected_alerts_count == 0
    assert evaluation.precision == 1.0
    assert evaluation.recall == 1.0
    assert evaluation.f1 == 1.0
    assert evaluation.warnings == []
    assert evaluation.applicability_state == "computed"


def test_subject_granularity_matches_expected_alerts_with_f1_one(tmp_path) -> None:
    """Idem a nivel subject (fixture tracked). Alertas reales del replay: worker_a
    (CR-01) en frame 5 (2500 ms), worker_c (CR-02) en frame 7 (3500 ms). Los
    episodios arrancan en start_ms=1500.0 y 2500.0 respectivamente; la ventana
    [start_ms+500, start_ms+3000] contiene a ambas."""
    from eovrt_control.evaluation.temporal import MatchingWindow

    summary = run_replay(_subject_config(tmp_path))
    # Patron subject sobre fixture tracked: no debe degradar a escena.
    assert summary.degraded is False
    assert "no_track_id" not in summary.degradation_causes
    windows = {
        "CR-01": MatchingWindow(persistencia_min_ms=500.0, t_alert_max_ms=3000.0),
        "CR-02": MatchingWindow(persistencia_min_ms=500.0, t_alert_max_ms=3000.0),
    }
    evaluation = evaluate_temporal_alerts(
        alerts_path=summary.output_files["alerts"],
        ground_truth_path=str(TRACKED_FIXTURE / "ground_truth_v2.json"),
        matching_windows=windows,
    )
    assert evaluation.observed_alerts_count == 2
    assert evaluation.matched_alerts_count == 2
    assert evaluation.missed_alerts_count == 0
    assert evaluation.unexpected_alerts_count == 0
    assert evaluation.precision == 1.0
    assert evaluation.recall == 1.0
    assert evaluation.f1 == 1.0
    assert evaluation.applicability_state == "computed"


def test_v1_ground_truth_still_evaluates(tmp_path) -> None:
    """Gate del item 7 del orden de trabajo: v1 no se rompe."""
    gt = TemporalGroundTruth.model_validate_json(
        (SCENE_FIXTURE / "ground_truth.json").read_text()
    )
    assert gt.scenario_id == "cr01_cr02_temporal"
    # Los GT v1 siempre traen subject_key concreto (contrato aditivo).
    assert all(expected.subject_key is not None for expected in gt.expected_alerts)


def test_v1_scene_ground_truth_still_matches_scene_alerts(tmp_path) -> None:
    """El evaluador sigue aceptando el schema v1 (control.eval.temporal.v1)."""
    summary = run_replay(_scene_config(tmp_path))
    v1_gt = {
        "scenario_id": "cr01_cr02_temporal",
        "expected_alerts": [
            {
                "id": "scene_cr01",
                "condition_id": "CR-01",
                "subject_key": "CR-01:sim-risk-stream",
                "first_evidence_frame_index": 2,
                "expected_alert_frame_index": 4,
                "max_alert_frame_index": 4,
                "first_evidence_timestamp_ms": 1000.0,
            }
        ],
    }
    gt_path = tmp_path / "v1_gt.json"
    gt_path.write_text(json.dumps(v1_gt), encoding="utf-8")
    evaluation = evaluate_temporal_alerts(
        alerts_path=summary.output_files["alerts"],
        ground_truth_path=str(gt_path),
    )
    assert evaluation.matched_alerts_count == 1
    assert evaluation.recall == 1.0


def test_temporal_evaluation_reports_unexpected_alert(tmp_path) -> None:
    alerts_path = tmp_path / "alerts.jsonl"
    ground_truth_path = tmp_path / "ground_truth.json"

    alerts_path.write_text(
        json.dumps(
            {
                "control_run_id": "control-run",
                "media_run_id": "media-run",
                "unit_id": "frame_0001",
                "source_id": "sim-risk-stream",
                "alert_id": "unexpected-alert",
                "pattern_id": "CR-01",
                "condition_id": "CR-01",
                "subject_key": "CR-01:sim-risk-stream:unknown_worker",
                "severity": "medium",
                "frame_index": 1,
                "timestamp_ms": 500.0,
                "evidence": {
                    "pattern_id": "CR-01",
                    "condition_id": "CR-01",
                    "subject_key": "CR-01:sim-risk-stream:unknown_worker",
                    "subject": {
                        "detection_id": "unknown_worker",
                        "label": "person",
                        "confidence": 0.9,
                        "bbox_xyxy": [0, 0, 10, 10],
                    },
                    "missing_class": "helmet",
                    "supporting": [],
                    "score": 0.9,
                    "rationale": "synthetic",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    ground_truth_path.write_text(
        json.dumps({"scenario_id": "empty", "expected_alerts": []}),
        encoding="utf-8",
    )

    evaluation = evaluate_temporal_alerts(alerts_path, ground_truth_path)

    assert evaluation.observed_alerts_count == 1
    assert evaluation.expected_alerts_count == 0
    assert evaluation.matched_alerts_count == 0
    assert evaluation.unexpected_alerts_count == 1
    assert evaluation.precision == 0.0
    assert evaluation.recall == 0.0


def test_clip_episode_v2_subject_level_requires_subject_key() -> None:
    """Un episodio level='subject' sin subject_key caeria a matching por escena
    y matchearia la alerta de OTRO sujeto de la misma fuente (F1 inflado)."""
    from eovrt_control.evaluation.temporal import ClipEpisodeV2

    with pytest.raises(ValueError):
        ClipEpisodeV2.model_validate(
            {
                "id": "ep",
                "condition_id": "CR-01",
                "level": "subject",
                "source_id": "cam-1",
                "start_ms": 0.0,
                "end_ms": 2000.0,
            }
        )


def test_clip_episode_v2_scene_level_requires_source_id() -> None:
    """Sin source_id el matching de escena compara contra None y nunca matchea:
    100% missed silencioso en vez de un error de validacion."""
    from eovrt_control.evaluation.temporal import ClipEpisodeV2

    with pytest.raises(ValueError):
        ClipEpisodeV2.model_validate(
            {
                "id": "ep",
                "condition_id": "CR-01",
                "level": "scene",
                "start_ms": 0.0,
                "end_ms": 2000.0,
            }
        )


def test_clip_episode_v2_end_ms_before_start_ms_fails() -> None:
    """Gap de cobertura de Task 1: `end_ms < start_ms` debe fallar la validacion
    en vez de producir una ventana de matching invertida silenciosa."""
    from eovrt_control.evaluation.temporal import ClipEpisodeV2

    with pytest.raises(ValueError):
        ClipEpisodeV2.model_validate(
            {
                "id": "ep",
                "condition_id": "CR-01",
                "level": "scene",
                "source_id": "cam-1",
                "start_ms": 5000.0,
                "end_ms": 1000.0,
            }
        )


def test_legacy_subject_ground_truth_against_scene_alerts_warns_loudly(tmp_path) -> None:
    """Un ground_truth.json v1 historico (claves por persona) evaluado contra el
    motor G0 bajo escena da F1=0. Sin senal, quien re-corra una evaluacion vieja
    lo leeria como 'el motor no detecta nada'."""
    summary = run_replay(_scene_config(tmp_path))
    evaluation = evaluate_temporal_alerts(
        alerts_path=summary.output_files["alerts"],
        ground_truth_path=str(SCENE_FIXTURE / "ground_truth.json"),
    )

    assert evaluation.f1 == 0.0
    assert evaluation.matched_alerts_count == 0
    assert evaluation.warnings, "el desajuste de granularidad debe declararse"
    assert "granularity_mismatch" in evaluation.warnings[0]


def test_clip_gt_v2_ms_schema_parses_and_validates():
    from eovrt_control.evaluation.temporal import ClipGroundTruthV2

    gt = ClipGroundTruthV2.model_validate(
        {
            "schema_version": "clip_gt.v2",
            "clip_id": "c1",
            "duration_ms": 20000.0,
            "negative": False,
            "episodes": [
                {
                    "id": "e1",
                    "condition_id": "CR-01",
                    "level": "scene",
                    "source_id": "s1",
                    "start_ms": 4000.0,
                    "end_ms": 12000.0,
                },
            ],
            "sub_threshold_events": [
                {
                    "condition_id": "CR-02",
                    "start_ms": 1000.0,
                    "end_ms": 1500.0,
                    "reason": "transitorio",
                },
            ],
        }
    )
    assert gt.episodes[0].start_ms == 4000.0 and gt.episodes[0].end_ms == 12000.0
    assert gt.sub_threshold_events[0].reason == "transitorio"


def test_clip_gt_v2_episode_outside_duration_fails():
    from eovrt_control.evaluation.temporal import ClipGroundTruthV2

    with pytest.raises(ValueError):
        ClipGroundTruthV2.model_validate(
            {
                "schema_version": "clip_gt.v2",
                "clip_id": "c1",
                "duration_ms": 5000.0,
                "episodes": [
                    {
                        "id": "e1",
                        "condition_id": "CR-01",
                        "level": "scene",
                        "source_id": "s1",
                        "start_ms": 4000.0,
                        "end_ms": 9000.0,
                    }
                ],
            }
        )


def test_clip_gt_v2_negative_with_episodes_fails():
    from eovrt_control.evaluation.temporal import ClipGroundTruthV2

    with pytest.raises(ValueError):
        ClipGroundTruthV2.model_validate(
            {
                "schema_version": "clip_gt.v2",
                "clip_id": "c1",
                "negative": True,
                "episodes": [
                    {
                        "id": "e1",
                        "condition_id": "CR-01",
                        "level": "scene",
                        "source_id": "s1",
                        "start_ms": 1000.0,
                        "end_ms": 2000.0,
                    }
                ],
            }
        )


def test_clip_gt_v2_scene_episode_requires_source_id():
    from eovrt_control.evaluation.temporal import ClipEpisodeV2

    with pytest.raises(ValueError):
        ClipEpisodeV2.model_validate(
            {
                "id": "e1",
                "condition_id": "CR-01",
                "level": "scene",
                "start_ms": 1.0,
                "end_ms": 2.0,
            }
        )


def test_matching_window_default_bands():
    from eovrt_control.evaluation.temporal import DEFAULT_MATCHING_WINDOWS

    assert DEFAULT_MATCHING_WINDOWS["CR-01"].persistencia_min_ms == 3000.0
    assert DEFAULT_MATCHING_WINDOWS["CR-01"].t_alert_max_ms == 10000.0
    assert DEFAULT_MATCHING_WINDOWS["CR-02"].persistencia_min_ms == 5000.0
    assert DEFAULT_MATCHING_WINDOWS["CR-02"].t_alert_max_ms == 20000.0


def test_alert_in_episode_window_ms():
    from eovrt_control.evaluation.temporal import (
        ClipEpisodeV2,
        MatchingWindow,
        _alert_in_episode_window,
    )

    ep = ClipEpisodeV2(
        id="e", condition_id="CR-01", level="scene", source_id="s1",
        start_ms=1000.0, end_ms=8000.0,
    )
    w = MatchingWindow(persistencia_min_ms=3000.0, t_alert_max_ms=10000.0)  # [4000, 11000]

    def _a(ts):
        return _mk_alert(
            alert_id="a", condition_id="CR-01", subject_key="CR-01:s1",
            source_id="s1", timestamp_ms=ts,
        )

    assert _alert_in_episode_window(_a(4000.0), ep, w) is True  # borde inferior
    assert _alert_in_episode_window(_a(11000.0), ep, w) is True  # borde superior
    assert _alert_in_episode_window(_a(3999.0), ep, w) is False  # antes de persistencia
    assert _alert_in_episode_window(_a(11001.0), ep, w) is False  # demasiado tarde
    assert _alert_in_episode_window(_a(None), ep, w) is False  # sin timestamp


def test_v2_episode_detected_extra_alert_is_re_alert(tmp_path) -> None:
    """La primera alerta dentro de la ventana del episodio matchea; una segunda
    alerta dentro de la MISMA ventana no es un FP: es un re_alert (ADR-011)."""
    from eovrt_control.evaluation.temporal import MatchingWindow

    gt = {
        "schema_version": "clip_gt.v2",
        "clip_id": "c1",
        "duration_ms": 20000.0,
        "episodes": [
            {
                "id": "e1",
                "condition_id": "CR-01",
                "level": "scene",
                "source_id": "s1",
                "start_ms": 1000.0,
                "end_ms": 12000.0,
            }
        ],
    }
    (tmp_path / "gt.json").write_text(json.dumps(gt), encoding="utf-8")
    alerts = [
        _mk_alert(
            alert_id="a1", condition_id="CR-01", subject_key="CR-01:s1",
            source_id="s1", timestamp_ms=5000.0,
        ),  # dentro de ventana [4000, 11000] -> match
        _mk_alert(
            alert_id="a2", condition_id="CR-01", subject_key="CR-01:s1",
            source_id="s1", timestamp_ms=6000.0,
        ),  # extra en el mismo episodio -> re_alert
    ]
    with (tmp_path / "alerts.jsonl").open("w", encoding="utf-8") as fh:
        for alert in alerts:
            fh.write(alert.model_dump_json() + "\n")
    windows = {"CR-01": MatchingWindow(persistencia_min_ms=3000.0, t_alert_max_ms=10000.0)}

    ev = evaluate_temporal_alerts(
        tmp_path / "alerts.jsonl", tmp_path / "gt.json", matching_windows=windows
    )

    assert ev.matched_alerts_count == 1  # 1 episodio detectado
    assert ev.re_alerts_count == 1  # la extra NO es FP (ADR-011)
    assert ev.unexpected_alerts_count == 0
    assert ev.missed_alerts_count == 0
    assert ev.applicability_state == "computed"


def test_v2_alert_outside_all_episodes_is_fp(tmp_path) -> None:
    from eovrt_control.evaluation.temporal import MatchingWindow

    gt = {
        "schema_version": "clip_gt.v2",
        "clip_id": "c1",
        "duration_ms": 20000.0,
        "episodes": [
            {
                "id": "e1",
                "condition_id": "CR-01",
                "level": "scene",
                "source_id": "s1",
                "start_ms": 1000.0,
                "end_ms": 12000.0,
            }
        ],
    }
    (tmp_path / "gt.json").write_text(json.dumps(gt), encoding="utf-8")
    with (tmp_path / "alerts.jsonl").open("w", encoding="utf-8") as fh:
        fh.write(
            _mk_alert(
                alert_id="a1", condition_id="CR-01", subject_key="CR-01:s1",
                source_id="s1", timestamp_ms=5000.0,
            ).model_dump_json()
            + "\n"
        )  # match
        fh.write(
            _mk_alert(
                alert_id="a2", condition_id="CR-01", subject_key="CR-01:s1",
                source_id="s1", timestamp_ms=18000.0,
            ).model_dump_json()
            + "\n"
        )  # fuera de toda ventana -> FP
    windows = {"CR-01": MatchingWindow(persistencia_min_ms=3000.0, t_alert_max_ms=10000.0)}

    ev = evaluate_temporal_alerts(
        tmp_path / "alerts.jsonl", tmp_path / "gt.json", matching_windows=windows
    )

    assert ev.matched_alerts_count == 1
    assert ev.unexpected_alerts_count == 1
    assert ev.re_alerts_count == 0


def test_v2_alert_in_sub_threshold_is_not_fp(tmp_path) -> None:
    from eovrt_control.evaluation.temporal import MatchingWindow

    gt = {
        "schema_version": "clip_gt.v2",
        "clip_id": "c1",
        "duration_ms": 20000.0,
        "episodes": [],  # sin episodios alertables
        "negative": False,
        "sub_threshold_events": [
            {
                "condition_id": "CR-01",
                "start_ms": 4000.0,
                "end_ms": 7000.0,
                "reason": "transitorio",
            }
        ],
    }
    (tmp_path / "gt.json").write_text(json.dumps(gt), encoding="utf-8")
    with (tmp_path / "alerts.jsonl").open("w", encoding="utf-8") as fh:
        fh.write(
            _mk_alert(
                alert_id="a1", condition_id="CR-01", subject_key="CR-01:s1",
                source_id="s1", timestamp_ms=5000.0,
            ).model_dump_json()
            + "\n"
        )  # cae en el sub_threshold_event

    ev = evaluate_temporal_alerts(
        tmp_path / "alerts.jsonl",
        tmp_path / "gt.json",
        matching_windows={
            "CR-01": MatchingWindow(persistencia_min_ms=3000.0, t_alert_max_ms=10000.0)
        },
    )

    assert ev.sub_threshold_count == 1
    assert ev.unexpected_alerts_count == 0  # sub-threshold NO es FP verdadero


def test_v2_non_temporal_alerts_are_not_applicable(tmp_path) -> None:
    gt = {
        "schema_version": "clip_gt.v2",
        "clip_id": "c1",
        "duration_ms": 20000.0,
        "episodes": [
            {
                "id": "e1",
                "condition_id": "CR-01",
                "level": "scene",
                "source_id": "s1",
                "start_ms": 1000.0,
                "end_ms": 12000.0,
            }
        ],
    }
    (tmp_path / "gt.json").write_text(json.dumps(gt), encoding="utf-8")
    alert = _mk_alert(
        alert_id="a1", condition_id="CR-01", subject_key="CR-01:s1",
        source_id="s1", timestamp_ms=None,
    )
    (tmp_path / "alerts.jsonl").write_text(alert.model_dump_json() + "\n", encoding="utf-8")

    ev = evaluate_temporal_alerts(tmp_path / "alerts.jsonl", tmp_path / "gt.json")

    assert ev.applicability_state == "not_applicable"
    assert ev.applicability_cause == "non_temporal_source"


def test_v2_overlapping_episode_windows_do_not_share_a_matched_alert(tmp_path) -> None:
    """Dos episodios del mismo condition_id + source_id cuyas ventanas de matching
    se solapan (t_alert_max_ms llega a 10-20s) NO pueden compartir una alerta: una
    unica alerta debe contar como match de a lo sumo un episodio (spec 43 pattern
    P8 'entrada/salida' produce justamente este caso). Sin la exclusion de alertas
    ya consumidas, la misma alerta se cuenta como "primer match" de ambos episodios
    e infla matched_alerts_count/recall silenciosamente."""
    from eovrt_control.evaluation.temporal import MatchingWindow

    gt = {
        "schema_version": "clip_gt.v2",
        "clip_id": "c1",
        "duration_ms": 20000.0,
        "episodes": [
            {
                "id": "e1",
                "condition_id": "CR-01",
                "level": "scene",
                "source_id": "s1",
                "start_ms": 1000.0,  # ventana [4000, 11000]
                "end_ms": 3000.0,
            },
            {
                "id": "e2",
                "condition_id": "CR-01",
                "level": "scene",
                "source_id": "s1",
                "start_ms": 2000.0,  # ventana [5000, 12000], se solapa con e1
                "end_ms": 4000.0,
            },
        ],
    }
    (tmp_path / "gt.json").write_text(json.dumps(gt), encoding="utf-8")
    alert = _mk_alert(
        alert_id="a1", condition_id="CR-01", subject_key="CR-01:s1",
        source_id="s1", timestamp_ms=6000.0,
    )  # cae en ambas ventanas: solo puede ser match de UN episodio
    (tmp_path / "alerts.jsonl").write_text(alert.model_dump_json() + "\n", encoding="utf-8")
    windows = {"CR-01": MatchingWindow(persistencia_min_ms=3000.0, t_alert_max_ms=10000.0)}

    ev = evaluate_temporal_alerts(
        tmp_path / "alerts.jsonl", tmp_path / "gt.json", matching_windows=windows
    )

    assert ev.matched_alerts_count == 1
    assert ev.missed_alerts_count == 1
    assert ev.recall == 0.5
    assert ev.observed_alerts_count == 1


def test_v2_default_matching_windows_full_scenario(tmp_path) -> None:
    """Ejercita las bandas DEFAULT de Tabla D.4 (`matching_windows=None`) con un
    escenario completo: match + re_alert + FP fuera de ventana + sub_threshold_event.
    CR-01 default: persistencia_min_ms=3000, t_alert_max_ms=10000 -> ventana del
    episodio (start_ms=1000) es [4000, 11000]."""
    gt = {
        "schema_version": "clip_gt.v2",
        "clip_id": "c_bands",
        "duration_ms": 30000.0,
        "episodes": [
            {
                "id": "e1",
                "condition_id": "CR-01",
                "level": "scene",
                "source_id": "s1",
                "start_ms": 1000.0,
                "end_ms": 20000.0,
            }
        ],
        "sub_threshold_events": [
            {
                "condition_id": "CR-01",
                "start_ms": 15000.0,
                "end_ms": 18000.0,
                "reason": "transitorio",
            }
        ],
    }
    (tmp_path / "gt.json").write_text(json.dumps(gt), encoding="utf-8")
    alerts = [
        _mk_alert(
            alert_id="a_match", condition_id="CR-01", subject_key="CR-01:s1",
            source_id="s1", timestamp_ms=5000.0,
        ),  # dentro de [4000, 11000] -> match
        _mk_alert(
            alert_id="a_re_alert", condition_id="CR-01", subject_key="CR-01:s1",
            source_id="s1", timestamp_ms=6000.0,
        ),  # segunda dentro de la misma ventana -> re_alert (ADR-011)
        _mk_alert(
            alert_id="a_fp", condition_id="CR-01", subject_key="CR-01:s1",
            source_id="s1", timestamp_ms=25000.0,
        ),  # fuera de toda ventana y de todo sub_threshold_event -> FP
        _mk_alert(
            alert_id="a_sub_threshold", condition_id="CR-01", subject_key="CR-01:s1",
            source_id="s1", timestamp_ms=16000.0,
        ),  # fuera de la ventana del episodio pero dentro del sub_threshold_event
    ]
    with (tmp_path / "alerts.jsonl").open("w", encoding="utf-8") as fh:
        for alert in alerts:
            fh.write(alert.model_dump_json() + "\n")

    ev = evaluate_temporal_alerts(
        tmp_path / "alerts.jsonl", tmp_path / "gt.json", matching_windows=None
    )

    assert ev.matched_alerts_count == 1
    assert ev.re_alerts_count == 1
    assert ev.unexpected_alerts_count == 1
    assert ev.sub_threshold_count == 1
    assert ev.applicability_state == "computed"
