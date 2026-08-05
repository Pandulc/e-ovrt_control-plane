"""`TrackingSource`: identidad por sujeto como capacidad del control-plane.

ADR-002 planeaba portar el tracker al **media-plane** para que emitiera `track_id`.
Nunca se ejecutó, y la campaña G1 (doc 89) lo resolvió con una herramienta post-hoc:
+0,141 de F1 sobre las MISMAS detecciones. Esto convierte esa herramienta en una
capacidad de la plataforma, config-driven, como decorador de FUENTE — con lo cual:

  - sirve para `media_jsonl` (DBE) y para `bus` (EBE live) por igual, porque decora
    cualquier `MediaEventSource`; el port al media-plane deja de ser necesario;
  - no toca el pipeline congelado del media-plane a 8 semanas de la defensa;
  - es opt-in (`input.track_persons`), así que ninguna corrida existente cambia.

Invariantes que estos tests fijan:
  - **Un tracker POR `source_id`**: con dos fuentes en el mismo run, mezclar sus
    cajas fabricaría identidades cruzadas entre cámaras.
  - Solo las personas reciben `track_id`; el resto pasa intacto.
  - Los items que no son eventos (errores de fuente) pasan sin tocar.
  - Streaming, en orden de llegada: no reordena ni bufferea (en live no podría).
"""
from eovrt_control.contracts.errors import ErrorEvent
from eovrt_control.contracts.media import DetectionEvent
from eovrt_control.sources.base import MediaEventSource
from eovrt_control.sources.tracking import TrackingSource


def _ev(frame, dets, *, source_id="clip.mp4"):
    return DetectionEvent(
        run_id="r", unit_id=f"u{frame}",
        source={"source_id": source_id, "source_type": "video", "width": 640,
                "height": 480, "frame_index": frame, "timestamp_ms": frame * 33.3},
        model={"name": "m"}, prompts={"prompt_set_id": "p"}, detections=dets,
    )


def _det(label, bbox, conf=0.9):
    return {"detection_id": None, "label": label, "prompt_id": label,
            "confidence": conf, "bbox_xyxy": bbox}


class _FakeSource(MediaEventSource):
    kind = "fake"

    def __init__(self, items):
        self._items = items

    def __iter__(self):
        yield from self._items


def _items(*events):
    return [(i, e, None, float(i)) for i, e in enumerate(events, start=1)]


def _personas(event):
    return [d for d in event.detections if d.label == "person"]


# ---------------------------------------------------------------------------
# Asignación básica
# ---------------------------------------------------------------------------

def test_asigna_track_id_a_las_personas():
    src = TrackingSource(_FakeSource(_items(_ev(0, [_det("person", [0, 0, 50, 100])]))))
    _, event, _, _ = next(iter(src))
    assert _personas(event)[0].track_id == "subject_001"


def test_la_misma_persona_conserva_su_id_entre_frames():
    src = TrackingSource(_FakeSource(_items(
        _ev(0, [_det("person", [0, 0, 50, 100])]),
        _ev(1, [_det("person", [2, 1, 52, 101])]),
    )))
    ids = [_personas(e)[0].track_id for _, e, _, _ in src]
    assert ids[0] == ids[1]


def test_las_clases_que_no_son_persona_pasan_intactas():
    src = TrackingSource(_FakeSource(_items(
        _ev(0, [_det("person", [0, 0, 50, 100]), _det("helmet", [10, 5, 30, 25])]))))
    _, event, _, _ = next(iter(src))
    casco = [d for d in event.detections if d.label == "helmet"][0]
    assert casco.track_id is None
    assert casco.bbox_xyxy == [10, 5, 30, 25]


def test_evento_sin_personas_no_rompe():
    src = TrackingSource(_FakeSource(_items(_ev(0, [_det("helmet", [0, 0, 10, 10])]))))
    _, event, _, _ = next(iter(src))
    assert event.detections[0].label == "helmet"


