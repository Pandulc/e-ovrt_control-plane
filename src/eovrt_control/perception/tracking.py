"""Tracking ligero por IoU para IDs estables entre frames."""

from __future__ import annotations

from dataclasses import dataclass, field


def iou(box_a: list[float], box_b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    if inter_area <= 0.0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter_area
    if union <= 0.0:
        return 0.0
    return inter_area / union


@dataclass
class SimpleIoUTracker:
    """Asigna IDs estables a personas observadas frame a frame."""

    iou_threshold: float = 0.30
    _tracks: dict[str, list[float]] = field(default_factory=dict)
    _next_index: int = 1

    def assign(self, person_boxes: list[list[float]]) -> list[str]:
        assigned: list[str | None] = [None] * len(person_boxes)
        unmatched_track_ids = set(self._tracks)
        unmatched_indices = list(range(len(person_boxes)))

        pairs: list[tuple[float, int, str]] = []
        for index, box in enumerate(person_boxes):
            for track_id, track_box in self._tracks.items():
                score = iou(box, track_box)
                if score >= self.iou_threshold:
                    pairs.append((score, index, track_id))
        pairs.sort(reverse=True)

        used_indices: set[int] = set()
        used_tracks: set[str] = set()
        for _, index, track_id in pairs:
            if index in used_indices or track_id in used_tracks:
                continue
            assigned[index] = track_id
            self._tracks[track_id] = person_boxes[index]
            used_indices.add(index)
            used_tracks.add(track_id)
            unmatched_track_ids.discard(track_id)
            if index in unmatched_indices:
                unmatched_indices.remove(index)

        for index in unmatched_indices:
            track_id = f"subject_{self._next_index:03d}"
            self._next_index += 1
            assigned[index] = track_id
            self._tracks[track_id] = person_boxes[index]

        # Retirar tracks que no matchearon en este frame.
        for track_id in unmatched_track_ids:
            del self._tracks[track_id]

        return [track_id for track_id in assigned if track_id is not None]


def apply_person_tracking(
    raw: list,
    tracker: SimpleIoUTracker | None,
) -> list:
    """Asigna detection_id estables a personas cuando hay tracker activo."""
    from eovrt_control.perception.events import RawModelDetection

    if tracker is None:
        return raw

    persons = [item for item in raw if item.label == "person"]
    others = [item for item in raw if item.label != "person"]
    if not persons:
        return raw

    track_ids = tracker.assign([person.bbox_xyxy for person in persons])
    tracked_persons = [
        RawModelDetection(
            label=person.label,
            confidence=person.confidence,
            bbox_xyxy=person.bbox_xyxy,
            detection_id=track_id,
        )
        for person, track_id in zip(persons, track_ids, strict=False)
    ]
    return tracked_persons + others


def bbox_center(box: list[float]) -> tuple[float, float]:
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def center_inside_region(point: tuple[float, float], region: list[float]) -> bool:
    cx, cy = point
    x1, y1, x2, y2 = region
    return x1 <= cx <= x2 and y1 <= cy <= y2


def person_upper_body_region(person_box: list[float]) -> list[float]:
    x1, y1, x2, y2 = person_box
    height = max(0.0, y2 - y1)
    margin_x = max(0.0, x2 - x1) * 0.12
    return [x1 + margin_x, y1, x2 - margin_x, y1 + height * 0.45]


def person_torso_region(person_box: list[float]) -> list[float]:
    x1, y1, x2, y2 = person_box
    height = max(0.0, y2 - y1)
    margin_x = max(0.0, x2 - x1) * 0.08
    return [x1 + margin_x, y1 + height * 0.25, x2 - margin_x, y1 + height * 0.85]
