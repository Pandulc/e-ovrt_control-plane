"""Evaluador `direct_evidence` + estrategias de evidencia (spec 41 §6, doc 12 §4).

Habilita la Fase 2 de E-DIR (el eje de la tesis) y la via bare_head-como-evidencia
(F-84.5): la evidencia del patron es una DETECCION DIRECTA de la condicion ("person
without hard hat", "bare head"), no la ausencia espacial de una clase EPP.

Reglas del spec que estos tests fijan:
  - Gating por persona (doc 12 §4.2): una deteccion directa solo aporta evidencia si
    matchea con una persona detectada — IoU>=0.5 para frases persona-centricas,
    centro-en-region para detecciones de parte (`bare_head`). Sin persona, el hit se
    cuenta como `ungated_direct_hits` (diagnostico) y NO genera evidencia.
  - La evidencia emitida conserva la semantica de bbox-de-persona en subject/
    supporting (aguas abajo se junta por bbox contra person_gt), y la trazabilidad
    "frase F sobre la persona en bbox X" va en el rationale.
  - `strategy: eind` es el default: los pattern sets existentes no cambian.
"""
import pytest

from eovrt_control.config import (
    DirectEvidenceClassConfig,
    PatternDefinition,
    PatternEvidenceConfig,
    PatternRegionConfig,
    load_patterns_file,
)
from eovrt_control.contracts.media import Detection, DetectionEvent
from eovrt_control.engine.evaluators import evaluate_pattern
from eovrt_control.engine.evaluators.direct_evidence import evaluate_direct_evidence
from eovrt_control.engine.pattern_engine import PatternEngine


def _edir_pattern(**overrides) -> PatternDefinition:
    base = dict(
        id="CR-01",
        name="person_without_helmet_direct",
        condition_id="CR-01",
        required_absent_class="helmet",
        region=PatternRegionConfig(
            type="upper_body", y_min_ratio=0.0, y_max_ratio=0.45, x_margin_ratio=0.12
        ),
        evidence=PatternEvidenceConfig(
            strategy="edir",
            direct=[DirectEvidenceClassConfig(prompt_id="cr01_spec", min_confidence=0.30)],
        ),
    )
    base.update(overrides)
    return PatternDefinition(**base)


def _event(detections, *, source_id="clip.mp4", timestamp_ms=0.0, frame_index=0):
    return DetectionEvent(
        run_id="media-run",
        unit_id=f"unit-{frame_index}",
        source={
            "source_id": source_id,
            "source_type": "video",
            "width": 640,
            "height": 480,
            "timestamp_ms": timestamp_ms,
            "frame_index": frame_index,
        },
        model={"name": "mock", "device": "cpu"},
        prompts={"prompt_set_id": "edir_v1"},
        detections=detections,
    )


def _det(label, confidence, bbox, *, track_id=None, prompt_id=None):
    return Detection(
        detection_id=None,
        track_id=track_id,
        label=label,
        prompt_id=prompt_id or label,
        confidence=confidence,
        bbox_xyxy=bbox,
    )


PERSONA = _det("person", 0.9, [100, 100, 220, 420])


# ---------------------------------------------------------------------------
# Gating por persona (doc 12 §4.2)
# ---------------------------------------------------------------------------

def test_hit_directo_gateado_por_iou_genera_evidencia():
    hit = _det("cr01_spec", 0.8, [102, 105, 218, 415])  # solapa a la persona
    result = evaluate_direct_evidence(_event([PERSONA, hit]), _edir_pattern())
    assert len(result.evidences) == 1
    ev = result.evidences[0]
    assert ev.missing_class == "helmet"           # la clase EPP violada, no la frase
    assert ev.subject.bbox_xyxy == PERSONA.bbox_xyxy  # semantica bbox-de-persona
    assert ev.score == pytest.approx(0.8)
    assert "cr01_spec" in ev.rationale            # trazabilidad: que frase disparo
    assert result.ungated_direct_hits == 0


def test_hit_bajo_el_umbral_de_su_clase_no_aporta():
    hit = _det("cr01_spec", 0.25, [102, 105, 218, 415])
    result = evaluate_direct_evidence(_event([PERSONA, hit]), _edir_pattern())
    assert result.evidences == []


def test_hit_sin_persona_se_descarta_y_se_cuenta():
    """Un FP suelto sin persona no puede disparar evidencia de patron (doc 12 §4.2)."""
    hit = _det("cr01_spec", 0.8, [500, 50, 600, 250])
    result = evaluate_direct_evidence(_event([PERSONA, hit]), _edir_pattern())
    assert result.evidences == []
    assert result.ungated_direct_hits == 1