# ---------------------------------------------------------------------------
# El invariante que más importa: un tracker POR fuente
# ---------------------------------------------------------------------------

def test_cada_fuente_tiene_su_propio_tracker():
    """Con dos cámaras en el mismo run, un tracker ÚNICO mezclaría sus cajas.

    Discriminador: la persona de camB está lejos de la de camA. Con un tracker
    compartido sería un track nuevo (`subject_002`); con un tracker por fuente es el
    PRIMER track de camB (`subject_001`). Que los ids se repitan entre fuentes es
    inocuo: la clave de estado del motor ya namespacea por `source_id` — lo verifica
    el test de abajo.
    """
    src = TrackingSource(_FakeSource(_items(
        _ev(0, [_det("person", [0, 0, 50, 100])], source_id="camA"),
        _ev(0, [_det("person", [500, 0, 550, 100])], source_id="camB"),
        _ev(1, [_det("person", [1, 0, 51, 100])], source_id="camA"),
    )))
    salida = [(e.source.source_id, _personas(e)[0].track_id) for _, e, _, _ in src]
    ids_a = [t for s, t in salida if s == "camA"]
    ids_b = [t for s, t in salida if s == "camB"]
    assert ids_a == ["subject_001", "subject_001"]   # camA mantiene su identidad
    assert ids_b == ["subject_001"]                  # camB arranca su propia numeración


def test_ids_repetidos_entre_fuentes_no_colisionan_en_el_motor():
    """El invariante que hace inocua la repetición: `state_key` incluye `source_id`."""
    from eovrt_control.config import PatternDefinition, PatternRegionConfig
    from eovrt_control.engine.evaluators.spatial_absence import state_key

    pattern = PatternDefinition(
        id="CR-01", name="n", condition_id="CR-01", required_absent_class="helmet",
        granularity="subject",
        region=PatternRegionConfig(type="upper_body", y_min_ratio=0.0,
                                   y_max_ratio=0.45, x_margin_ratio=0.12),
    )
    assert state_key(pattern, "camA", "subject_001") != state_key(pattern, "camB", "subject_001")


# ---------------------------------------------------------------------------
# Transparencia sobre el resto del stream
# ---------------------------------------------------------------------------

def test_los_items_de_error_pasan_sin_tocar():
    err = ErrorEvent(control_run_id="c", message="boom", error_type="X")
    src = TrackingSource(_FakeSource([(1, None, err, None)]))
    items = list(src)
    assert items == [(1, None, err, None)]


def test_conserva_line_number_y_timestamp_de_recepcion():
    src = TrackingSource(_FakeSource([(7, _ev(0, [_det("person", [0, 0, 50, 100])]), None, 123.5)]))
    line_number, _, _, ts = next(iter(src))
    assert (line_number, ts) == (7, 123.5)


def test_no_reordena_el_stream():
    """En live no se puede bufferear: se procesa en orden de llegada."""
    src = TrackingSource(_FakeSource(_items(_ev(2, []), _ev(0, []), _ev(1, []))))
    assert [e.source.frame_index for _, e, _, _ in src] == [2, 0, 1]


def test_expone_el_kind_de_la_fuente_decorada():
    src = TrackingSource(_FakeSource([]))
    assert src.kind == "fake"


# ---------------------------------------------------------------------------
# Config: opt-in, y equivalencia con la campaña G1
# ---------------------------------------------------------------------------

def test_track_persons_es_opt_in_y_default_false():
    from eovrt_control.config import InputSection
    assert InputSection(type="media_jsonl", path="x.jsonl").track_persons is False


