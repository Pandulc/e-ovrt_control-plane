"""Gate final (Task 9): la cadena t_capture->alert de punta a punta.

Prueba, sobre una corrida real (no mockeada), que:
  (a) toda alerta escrita en `alerts.jsonl` carga sus hitos de primera
      evidencia (`first_evidence_unit_id`/`first_evidence_ms`) y el instante
      de registro (`alert_registered_ms`), con `registered >= first_evidence`
      (Tasks 2-3);
  (b) el join `join_capture_to_alert` (Task 8) declara el estado de
      aplicabilidad correcto segun el `source_clock` de la fuente: video
      (media) -> `not_interpretable/dbe_media_time`; wallclock single-host
      -> `computed`.

Es DETERMINISTA a proposito: en vez de levantar los dos servicios reales por
bus (spec 44, Step 4 del brief, out of scope para este dispatch), corre un
replay real por `RunManager` (mismo camino que `test_run_manager.py`) sobre un
fixture chico de detecciones que confirma CR-01 (persona sin casco en la
region del patron), y lee el `alerts.jsonl` real que la corrida escribio
(mismo camino que `test_alerts_are_read_from_the_run_artifacts`). No usa el
bus ZeroMQ: el replay `media_jsonl` no abre sockets, asi que no aplica la
regla de SIGABRT (cerrar sockets solo desde el hilo que los creo).

`capture_monotonic_ns` no existe en ningun artefacto real de esta corrida
(vendria del `metrics.jsonl` del media-plane en la corrida live de dos
servicios): se sintetiza aca un valor levemente anterior a
`alert_registered_ms` por unidad, para poder ejercitar la rama `computed`
del join con datos derivados de una alerta real.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eovrt_control.metrics.latency import join_capture_to_alert
from eovrt_control.service.run_manager import RunManager
from eovrt_control.service.run_request import ControlRunRequest
from eovrt_control.service.settings import ServiceSettings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATTERNS = _REPO_ROOT / "configs/patterns/cr01_cr02_v1.yaml"


def _event(unit_id: str) -> dict:
    """Persona detectada sin casco en la region CR-01: confirma en 1 frame
    (confirm_after_frames: 1 en cr01_cr02_v1.yaml). Mismo shape que
    `test_run_manager.py`/`test_alert_bus_integration.py`.
    """
    return {
        "run_id": "media-run",
        "unit_id": unit_id,
        "source": {
            "source_id": "cam-1",
            "source_type": "video",
            "frame_index": 0,
            "timestamp_ms": 0.0,
            "width": 640,
            "height": 480,
        },
        "model": {"name": "mock", "device": "cpu"},
        "prompts": {"prompt_set_id": "cr01_cr02_v1"},
        "detections": [
            {
                "detection_id": "p1",
                "label": "person",
                "prompt_id": "person",
                "confidence": 0.9,
                "bbox_xyxy": [100, 100, 220, 420],
            }
        ],
    }


def _detections_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "detections.jsonl"
    path.write_text(json.dumps(_event("u1")) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def _gate_run_alerts(tmp_path_factory) -> list[dict]:
    """Corre el replay real UNA sola vez y devuelve las alertas leidas del
    `alerts.jsonl` real que escribio (RunManager, camino del servicio).
    """
    tmp_path = tmp_path_factory.mktemp("capture-to-alert-gate")
    manager = RunManager(ServiceSettings(runs_dir=tmp_path / "runs"))
    try:
        payload = {
            "run": {"id": "gate-run", "scenario": "DBE", "name": "gate"},
            "input": {"type": "media_jsonl", "path": str(_detections_fixture(tmp_path))},
            "patterns": {"file": str(_PATTERNS), "active_ids": ["CR-01"]},
        }
        manager.start_run(ControlRunRequest(mode="replay", config=payload))
        manager.join_active(timeout=30.0)

        state = manager.get("gate-run")
        assert state["status"] == "succeeded"
        return manager.alerts("gate-run")
    finally:
        manager.shutdown()
        manager.join_active(timeout=15.0)


@pytest.fixture(params=["media", "wallclock"])
def live_run_artifacts(request, _gate_run_alerts):
    """(alerts, capture_ns_by_unit, source_clock) leidos/derivados de la
    corrida real. `capture_ns_by_unit` se sintetiza (spec 40 SS5.2.4: en la
    corrida live de dos servicios vendria de `metrics.jsonl` del media-plane)
    un poco antes de `alert_registered_ms` para cada unidad, de forma que la
    rama `wallclock` del join de una latencia positiva.
    """
    alerts = _gate_run_alerts
    capture_ns_by_unit = {}
    for alert in alerts:
        unit_id = alert["first_evidence_unit_id"]
        if unit_id is None:
            continue
        registered_ms = alert["alert_registered_ms"]
        capture_ns_by_unit[unit_id] = (registered_ms - 50.0) * 1e6
    return alerts, capture_ns_by_unit, request.param


def test_every_alert_has_first_evidence_and_registered(live_run_artifacts) -> None:
    alerts, _cap_by_unit, _source_clock = live_run_artifacts
    assert alerts, "la corrida debe producir >=1 alerta"
    for alert in alerts:
        assert alert["first_evidence_unit_id"] is not None
        assert alert["first_evidence_ms"] is not None
        assert alert["alert_registered_ms"] is not None
        assert alert["alert_registered_ms"] >= alert["first_evidence_ms"]


def test_join_declares_expected_applicability(live_run_artifacts) -> None:
    alerts, cap_by_unit, source_clock = live_run_artifacts
    out = join_capture_to_alert(alerts, cap_by_unit, source_clock=source_clock)
    assert out, "el join debe producir >=1 fila"
    if source_clock == "media":
        assert all(
            row["status"] == "not_interpretable" and row["cause"] == "dbe_media_time"
            for row in out
        )
    elif source_clock == "wallclock":
        assert all(row["status"] == "computed" for row in out)
