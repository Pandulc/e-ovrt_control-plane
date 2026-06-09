"""Motor de estados para patrones de riesgo."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from eovrt_control.config import PatternDefinition
from eovrt_control.contracts.alerts import AlertEvent
from eovrt_control.contracts.media import DetectionEvent
from eovrt_control.contracts.pattern import PatternEvidence, PatternStateChanged
from eovrt_control.engine.evaluators.spatial_absence import evaluate_spatial_absence


@dataclass
class PatternRuntimeState:
    state: str = "inactive"
    hit_count: int = 0
    clear_count: int = 0


@dataclass(frozen=True)
class PatternEngineResult:
    pattern_events: list[PatternStateChanged]
    alerts: list[AlertEvent]
    evidences_count: int
    subjects_count: int


class PatternEngine:
    """Evalua patrones configurados y mantiene estado temporal simple."""

    def __init__(self, control_run_id: str, patterns: list[PatternDefinition]) -> None:
        self.control_run_id = control_run_id
        self.patterns = patterns
        self._state: dict[tuple[str, str], PatternRuntimeState] = {}

    def process(self, event: DetectionEvent) -> PatternEngineResult:
        pattern_events: list[PatternStateChanged] = []
        alerts: list[AlertEvent] = []
        evidences_count = 0
        subjects_count = 0

        for pattern in self.patterns:
            result = evaluate_spatial_absence(event, pattern)
            evidence_by_subject = {evidence.subject_key: evidence for evidence in result.evidences}
            evidences_count += len(result.evidences)
            subjects_count += len(result.observed_subject_keys)

            for subject_key, evidence in evidence_by_subject.items():
                change = self._advance_hit(event, pattern, subject_key, evidence)
                if change is None:
                    continue
                pattern_events.append(change)
                if change.state == "confirmed":
                    alerts.append(self._make_alert(event, pattern, change))

            clear_subjects = result.observed_subject_keys - set(evidence_by_subject)
            for subject_key in clear_subjects:
                change = self._advance_clear(event, pattern, subject_key)
                if change is not None:
                    pattern_events.append(change)

        return PatternEngineResult(
            pattern_events=pattern_events,
            alerts=alerts,
            evidences_count=evidences_count,
            subjects_count=subjects_count,
        )

    def _advance_hit(
        self,
        event: DetectionEvent,
        pattern: PatternDefinition,
        subject_key: str,
        evidence: PatternEvidence,
    ) -> PatternStateChanged | None:
        key = (pattern.id, subject_key)
        runtime_state = self._state.setdefault(key, PatternRuntimeState())
        previous = runtime_state.state
        runtime_state.hit_count += 1
        runtime_state.clear_count = 0

        if runtime_state.state == "inactive":
            runtime_state.state = (
                "confirmed"
                if runtime_state.hit_count >= pattern.timing.confirm_after_frames
                else "candidate"
            )
        elif runtime_state.state == "candidate":
            if runtime_state.hit_count >= pattern.timing.confirm_after_frames:
                runtime_state.state = "confirmed"
        elif runtime_state.state == "confirmed":
            runtime_state.state = "sustained"

        if previous == runtime_state.state:
            return None
        return self._make_state_event(event, pattern, subject_key, previous, runtime_state.state, evidence)

    def _advance_clear(
        self,
        event: DetectionEvent,
        pattern: PatternDefinition,
        subject_key: str,
    ) -> PatternStateChanged | None:
        key = (pattern.id, subject_key)
        runtime_state = self._state.get(key)
        if runtime_state is None or runtime_state.state in {"inactive", "resolved"}:
            return None

        runtime_state.clear_count += 1
        if runtime_state.clear_count < pattern.timing.resolve_after_frames:
            return None

        previous = runtime_state.state
        runtime_state.state = "resolved"
        placeholder = PatternEvidence(
            pattern_id=pattern.id,
            condition_id=pattern.condition_id,
            subject_key=subject_key,
            subject={
                "label": pattern.subject_class,
                "confidence": 0.0,
                "bbox_xyxy": [0.0, 0.0, 0.0, 0.0],
            },
            missing_class=pattern.required_absent_class,
            supporting=[],
            score=0.0,
            rationale="El sujeto observado ya no satisface el patron.",
        )
        return self._make_state_event(event, pattern, subject_key, previous, "resolved", placeholder)

    def _make_state_event(
        self,
        event: DetectionEvent,
        pattern: PatternDefinition,
        subject_key: str,
        previous_state: str,
        state: str,
        evidence: PatternEvidence,
    ) -> PatternStateChanged:
        return PatternStateChanged(
            control_run_id=self.control_run_id,
            media_run_id=event.run_id,
            unit_id=event.unit_id,
            source_id=event.source.source_id,
            pattern_id=pattern.id,
            condition_id=pattern.condition_id,
            subject_key=subject_key,
            previous_state=previous_state,
            state=state,
            severity=pattern.severity,
            evidence=evidence,
            frame_index=event.source.frame_index,
            timestamp_ms=event.source.timestamp_ms,
        )

    def _make_alert(
        self,
        event: DetectionEvent,
        pattern: PatternDefinition,
        change: PatternStateChanged,
    ) -> AlertEvent:
        seed = (
            f"{self.control_run_id}:{event.run_id}:{event.unit_id}:"
            f"{pattern.id}:{change.subject_key}"
        )
        return AlertEvent(
            control_run_id=self.control_run_id,
            media_run_id=event.run_id,
            unit_id=event.unit_id,
            source_id=event.source.source_id,
            alert_id=str(uuid5(NAMESPACE_URL, seed)),
            pattern_id=pattern.id,
            condition_id=pattern.condition_id,
            subject_key=change.subject_key,
            severity=pattern.severity,
            evidence=change.evidence,
            frame_index=event.source.frame_index,
            timestamp_ms=event.source.timestamp_ms,
        )

