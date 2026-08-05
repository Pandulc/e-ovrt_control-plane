"""Asigna `track_id` post-hoc sobre un `detections.jsonl` ya producido.

El motor consume `Detection.track_id` bajo `granularity: subject` desde siempre
(spec 41 §2.1) y el contrato existe de punta a punta: **lo unico que faltaba era el
productor** (doc 79 del repo docs). Esta herramienta lo aporta sobre las detecciones ya
hechas, de modo que el experimento G0-vs-G1 no necesita re-inferir ni tocar el pipeline
online — y la comparacion es de variable unica, porque las cajas son bit a bit las
mismas y solo cambia la granularidad del motor.

Reusa `SimpleIoUTracker` de `eovrt_labs` (IoU + distancia de centro + area, con ventana
de perdida), que ya esta testeado. Lo que agrega este modulo es el pegamento — y ahi
estan las trampas:

  - Escribe en **`track_id`**, no en `detection_id`. `apply_person_tracking` de labs
    escribe en `detection_id` (bug doc 34 §4.1), que el motor NO lee como identidad.
  - Procesa los frames **en orden**: desordenados, el tracker ve saltos hacia atras y
    fragmenta identidades.
  - Solo las personas reciben identidad; el resto de las detecciones pasa intacto.

Uso:
  python -m eovrt_control.tools.track_detections --in <detections.jsonl> --out <out.jsonl>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def track_persons_in_event(event: dict, tracker) -> dict:
    """Devuelve una copia del evento con `track_id` en cada deteccion de persona."""
    detections = list(event.get("detections") or [])
    indices = [i for i, d in enumerate(detections) if d.get("label") == "person"]
    source = event.get("source") or {}

    if not indices:
        # Igual se avisa al tracker: un frame sin personas es informacion (envejece
        # los tracks perdidos), no un frame que no ocurrio.
        tracker.assign([], frame_index=source.get("frame_index"),
                       timestamp_ms=source.get("timestamp_ms"))
        return dict(event, detections=detections)

    track_ids = tracker.assign(
        [detections[i]["bbox_xyxy"] for i in indices],
        confidences=[detections[i].get("confidence", 1.0) for i in indices],
        frame_index=source.get("frame_index"),
        timestamp_ms=source.get("timestamp_ms"),
    )
    nuevos = list(detections)
    for i, track_id in zip(indices, track_ids, strict=False):
        nuevos[i] = dict(detections[i], track_id=track_id)
    return dict(event, detections=nuevos)


def track_event_stream(in_path: Path, out_path: Path, **tracker_kwargs) -> dict:
    """Trackea un `detections.jsonl` completo. Devuelve estadisticas de la corrida."""
    from eovrt_labs.perception.tracking import SimpleIoUTracker

    eventos = []
    with Path(in_path).open() as fh:
        for line in fh:
            if line.strip():
                eventos.append(json.loads(line))
    # En orden de frame: el tracker asume avance monotono.
    eventos.sort(key=lambda e: (e.get("source") or {}).get("frame_index") or 0)

    tracker = SimpleIoUTracker(**tracker_kwargs)
    vistos: set[str] = set()
    personas = 0
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with Path(out_path).open("w") as fh:
        for event in eventos:
            trackeado = track_persons_in_event(event, tracker)
            for d in trackeado["detections"]:
                if d.get("label") == "person":
                    personas += 1
                    if d.get("track_id"):
                        vistos.add(d["track_id"])
            fh.write(json.dumps(trackeado, ensure_ascii=False) + "\n")

    return {"frames": len(eventos), "person_detections": personas, "tracks": len(vistos)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--in", dest="src", required=True, type=Path)
    ap.add_argument("--out", dest="dst", required=True, type=Path)
    ap.add_argument("--iou-threshold", type=float, default=None)
    ap.add_argument("--max-lost-ms", type=float, default=None)
    a = ap.parse_args()

    kwargs = {}
    if a.iou_threshold is not None:
        kwargs["iou_threshold"] = a.iou_threshold
    if a.max_lost_ms is not None:
        kwargs["max_lost_ms"] = a.max_lost_ms

    stats = track_event_stream(a.src, a.dst, **kwargs)
    print(f"{stats['frames']} frames, {stats['person_detections']} personas, "
          f"{stats['tracks']} tracks -> {a.dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
