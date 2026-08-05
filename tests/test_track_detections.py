"""Tracking post-hoc de `track_id` sobre un detections.jsonl ya producido.

Por que post-hoc: el motor ya consume `Detection.track_id` bajo `granularity: subject`
(spec 41 §2.1) y el contrato existe de punta a punta — **lo unico que falta es el
productor** (doc 79). Asignar identidad sobre las detecciones YA HECHAS permite correr
el experimento G0-vs-G1 sin re-inferir y sin tocar el pipeline online: la comparacion
es de variable unica porque las cajas son bit a bit las mismas y solo cambia la
granularidad del motor.

`SimpleIoUTracker` (eovrt_labs) ya esta testeado; lo que estos tests fijan es el
pegamento, que es donde estan las trampas:
  - solo las personas reciben `track_id` (el resto pasa intacto);
  - los frames se procesan en ORDEN, si no el tracker ve saltos hacia atras;
  - `apply_person_tracking` de labs escribe en `detection_id` (bug doc 34 §4.1) —
    aca se escribe en `track_id`, que es lo que el motor lee.
"""
import json

from eovrt_control.tools.track_detections import track_event_stream, track_persons_in_event


def _det(label, bbox, conf=0.9, det_id=None):
    return {"detection_id": det_id, "label": label, "prompt_id": label,
            "confidence": conf, "bbox_xyxy": bbox}


def _ev(frame, dets, ts=None):
    return {
        "schema_version": "media.detection.v1", "run_id": "r", "unit_id": f"u{frame}",
        "source": {"source_id": "clip.mp4", "source_type": "video", "width": 640,
                   "height": 480, "frame_index": frame,
                   "timestamp_ms": ts if ts is not None else frame * 33.3},
        "model": {"name": "m"}, "prompts": {"prompt_set_id": "p"}, "detections": dets,
    }


def test_asigna_track_id_a_las_personas():
    from eovrt_labs.perception.tracking import SimpleIoUTracker
    ev = _ev(0, [_det("person", [0, 0, 50, 100])])
    out = track_persons_in_event(ev, SimpleIoUTracker())
    assert out["detections"][0]["track_id"] == "subject_001"


def test_las_clases_que_no_son_persona_pasan_intactas():
    from eovrt_labs.perception.tracking import SimpleIoUTracker
    ev = _ev(0, [_det("person", [0, 0, 50, 100]), _det("helmet", [10, 5, 30, 25])])
    out = track_persons_in_event(ev, SimpleIoUTracker())
    casco = [d for d in out["detections"] if d["label"] == "helmet"][0]
    assert "track_id" not in casco or casco["track_id"] is None
    assert casco["bbox_xyxy"] == [10, 5, 30, 25]


def test_la_misma_persona_conserva_su_id_entre_frames():
    from eovrt_labs.perception.tracking import SimpleIoUTracker
    tracker = SimpleIoUTracker()
    a = track_persons_in_event(_ev(0, [_det("person", [0, 0, 50, 100])]), tracker)
    b = track_persons_in_event(_ev(1, [_det("person", [2, 1, 52, 101])]), tracker)
    assert a["detections"][0]["track_id"] == b["detections"][0]["track_id"]


def test_dos_personas_separadas_reciben_ids_distintos():
    from eovrt_labs.perception.tracking import SimpleIoUTracker
    ev = _ev(0, [_det("person", [0, 0, 50, 100]), _det("person", [300, 0, 350, 100])])
    out = track_persons_in_event(ev, SimpleIoUTracker())
    ids = {d["track_id"] for d in out["detections"]}
    assert len(ids) == 2


def test_un_evento_sin_personas_no_rompe():
    from eovrt_labs.perception.tracking import SimpleIoUTracker
    out = track_persons_in_event(_ev(0, [_det("helmet", [0, 0, 10, 10])]), SimpleIoUTracker())
    assert out["detections"][0]["label"] == "helmet"


def test_el_stream_se_procesa_en_orden_de_frame(tmp_path):
    """Desordenado, el tracker veria saltos hacia atras y fragmentaria identidades."""
    src, dst = tmp_path / "in.jsonl", tmp_path / "out.jsonl"
    src.write_text("\n".join(json.dumps(_ev(f, [_det("person", [f, 0, 50 + f, 100])]))
                             for f in (2, 0, 1)))
    stats = track_event_stream(src, dst)
    frames = [json.loads(x)["source"]["frame_index"] for x in dst.read_text().splitlines()]
    assert frames == [0, 1, 2]
    # la persona se mueve de a 1 px: un solo track
    assert stats["tracks"] == 1


def test_reporta_estadisticas_utiles(tmp_path):
    src, dst = tmp_path / "in.jsonl", tmp_path / "out.jsonl"
    src.write_text("\n".join([
        json.dumps(_ev(0, [_det("person", [0, 0, 50, 100])])),
        json.dumps(_ev(1, [_det("person", [1, 0, 51, 100]), _det("person", [300, 0, 350, 100])])),
    ]))
    stats = track_event_stream(src, dst)
    assert stats["frames"] == 2
    assert stats["person_detections"] == 3
    assert stats["tracks"] == 2


def test_no_deja_personas_sin_track_id(tmp_path):
    """Si una persona quedara sin id, el motor degrada a escena EN SILENCIO
    (causa no_track_id) y el experimento G1 mediria G0 sin avisar."""
    src, dst = tmp_path / "in.jsonl", tmp_path / "out.jsonl"
    src.write_text("\n".join(json.dumps(_ev(f, [_det("person", [f * 5, 0, 50, 100]),
                                               _det("person", [200, 0, 250, 100])]))
                             for f in range(4)))
    track_event_stream(src, dst)
    for linea in dst.read_text().splitlines():
        for d in json.loads(linea)["detections"]:
            if d["label"] == "person":
                assert d.get("track_id"), "persona sin track_id: el motor degradaría a escena"