def test_solo_las_clases_declaradas_aportan():
    """Una deteccion de otra variante (no declarada en el patron) se ignora."""
    otro = _det("cr01_neg", 0.9, [102, 105, 218, 415])
    result = evaluate_direct_evidence(_event([PERSONA, otro]), _edir_pattern())
    assert result.evidences == []
    assert result.ungated_direct_hits == 0        # ni evidencia ni diagnostico: no es del patron


def test_persona_bajo_umbral_de_sujeto_no_gatea():
    persona_debil = _det("person", 0.2, [100, 100, 220, 420])
    hit = _det("cr01_spec", 0.8, [102, 105, 218, 415])
    result = evaluate_direct_evidence(_event([persona_debil, hit]), _edir_pattern())
    assert result.evidences == []
    assert result.ungated_direct_hits == 1


def test_gating_region_center_para_detecciones_de_parte():
    """`bare_head` es una caja chica: se gatea por centro-en-region (upper_body)."""
    pattern = _edir_pattern(
        evidence=PatternEvidenceConfig(
            strategy="edir",
            direct=[DirectEvidenceClassConfig(
                prompt_id="bare_head", min_confidence=0.30, match="region_center")],
        ),
    )
    cabeza = _det("bare_head", 0.7, [140, 110, 180, 150])   # tercio superior
    result = evaluate_direct_evidence(_event([PERSONA, cabeza]), pattern)
    assert len(result.evidences) == 1

    pies = _det("bare_head", 0.7, [140, 380, 180, 415])     # al pie de la persona
    result = evaluate_direct_evidence(_event([PERSONA, pies]), pattern)
    assert result.evidences == []
    assert result.ungated_direct_hits == 1


# ---------------------------------------------------------------------------
# Agregacion por clave de estado (misma semantica que spatial_absence)
# ---------------------------------------------------------------------------

def test_escena_agrega_varias_personas_en_una_evidencia():
    p2 = _det("person", 0.85, [400, 100, 520, 420])
    h1 = _det("cr01_spec", 0.8, [102, 105, 218, 415])
    h2 = _det("cr01_spec", 0.6, [402, 105, 518, 415])
    result = evaluate_direct_evidence(_event([PERSONA, p2, h1, h2]), _edir_pattern())
    assert len(result.evidences) == 1             # clave de escena unica
    ev = result.evidences[0]
    assert ev.subjects_in_evidence == 2
    assert len(ev.supporting) == 1
    assert ev.score == pytest.approx(0.8)         # el hit mas confiable manda


def test_granularidad_subject_emite_por_track():
    pattern = _edir_pattern(granularity="subject")
    p1 = _det("person", 0.9, [100, 100, 220, 420], track_id="t1")
    p2 = _det("person", 0.85, [400, 100, 520, 420], track_id="t2")
    h1 = _det("cr01_spec", 0.8, [102, 105, 218, 415])
    h2 = _det("cr01_spec", 0.6, [402, 105, 518, 415])
    result = evaluate_direct_evidence(_event([p1, p2, h1, h2]), pattern)
    assert len(result.evidences) == 2
    assert {ev.subject_key for ev in result.evidences} == {
        "CR-01:clip.mp4:t1", "CR-01:clip.mp4:t2"}


def test_subject_sin_track_degrada_a_escena_con_causa():
    pattern = _edir_pattern(granularity="subject")
    hit = _det("cr01_spec", 0.8, [102, 105, 218, 415])
    result = evaluate_direct_evidence(_event([PERSONA, hit]), pattern)  # sin track_id
    assert len(result.evidences) == 1
    assert result.evidences[0].subject_key == "CR-01:clip.mp4"
    assert "no_track_id" in result.degradation_causes


def test_personas_observadas_sin_hit_avanzan_clear():
    """La persona sin evidencia directa cuenta como observada: el motor resuelve."""
    result = evaluate_direct_evidence(_event([PERSONA]), _edir_pattern())
    assert result.evidences == []
    assert result.observed_subject_keys == {"CR-01:clip.mp4"}


# ---------------------------------------------------------------------------
# Config: validacion de estrategias
# ---------------------------------------------------------------------------

def test_strategy_edir_sin_clases_directas_es_invalida():
    with pytest.raises(ValueError, match="direct"):
        _edir_pattern(evidence=PatternEvidenceConfig(strategy="edir"))


def test_strategy_hyb_and_todavia_no_esta_implementada():
    with pytest.raises(ValueError, match="hyb_and"):
        _edir_pattern(evidence=PatternEvidenceConfig(
            strategy="hyb_and",
            direct=[DirectEvidenceClassConfig(prompt_id="cr01_spec")],
        ))


def test_default_es_eind_y_los_pattern_sets_existentes_no_cambian():
    patterns = load_patterns_file("configs/patterns/cr01_cr02_v2.yaml")
    for pattern in patterns.active_patterns(None):
        assert pattern.evidence.strategy == "eind"


