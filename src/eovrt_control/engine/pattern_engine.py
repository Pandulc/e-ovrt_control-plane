"""Motor de estados para patrones de riesgo."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
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
    first_hit_timestamp_ms: float | None = None
    clear_started_timestamp_ms: float | None = None
    last_seen_timestamp_ms: float | None = None
    last_seen_frame: int | None = None
    last_alert_timestamp_ms: float | None = None
    last_alert_frame: int | None = None
    last_covered_timestamp_ms: float | None = None
    last_covered_frame: int | None = None
    max_subjects_in_evidence: int = 0
    # Hito de primera evidencia positiva del episodio (spec 40 SS5.2.4): instante
    # monotonico de recepcion (no confundir con first_hit_timestamp_ms, que es
    # tiempo de fuente/frame). No se reescribe hasta resolve/expire.
    first_evidence_monotonic_ms: float | None = None
    first_evidence_unit_id: str | None = None
    first_evidence_frame_index: int | None = None


def _memory_configured(pattern: PatternDefinition) -> bool:
    return (
        pattern.timing.coverage_memory_ms is not None
        or pattern.timing.coverage_memory_frames is not None
    )


def _memory_applicable(pattern: PatternDefinition) -> bool:
    """ADR-012: la memoria de cobertura es estado POR SUJETO a traves de frames.
    Bajo escena no hay identidad de sujeto a la cual colgarla, asi que no se
    aplica. Unico predicado: `_memory_covers` lo consulta en vez de repetirlo."""
    return _memory_configured(pattern) and pattern.granularity == "subject"


@dataclass(frozen=True)
class PatternEngineResult:
    pattern_events: list[PatternStateChanged]
    alerts: list[AlertEvent]
    evidences_count: int
    subjects_count: int
    degradation_causes: set[str] = field(default_factory=set)


class PatternEngine:
    """Evalua patrones configurados y mantiene estado temporal simple."""

    def __init__(
        self,
        control_run_id: str,
        patterns: list[PatternDefinition],
        experiment_id: str | None = None,
    ) -> None:
        self.control_run_id = control_run_id
        self.patterns = patterns
        # ADR-004: identificador de experimento, propagado a cada evento
        # emitido por este motor (no solo al RunSummary).
        self.experiment_id = experiment_id
        self._state: dict[tuple[str, str], PatternRuntimeState] = {}
        # Instante monotonico de recepcion de la unidad en curso (Task 2);
        # process() lo actualiza en cada llamada.
        self._current_ts_receive_ms: float | None = None

    def process(
        self, event: DetectionEvent, ts_receive_ms: float | None = None
    ) -> PatternEngineResult:
        # Instante monotonico de recepcion de esta unidad; lo leen los helpers
        # de emision durante este process() para el hito first_evidence_*.
        self._current_ts_receive_ms = ts_receive_ms
        pattern_events: list[PatternStateChanged] = []
        alerts: list[AlertEvent] = []
        evidences_count = 0
        subjects_count = 0
        degradation_causes: set[str] = set()

        for pattern in self.patterns:
            result = evaluate_spatial_absence(event, pattern)
            evidence_by_subject = {evidence.subject_key: evidence for evidence in result.evidences}
            evidences_count += len(result.evidences)
            subjects_count += result.subjects_observed
            degradation_causes |= result.degradation_causes

            memory_enabled = _memory_applicable(pattern)
            if _memory_configured(pattern) and not memory_enabled:
                degradation_causes.add("coverage_memory_unsupported_scene")

            # Sujetos con cobertura real: registrar la cobertura y avanzar clear.
            clear_subjects = result.observed_subject_keys - set(evidence_by_subject)
            for subject_key in clear_subjects:
                key = (pattern.id, subject_key)
                runtime_state = self._state.get(key)
                if runtime_state is None and memory_enabled:
                    runtime_state = self._state.setdefault(key, PatternRuntimeState())
                if runtime_state is not None:
                    runtime_state.last_covered_timestamp_ms = event.source.timestamp_ms
                    runtime_state.last_covered_frame = event.source.frame_index
                    runtime_state.last_seen_timestamp_ms = event.source.timestamp_ms
                    runtime_state.last_seen_frame = event.source.frame_index
                change = self._advance_clear(event, pattern, subject_key)
                if change is not None:
                    pattern_events.append(change)

            for subject_key, evidence in evidence_by_subject.items():
                runtime_state = self._state.get((pattern.id, subject_key))
                if runtime_state is not None and self._memory_covers(
                    event, pattern, runtime_state
                ):
                    # Cobertura reciente: tratar como cubierto (sin refrescar la
                    # memoria, que solo se renueva con cobertura real).
                    runtime_state.last_seen_timestamp_ms = event.source.timestamp_ms
                    runtime_state.last_seen_frame = event.source.frame_index
                    change = self._advance_clear(event, pattern, subject_key)
                    if change is not None:
                        pattern_events.append(change)
                    continue
                change = self._advance_hit(event, pattern, subject_key, evidence)
                if change is None:
                    continue
                pattern_events.append(change)
                if change.state == "confirmed":
                    alert = self._maybe_alert(event, pattern, change)
                    if alert is not None:
                        alerts.append(alert)

            pattern_events.extend(
                self._expire_absent_subjects(event, pattern, result.observed_subject_keys)
            )

        return PatternEngineResult(
            pattern_events=pattern_events,
            alerts=alerts,
            evidences_count=evidences_count,
            subjects_count=subjects_count,
            degradation_causes=degradation_causes,
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
        runtime_state.last_seen_timestamp_ms = event.source.timestamp_ms
        runtime_state.last_seen_frame = event.source.frame_index

        if runtime_state.state in {"inactive", "resolved"}:
            runtime_state.hit_count = 0
            runtime_state.first_hit_timestamp_ms = event.source.timestamp_ms
            # Nuevo episodio: el maximo episodico arranca de cero.
            runtime_state.max_subjects_in_evidence = 0

        if runtime_state.first_hit_timestamp_ms is None:
            runtime_state.first_hit_timestamp_ms = event.source.timestamp_ms

        if runtime_state.first_evidence_monotonic_ms is None:
            runtime_state.first_evidence_monotonic_ms = self._current_ts_receive_ms
            runtime_state.first_evidence_unit_id = event.unit_id
            runtime_state.first_evidence_frame_index = event.source.frame_index

        runtime_state.hit_count += 1
        runtime_state.clear_count = 0
        runtime_state.clear_started_timestamp_ms = None
        # `subjects_in_evidence` siempre viene poblado (>=1) por el evaluador:
        # una evidencia existe solo si hay al menos un sujeto descubierto.
        runtime_state.max_subjects_in_evidence = max(
            runtime_state.max_subjects_in_evidence, evidence.subjects_in_evidence
        )

        if runtime_state.state in {"inactive", "resolved"}:
            runtime_state.state = (
                "confirmed"
                if self._confirmation_met(event, pattern, runtime_state)
                else "candidate"
            )
        elif runtime_state.state == "candidate":
            if self._confirmation_met(event, pattern, runtime_state):
                runtime_state.state = "confirmed"
        elif runtime_state.state == "confirmed":
            runtime_state.state = "sustained"

        if previous == runtime_state.state:
            return None
        return self._make_state_event(
            event,
            pattern,
            subject_key,
            previous,
            runtime_state.state,
            evidence,
            subjects_in_evidence_max=runtime_state.max_subjects_in_evidence,
            first_evidence_ms=runtime_state.first_evidence_monotonic_ms,
            first_evidence_unit_id=runtime_state.first_evidence_unit_id,
            first_evidence_frame_index=runtime_state.first_evidence_frame_index,
        )

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

        runtime_state.last_seen_timestamp_ms = event.source.timestamp_ms
        runtime_state.last_seen_frame = event.source.frame_index

        if runtime_state.clear_count == 0:
            runtime_state.clear_started_timestamp_ms = event.source.timestamp_ms
        if runtime_state.clear_started_timestamp_ms is None:
            runtime_state.clear_started_timestamp_ms = event.source.timestamp_ms

        runtime_state.clear_count += 1
        if not self._resolution_met(event, pattern, runtime_state):
            return None

        previous = runtime_state.state
        episode_max_subjects_in_evidence = runtime_state.max_subjects_in_evidence
        episode_first_evidence_ms = runtime_state.first_evidence_monotonic_ms
        episode_first_evidence_unit_id = runtime_state.first_evidence_unit_id
        episode_first_evidence_frame_index = runtime_state.first_evidence_frame_index
        runtime_state.state = "resolved"
        runtime_state.hit_count = 0
        runtime_state.clear_count = 0
        runtime_state.first_hit_timestamp_ms = None
        runtime_state.clear_started_timestamp_ms = None
        runtime_state.first_evidence_monotonic_ms = None
        runtime_state.first_evidence_unit_id = None
        runtime_state.first_evidence_frame_index = None
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
        return self._make_state_event(
            event,
            pattern,
            subject_key,
            previous,
            "resolved",
            placeholder,
            subjects_in_evidence_max=episode_max_subjects_in_evidence,
            first_evidence_ms=episode_first_evidence_ms,
            first_evidence_unit_id=episode_first_evidence_unit_id,
            first_evidence_frame_index=episode_first_evidence_frame_index,
        )

    def _memory_covers(
        self,
        event: DetectionEvent,
        pattern: PatternDefinition,
        runtime_state: PatternRuntimeState,
    ) -> bool:
        """True si el sujeto tuvo cobertura real dentro de la ventana de memoria."""
        if not _memory_applicable(pattern):
            return False
        memory_ms = pattern.timing.coverage_memory_ms
        memory_frames = pattern.timing.coverage_memory_frames
        if (
            memory_ms is not None
            and event.source.timestamp_ms is not None
            and runtime_state.last_covered_timestamp_ms is not None
        ):
            elapsed = event.source.timestamp_ms - runtime_state.last_covered_timestamp_ms
            return elapsed <= memory_ms
        if (
            memory_frames is not None
            and event.source.frame_index is not None
            and runtime_state.last_covered_frame is not None
        ):
            return event.source.frame_index - runtime_state.last_covered_frame <= memory_frames
        return False

    def _expire_absent_subjects(
        self,
        event: DetectionEvent,
        pattern: PatternDefinition,
        observed_subject_keys: set[str],
    ) -> list[PatternStateChanged]:
        """Resuelve sujetos activos que dejaron de observarse mas alla del timeout."""
        timeout_ms = pattern.timing.subject_absent_timeout_ms
        timeout_frames = pattern.timing.subject_absent_timeout_frames
        if timeout_ms is None and timeout_frames is None:
            return []

        changes: list[PatternStateChanged] = []
        stale_keys: list[tuple[str, str]] = []
        for (pattern_id, subject_key), runtime_state in self._state.items():
            if pattern_id != pattern.id or subject_key in observed_subject_keys:
                continue
            if runtime_state.state in {"inactive", "resolved"}:
                # Entradas sin condicion activa (p. ej. creadas por la memoria de
                # cobertura): purgarlas al expirar para acotar el estado.
                if self._absence_exceeded(event, runtime_state, timeout_ms, timeout_frames):
                    stale_keys.append((pattern_id, subject_key))
                continue
            if not self._absence_exceeded(event, runtime_state, timeout_ms, timeout_frames):
                continue

            previous = runtime_state.state
            episode_max_subjects_in_evidence = runtime_state.max_subjects_in_evidence
            episode_first_evidence_ms = runtime_state.first_evidence_monotonic_ms
            episode_first_evidence_unit_id = runtime_state.first_evidence_unit_id
            episode_first_evidence_frame_index = runtime_state.first_evidence_frame_index
            runtime_state.state = "resolved"
            runtime_state.hit_count = 0
            runtime_state.clear_count = 0
            runtime_state.first_hit_timestamp_ms = None
            runtime_state.clear_started_timestamp_ms = None
            runtime_state.first_evidence_monotonic_ms = None
            runtime_state.first_evidence_unit_id = None
            runtime_state.first_evidence_frame_index = None
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
                rationale="El sujeto dejo de observarse antes de resolver la condicion.",
            )
            changes.append(
                self._make_state_event(
                    event,
                    pattern,
                    subject_key,
                    previous,
                    "resolved",
                    placeholder,
                    subjects_in_evidence_max=episode_max_subjects_in_evidence,
                    first_evidence_ms=episode_first_evidence_ms,
                    first_evidence_unit_id=episode_first_evidence_unit_id,
                    first_evidence_frame_index=episode_first_evidence_frame_index,
                )
            )
        for key in stale_keys:
            del self._state[key]
        return changes

    def _absence_exceeded(
        self,
        event: DetectionEvent,
        runtime_state: PatternRuntimeState,
        timeout_ms: float | None,
        timeout_frames: int | None,
    ) -> bool:
        if (
            timeout_ms is not None
            and event.source.timestamp_ms is not None
            and runtime_state.last_seen_timestamp_ms is not None
        ):
            return event.source.timestamp_ms - runtime_state.last_seen_timestamp_ms >= timeout_ms
        if (
            timeout_frames is not None
            and event.source.frame_index is not None
            and runtime_state.last_seen_frame is not None
        ):
            return event.source.frame_index - runtime_state.last_seen_frame >= timeout_frames
        return False

    def _confirmation_met(
        self,
        event: DetectionEvent,
        pattern: PatternDefinition,
        runtime_state: PatternRuntimeState,
    ) -> bool:
        if (
            pattern.timing.confirm_after_ms is not None
            and event.source.timestamp_ms is not None
            and runtime_state.first_hit_timestamp_ms is not None
        ):
            elapsed_ms = event.source.timestamp_ms - runtime_state.first_hit_timestamp_ms
            return elapsed_ms >= pattern.timing.confirm_after_ms
        return runtime_state.hit_count >= pattern.timing.confirm_after_frames

    def _resolution_met(
        self,
        event: DetectionEvent,
        pattern: PatternDefinition,
        runtime_state: PatternRuntimeState,
    ) -> bool:
        if (
            pattern.timing.resolve_after_ms is not None
            and event.source.timestamp_ms is not None
            and runtime_state.clear_started_timestamp_ms is not None
        ):
            elapsed_ms = event.source.timestamp_ms - runtime_state.clear_started_timestamp_ms
            return elapsed_ms >= pattern.timing.resolve_after_ms
        return runtime_state.clear_count >= pattern.timing.resolve_after_frames

    def _make_state_event(
        self,
        event: DetectionEvent,
        pattern: PatternDefinition,
        subject_key: str,
        previous_state: str,
        state: str,
        evidence: PatternEvidence,
        subjects_in_evidence_max: int | None = None,
        first_evidence_ms: float | None = None,
        first_evidence_unit_id: str | None = None,
        first_evidence_frame_index: int | None = None,
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
            subjects_in_evidence_max=subjects_in_evidence_max,
            first_evidence_ms=first_evidence_ms,
            first_evidence_unit_id=first_evidence_unit_id,
            first_evidence_frame_index=first_evidence_frame_index,
            experiment_id=self.experiment_id,
        )

    def _maybe_alert(
        self,
        event: DetectionEvent,
        pattern: PatternDefinition,
        change: PatternStateChanged,
    ) -> AlertEvent | None:
        """Emite alerta salvo que un cooldown activo la suprima; registra la ultima."""
        runtime_state = self._state.get((pattern.id, change.subject_key))
        if runtime_state is not None and not self._cooldown_ok(event, pattern, runtime_state):
            return None
        alert = self._make_alert(event, pattern, change, runtime_state)
        if runtime_state is not None:
            runtime_state.last_alert_timestamp_ms = event.source.timestamp_ms
            runtime_state.last_alert_frame = event.source.frame_index
        return alert

    def _cooldown_ok(
        self,
        event: DetectionEvent,
        pattern: PatternDefinition,
        runtime_state: PatternRuntimeState,
    ) -> bool:
        cooldown_ms = pattern.timing.realert_cooldown_ms
        cooldown_frames = pattern.timing.realert_cooldown_frames
        if cooldown_ms is None and cooldown_frames is None:
            return True
        if (
            cooldown_ms is not None
            and event.source.timestamp_ms is not None
            and runtime_state.last_alert_timestamp_ms is not None
        ):
            return event.source.timestamp_ms - runtime_state.last_alert_timestamp_ms >= cooldown_ms
        if (
            cooldown_frames is not None
            and event.source.frame_index is not None
            and runtime_state.last_alert_frame is not None
        ):
            return event.source.frame_index - runtime_state.last_alert_frame >= cooldown_frames
        return True

    def _make_alert(
        self,
        event: DetectionEvent,
        pattern: PatternDefinition,
        change: PatternStateChanged,
        runtime_state: PatternRuntimeState | None,
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
            subjects_in_evidence_max=change.subjects_in_evidence_max,
            # Instante monotonico de escritura de la alerta (spec 40 SS5.2.4).
            alert_registered_ms=time.monotonic() * 1000.0,
            # Hito de primera evidencia del episodio, copiado del mismo
            # runtime_state que uso el PatternStateChanged que confirmo.
            first_evidence_ms=runtime_state.first_evidence_monotonic_ms
            if runtime_state is not None
            else None,
            first_evidence_unit_id=runtime_state.first_evidence_unit_id
            if runtime_state is not None
            else None,
            first_evidence_frame_index=runtime_state.first_evidence_frame_index
            if runtime_state is not None
            else None,
            experiment_id=self.experiment_id,
        )
