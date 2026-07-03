"""Tracking temporal ligero para IDs estables entre frames."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt

AppearanceFeature = list[float]


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
    area_a = _box_area(box_a)
    area_b = _box_area(box_b)
    union = area_a + area_b - inter_area
    if union <= 0.0:
        return 0.0
    return inter_area / union


@dataclass
class TrackState:
    """Estado interno de una persona seguida temporalmente."""

    track_id: str
    box: list[float]
    previous_box: list[float] | None = None
    last_seen_frame: int = 0
    previous_seen_frame: int | None = None
    last_seen_timestamp_ms: float | None = None
    appearance: AppearanceFeature | None = None
    appearance_hits: int = 0
    hits: int = 1

    def predict_box(self, frame_index: int) -> list[float]:
        if (
            self.previous_box is None
            or self.previous_seen_frame is None
            or self.last_seen_frame <= self.previous_seen_frame
        ):
            return list(self.box)

        elapsed = max(1, self.last_seen_frame - self.previous_seen_frame)
        lookahead = max(0, frame_index - self.last_seen_frame)
        velocity = [
            (current - previous) / elapsed
            for current, previous in zip(self.box, self.previous_box, strict=False)
        ]
        return [
            current + delta * lookahead
            for current, delta in zip(self.box, velocity, strict=False)
        ]


@dataclass
class SimpleIoUTracker:
    """Asigna IDs estables a personas observadas frame a frame.

    El nombre se conserva por compatibilidad, pero el tracker ya no usa IoU puro:
    mantiene tracks perdidos durante una ventana corta y combina IoU, distancia
    de centro y similitud de area para reducir fragmentacion de identidades.
    """

    iou_threshold: float = 0.20
    max_lost_ms: float = 1500.0
    max_lost_frames: int = 30
    center_gate_ratio: float = 0.75
    area_ratio_min: float = 0.35
    min_score: float = 0.25
    appearance_enabled: bool = True
    appearance_weight: float = 0.45
    appearance_min_similarity: float = 0.30
    appearance_update_alpha: float = 0.20
    appearance_center_gate_ratio: float = 1.75
    _tracks: dict[str, TrackState] = field(default_factory=dict)
    _next_index: int = 1
    _frame_counter: int = -1

    def assign(
        self,
        person_boxes: list[list[float]],
        *,
        confidences: list[float] | None = None,
        appearance_features: list[AppearanceFeature | None] | None = None,
        frame_index: int | None = None,
        timestamp_ms: float | None = None,
    ) -> list[str]:
        if frame_index is None:
            self._frame_counter += 1
            frame_index = self._frame_counter
        else:
            self._frame_counter = max(self._frame_counter, frame_index)

        self._drop_expired(frame_index, timestamp_ms)

        assigned: list[str | None] = [None] * len(person_boxes)
        unmatched_indices = list(range(len(person_boxes)))

        pairs: list[tuple[float, int, str]] = []
        for index, box in enumerate(person_boxes):
            confidence = confidences[index] if confidences and index < len(confidences) else 1.0
            if confidence < self.min_score:
                continue
            for track_id, track in self._tracks.items():
                appearance = (
                    appearance_features[index]
                    if appearance_features and index < len(appearance_features)
                    else None
                )
                score = self._match_score(box, track, frame_index, appearance)
                if score is not None:
                    pairs.append((score, index, track_id))
        pairs.sort(reverse=True)

        used_indices: set[int] = set()
        used_tracks: set[str] = set()
        for _, index, track_id in pairs:
            if index in used_indices or track_id in used_tracks:
                continue
            assigned[index] = track_id
            appearance = (
                appearance_features[index]
                if appearance_features and index < len(appearance_features)
                else None
            )
            self._update_track(
                track_id,
                person_boxes[index],
                frame_index,
                timestamp_ms,
                appearance,
            )
            used_indices.add(index)
            used_tracks.add(track_id)
            if index in unmatched_indices:
                unmatched_indices.remove(index)

        for index in unmatched_indices:
            track_id = f"subject_{self._next_index:03d}"
            self._next_index += 1
            assigned[index] = track_id
            confidence = confidences[index] if confidences and index < len(confidences) else 1.0
            if confidence >= self.min_score:
                self._tracks[track_id] = TrackState(
                    track_id=track_id,
                    box=list(person_boxes[index]),
                    last_seen_frame=frame_index,
                    last_seen_timestamp_ms=timestamp_ms,
                    appearance=_initial_appearance(appearance_features, index),
                    appearance_hits=(
                        1
                        if appearance_features
                        and index < len(appearance_features)
                        and appearance_features[index] is not None
                        else 0
                    ),
                )

        return [track_id for track_id in assigned if track_id is not None]

    def _drop_expired(self, frame_index: int, timestamp_ms: float | None) -> None:
        expired: list[str] = []
        for track_id, track in self._tracks.items():
            if self._is_track_alive(track, frame_index, timestamp_ms):
                continue
            expired.append(track_id)
        for track_id in expired:
            del self._tracks[track_id]

    def _is_track_alive(
        self,
        track: TrackState,
        frame_index: int,
        timestamp_ms: float | None,
    ) -> bool:
        if timestamp_ms is not None and track.last_seen_timestamp_ms is not None:
            return timestamp_ms - track.last_seen_timestamp_ms <= self.max_lost_ms
        return frame_index - track.last_seen_frame <= self.max_lost_frames

    def _update_track(
        self,
        track_id: str,
        box: list[float],
        frame_index: int,
        timestamp_ms: float | None,
        appearance: AppearanceFeature | None,
    ) -> None:
        track = self._tracks[track_id]
        track.previous_box = list(track.box)
        track.previous_seen_frame = track.last_seen_frame
        track.box = list(box)
        track.last_seen_frame = frame_index
        track.last_seen_timestamp_ms = timestamp_ms
        self._update_appearance(track, appearance)
        track.hits += 1

    def _update_appearance(
        self,
        track: TrackState,
        appearance: AppearanceFeature | None,
    ) -> None:
        if not self.appearance_enabled or appearance is None:
            return
        if track.appearance is None:
            track.appearance = list(appearance)
        else:
            alpha = self.appearance_update_alpha
            track.appearance = _normalize_feature(
                [
                    (old * (1.0 - alpha)) + (new * alpha)
                    for old, new in zip(track.appearance, appearance, strict=False)
                ]
            )
        track.appearance_hits += 1

    def _match_score(
        self,
        box: list[float],
        track: TrackState,
        frame_index: int,
        appearance: AppearanceFeature | None,
    ) -> float | None:
        predicted = track.predict_box(frame_index)
        overlap = iou(box, predicted)
        center_score = _center_score(box, predicted, self.center_gate_ratio)
        wide_center_score = _center_score(
            box,
            predicted,
            self.appearance_center_gate_ratio,
        )
        area_score = _area_score(box, predicted)
        appearance_score = _appearance_score(appearance, track.appearance)
        has_appearance = self.appearance_enabled and appearance_score is not None

        if area_score < self.area_ratio_min:
            return None
        if overlap < self.iou_threshold and center_score <= 0.0:
            if (
                not has_appearance
                or appearance_score < self.appearance_min_similarity
                or wide_center_score <= 0.0
            ):
                return None

        geometry_score = (overlap * 0.65) + (center_score * 0.25) + (area_score * 0.10)
        if not has_appearance:
            return geometry_score

        appearance_weight = max(0.0, min(1.0, self.appearance_weight))
        score = (geometry_score * (1.0 - appearance_weight)) + (
            appearance_score * appearance_weight
        )
        if center_score <= 0.0:
            score += wide_center_score * 0.05
        if (
            track.appearance_hits >= 2
            and appearance_score < self.appearance_min_similarity
            and overlap < max(0.35, self.iou_threshold)
        ):
            score -= 0.20
        return score


def apply_person_tracking(
    raw: list,
    tracker: SimpleIoUTracker | None,
    *,
    appearance_features: list[AppearanceFeature | None] | None = None,
    frame_index: int | None = None,
    timestamp_ms: float | None = None,
) -> list:
    """Asigna detection_id estables a personas cuando hay tracker activo."""
    from eovrt_control.perception.events import RawModelDetection

    if tracker is None:
        return raw

    persons = [item for item in raw if item.label == "person"]
    if not persons:
        tracker.assign([], frame_index=frame_index, timestamp_ms=timestamp_ms)
        return raw

    track_ids = tracker.assign(
        [person.bbox_xyxy for person in persons],
        confidences=[person.confidence for person in persons],
        appearance_features=appearance_features,
        frame_index=frame_index,
        timestamp_ms=timestamp_ms,
    )
    track_iter = iter(track_ids)
    tracked: list = []
    for item in raw:
        if item.label != "person":
            tracked.append(item)
            continue
        tracked.append(
            RawModelDetection(
                label=item.label,
                confidence=item.confidence,
                bbox_xyxy=item.bbox_xyxy,
                detection_id=next(track_iter),
            )
        )
    return tracked


def bbox_center(box: list[float]) -> tuple[float, float]:
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _box_area(box: list[float]) -> float:
    x1, y1, x2, y2 = box
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _box_diag(box: list[float]) -> float:
    x1, y1, x2, y2 = box
    return sqrt(max(0.0, x2 - x1) ** 2 + max(0.0, y2 - y1) ** 2)


def _center_score(
    box_a: list[float],
    box_b: list[float],
    center_gate_ratio: float,
) -> float:
    ax, ay = bbox_center(box_a)
    bx, by = bbox_center(box_b)
    distance = sqrt((ax - bx) ** 2 + (ay - by) ** 2)
    gate = max(_box_diag(box_a), _box_diag(box_b)) * center_gate_ratio
    if gate <= 0.0 or distance > gate:
        return 0.0
    return 1.0 - (distance / gate)


def _area_score(box_a: list[float], box_b: list[float]) -> float:
    area_a = _box_area(box_a)
    area_b = _box_area(box_b)
    if area_a <= 0.0 or area_b <= 0.0:
        return 0.0
    return min(area_a, area_b) / max(area_a, area_b)


def _normalize_feature(feature: AppearanceFeature) -> AppearanceFeature:
    norm = sqrt(sum(value * value for value in feature))
    if norm <= 0.0:
        return list(feature)
    return [value / norm for value in feature]


def _initial_appearance(
    appearance_features: list[AppearanceFeature | None] | None,
    index: int,
) -> AppearanceFeature | None:
    if appearance_features is None or index >= len(appearance_features):
        return None
    feature = appearance_features[index]
    if feature is None:
        return None
    return _normalize_feature(feature)


def _appearance_score(
    feature_a: AppearanceFeature | None,
    feature_b: AppearanceFeature | None,
) -> float | None:
    if feature_a is None or feature_b is None:
        return None
    if len(feature_a) != len(feature_b):
        return None
    similarity = sum(a * b for a, b in zip(feature_a, feature_b, strict=False))
    return max(0.0, min(1.0, similarity))


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