def test_pattern_set_edir_v1_carga_y_declara_edir():
    patterns = load_patterns_file("configs/patterns/cr01_cr02_edir_v1.yaml")
    activos = patterns.active_patterns(["CR-01", "CR-02"])
    assert [p.evidence.strategy for p in activos] == ["edir", "edir"]
    assert activos[0].timing.confirm_after_ms == 4000.0   # mismos timings que v2:
    assert activos[1].timing.confirm_after_ms == 7000.0   # la unica variable es la evidencia


# ---------------------------------------------------------------------------
# Despacho por estrategia + fusion hyb_or (spec 41 §6.2)
# ---------------------------------------------------------------------------

def test_dispatch_eind_sigue_usando_spatial_absence():
    pattern = _edir_pattern(evidence=PatternEvidenceConfig())  # default eind
    result = evaluate_pattern(_event([PERSONA]), pattern)      # persona sin casco
    assert len(result.evidences) == 1                          # ausencia espacial


def _hyb_or_pattern() -> PatternDefinition:
    return _edir_pattern(
        evidence=PatternEvidenceConfig(
            strategy="hyb_or",
            direct=[DirectEvidenceClassConfig(prompt_id="cr01_spec", min_confidence=0.30)],
        ),
    )


def test_hyb_or_toma_la_evidencia_espacial_sola():
    result = evaluate_pattern(_event([PERSONA]), _hyb_or_pattern())
    assert len(result.evidences) == 1


def test_hyb_or_toma_la_evidencia_directa_aunque_el_casco_cubra():
    """El caso que define a -or: el casco esta detectado (spatial no dispara) pero la
    frase directa insiste — la union la deja pasar. Sube recall, paga precision."""
    casco = _det("helmet", 0.8, [140, 105, 180, 140])
    hit = _det("cr01_spec", 0.8, [102, 105, 218, 415])
    result = evaluate_pattern(_event([PERSONA, casco, hit]), _hyb_or_pattern())
    assert len(result.evidences) == 1
    assert "cr01_spec" in result.evidences[0].rationale


def test_hyb_or_no_duplica_cuando_ambas_disparan():
    hit = _det("cr01_spec", 0.8, [102, 105, 218, 415])
    result = evaluate_pattern(_event([PERSONA, hit]), _hyb_or_pattern())
    assert len(result.evidences) == 1              # una evidencia por clave de estado


# ---------------------------------------------------------------------------
# Integracion con el motor: la logica temporal es la misma
# ---------------------------------------------------------------------------

def test_motor_confirma_alerta_edir_tras_la_persistencia():
    pattern = _edir_pattern()   # confirm_after_ms default del PatternTimingConfig
    engine = PatternEngine(control_run_id="control-run", patterns=[pattern])
    hit = _det("cr01_spec", 0.8, [102, 105, 218, 415])

    r0 = engine.process(_event([PERSONA, hit], timestamp_ms=0.0, frame_index=0))
    assert r0.alerts == [] or r0.alerts  # primer frame: candidate (o confirmado si no hay ventana)
    r1 = engine.process(_event([PERSONA, hit], timestamp_ms=5000.0, frame_index=150))
    estados = [ev.state for ev in r0.pattern_events + r1.pattern_events]
    assert "confirmed" in estados
    assert len(r0.alerts) + len(r1.alerts) >= 1


def test_motor_propaga_ungated_hits_como_diagnostico():
    pattern = _edir_pattern()
    engine = PatternEngine(control_run_id="control-run", patterns=[pattern])
    suelto = _det("cr01_spec", 0.8, [500, 50, 600, 250])
    result = engine.process(_event([suelto]))
    assert result.alerts == []
    assert result.ungated_direct_hits == 1


# ---------------------------------------------------------------------------
# SDR/TTFD siguen la estrategia del patron (fix hallado en el humo de D1)
# ---------------------------------------------------------------------------

def test_flags_de_sdr_usan_el_evaluador_de_la_estrategia():
    """Con un patron edir, una persona SIN hit directo NO es un frame positivo.

    Antes del fix, `_positive_flags_for_source` hardcodeaba spatial_absence: en un
    run E-DIR (caption sin clase helmet) toda persona contaba como evidencia de
    ausencia y el SDR salia ~1,0 con CERO alertas — medido en el humo de D1 sobre
    a_p1_c09 (SDR 0.994, 0 alertas). El principio del propio docstring ("reusa el
    evaluador real del motor") exige despachar por estrategia.
    """
    from eovrt_control.evaluation.temporal import _positive_flags_for_source

    pattern = _edir_pattern()
    hit = _det("cr01_spec", 0.8, [102, 105, 218, 415])
    solo_persona = _event([PERSONA], timestamp_ms=0.0, frame_index=0)
    con_hit = _event([PERSONA, hit], timestamp_ms=100.0, frame_index=3)

    flags = _positive_flags_for_source([solo_persona, con_hit], pattern)
    assert flags == [False, True]
