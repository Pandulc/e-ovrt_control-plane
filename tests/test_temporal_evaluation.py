import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from eovrt_control.evaluation import evaluate_temporal_alerts
from eovrt_control.evaluation.temporal import ClipEpisode, TemporalGroundTruth
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
    summary = run_replay(_scene_config(tmp_path))
    evaluation = evaluate_temporal_alerts(
        alerts_path=summary.output_files["alerts"],
        ground_truth_path=str(SCENE_FIXTURE / "ground_truth_v2.json"),
    )
    # Exactamente las dos condiciones persistentes; nada espurio del transitorio.
    assert evaluation.observed_alerts_count == 2
    assert evaluation.matched_alerts_count == 2
    assert evaluation.missed_alerts_count == 0
    assert evaluation.unexpected_alerts_count == 0
    assert evaluation.precision == 1.0
    assert evaluation.recall == 1.0
    assert evaluation.f1 == 1.0
    assert evaluation.warnings == []
    # Latencias primera-evidencia -> alerta: insumo de los requisitos PR-01/PR-02.
    # CR-01 confirma en f4 desde f2 (2 frames, 1000 ms); CR-02 en f5 desde f2
    # (3 frames, 1500 ms). Promedios: 2.5 frames, 1250 ms.
    assert evaluation.avg_latency_frames_from_first_evidence == 2.5
    assert evaluation.avg_latency_ms_from_first_evidence == 1250.0


def test_subject_granularity_matches_expected_alerts_with_f1_one(tmp_path) -> None:
    summary = run_replay(_subject_config(tmp_path))
    # Patron subject sobre fixture tracked: no debe degradar a escena.
    assert summary.degraded is False
    assert "no_track_id" not in summary.degradation_causes
    evaluation = evaluate_temporal_alerts(
        alerts_path=summary.output_files["alerts"],
        ground_truth_path=str(TRACKED_FIXTURE / "ground_truth_v2.json"),
    )
    assert evaluation.observed_alerts_count == 2
    assert evaluation.matched_alerts_count == 2
    assert evaluation.unexpected_alerts_count == 0
    assert evaluation.precision == 1.0
    assert evaluation.recall == 1.0
    assert evaluation.f1 == 1.0


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


def test_clip_episode_subject_level_requires_subject_key() -> None:
    """Un episodio level='subject' sin subject_key caeria a matching por escena
    y matchearia la alerta de OTRO sujeto de la misma fuente (F1 inflado)."""
    with pytest.raises(ValidationError):
        ClipEpisode.model_validate(
            {
                "id": "ep",
                "condition_id": "CR-01",
                "level": "subject",
                "source_id": "cam-1",
                "first_evidence_frame_index": 0,
                "expected_alert_frame_index": 2,
            }
        )


def test_clip_episode_scene_level_requires_source_id() -> None:
    """Sin source_id el matching de escena compara contra None y nunca matchea:
    100% missed silencioso en vez de un error de validacion."""
    with pytest.raises(ValidationError):
        ClipEpisode.model_validate(
            {
                "id": "ep",
                "condition_id": "CR-01",
                "level": "scene",
                "first_evidence_frame_index": 0,
                "expected_alert_frame_index": 2,
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