def test_reproduce_exactamente_la_herramienta_post_hoc(tmp_path):
    """La campaña G1 (doc 89) se corrió con `tools.track_detections`. Si el
    decorador diera otros ids, el 0,930 dejaría de ser reproducible por la
    plataforma. Con el stream en orden de frame, ambos caminos coinciden."""
    import json
    from eovrt_control.sources.jsonl import JsonlSource
    from eovrt_control.tools.track_detections import track_event_stream

    src_path = tmp_path / "det.jsonl"
    eventos = [_ev(f, [_det("person", [f, 0, 50 + f, 100]),
                       _det("person", [200 + f, 0, 250 + f, 100])]) for f in range(6)]
    src_path.write_text("\n".join(e.model_dump_json() for e in eventos))

    out = tmp_path / "tracked.jsonl"
    track_event_stream(src_path, out)
    esperados = [[d.get("track_id") for d in json.loads(linea)["detections"]
                  if d["label"] == "person"]
                 for linea in out.read_text().splitlines() if linea.strip()]

    obtenidos = [[d.track_id for d in _personas(e)]
                 for _, e, _, _ in TrackingSource(JsonlSource(src_path, "c"))]
    assert obtenidos == esperados


# ---------------------------------------------------------------------------
# Delegación del ciclo de vida (bug atrapado en la revisión crítica de D-90.3)
# ---------------------------------------------------------------------------
# La base define no-ops para close/request_stop/dropped_events. Sin delegación
# explícita, el decorador en live: (1) SILENCIA bus_dropped_events — violación de
# ADR-003, "los drops nunca se silencian"; (2) nunca cierra el socket del bus;
# (3) hace no-op el request_stop, cuya alternativa es la trampa SIGABRT.

class _LifecycleSource(MediaEventSource):
    kind = "lifecycle"

    def __init__(self):
        self.closed = False
        self.stop_requested = False

    def __iter__(self):
        yield from ()

    def close(self):
        self.closed = True

    def request_stop(self):
        self.stop_requested = True

    @property
    def dropped_events(self):
        return 7


def test_close_delega_a_la_fuente_interna():
    inner = _LifecycleSource()
    TrackingSource(inner).close()
    assert inner.closed


def test_request_stop_delega_a_la_fuente_interna():
    inner = _LifecycleSource()
    TrackingSource(inner).request_stop()
    assert inner.stop_requested


def test_dropped_events_delega_y_no_silencia_perdidas_del_bus():
    assert TrackingSource(_LifecycleSource()).dropped_events == 7


def test_frames_hacia_atras_avisan_una_vez_por_fuente(caplog):
    """tools.track_detections ORDENA; el decorador no puede (en live no hay futuro).
    Un jsonl desordenado degradaría la calidad del tracking EN SILENCIO — se avisa."""
    import logging
    src = TrackingSource(_FakeSource(_items(
        _ev(5, [_det("person", [0, 0, 50, 100])]),
        _ev(2, [_det("person", [0, 0, 50, 100])]),
        _ev(1, [_det("person", [0, 0, 50, 100])]),
    )))
    with caplog.at_level(logging.WARNING):
        list(src)
    avisos = [r for r in caplog.records if "hacia atras" in r.message]
    assert len(avisos) == 1


def test_delega_atributos_propios_de_la_fuente_interna():
    """Proxy transparente: el contrato de `MediaEventSource` se delega explicito,
    pero una fuente concreta expone mas (`BusSource.endpoint`, stats). Sin esto, un
    live con `track_persons: true` reventaria con AttributeError en cualquier codigo
    que lea un atributo del bus — y es un camino que no se puede testear a fondo sin
    hardware."""
    class _ConExtras(MediaEventSource):
        kind = "extras"
        endpoint = "tcp://127.0.0.1:5599"

        def __iter__(self):
            yield from ()

    src = TrackingSource(_ConExtras())
    assert src.endpoint == "tcp://127.0.0.1:5599"


def test_un_atributo_inexistente_sigue_siendo_error():
    src = TrackingSource(_FakeSource([]))
    try:
        src.no_existe
    except AttributeError:
        return
    raise AssertionError("un atributo inexistente debe seguir levantando AttributeError")
