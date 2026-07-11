import json

from eovrt_control.contracts.media import DetectionEvent
from eovrt_control.sources.base import MediaEventSource
from eovrt_control.sources.jsonl import JsonlSource
from eovrt_control.sources.media_jsonl import iter_media_jsonl
from eovrt_control.sources.memory import MemorySource


def _event(unit_id: str = "unit-1") -> dict:
    return {
        "run_id": "media-run",
        "unit_id": unit_id,
        "source": {
            "source_id": "image.jpg",
            "source_type": "image",
            "width": 640,
            "height": 480,
        },
        "model": {"name": "mock", "device": "cpu"},
        "prompts": {"prompt_set_id": "cr01_cr02_v1"},
        "detections": [],
    }


def test_jsonl_source_yields_events_with_line_numbers(tmp_path) -> None:
    path = tmp_path / "detections.jsonl"
    path.write_text(
        json.dumps(_event("unit-1")) + "\n" + json.dumps(_event("unit-2")) + "\n",
        encoding="utf-8",
    )

    items = list(JsonlSource(path, "control-run"))

    assert [line for line, _, _, _ in items] == [1, 2]
    assert [event.unit_id for _, event, _, _ in items] == ["unit-1", "unit-2"]
    assert all(error is None for _, _, error, _ in items)


def test_jsonl_source_skips_blank_lines(tmp_path) -> None:
    path = tmp_path / "detections.jsonl"
    path.write_text(json.dumps(_event()) + "\n\n   \n", encoding="utf-8")

    items = list(JsonlSource(path, "control-run"))

    assert len(items) == 1


def test_jsonl_source_reports_unparseable_line_as_unit_error(tmp_path) -> None:
    path = tmp_path / "detections.jsonl"
    path.write_text("{no es json}\n", encoding="utf-8")

    (line_number, event, error, _) = next(iter(JsonlSource(path, "control-run")))

    assert line_number == 1
    assert event is None
    # line_number presente => unidad fallida (cuenta en units_failed).
    assert error.line_number == 1
    assert error.error_type == "JSONDecodeError"
    assert error.control_run_id == "control-run"


def test_jsonl_source_reports_invalid_schema_as_unit_error(tmp_path) -> None:
    path = tmp_path / "detections.jsonl"
    path.write_text(json.dumps({"run_id": "media-run"}) + "\n", encoding="utf-8")

    (_, event, error, _) = next(iter(JsonlSource(path, "control-run")))

    assert event is None
    assert error.error_type == "ValidationError"
    assert error.line_number == 1


def test_jsonl_source_reports_missing_file_as_source_error(tmp_path) -> None:
    path = tmp_path / "no_existe.jsonl"

    items = list(JsonlSource(path, "control-run"))

    assert len(items) == 1
    (_, event, error, _) = items[0]
    assert event is None
    assert error.error_type == "FileNotFoundError"
    # line_number ausente => error de FUENTE, no cuenta en units_failed.
    assert error.line_number is None


def test_jsonl_source_declares_kind_and_zero_drops(tmp_path) -> None:
    path = tmp_path / "detections.jsonl"
    path.write_text(json.dumps(_event()) + "\n", encoding="utf-8")
    source = JsonlSource(path, "control-run")

    assert isinstance(source, MediaEventSource)
    assert source.kind == "jsonl"
    assert source.dropped_events == 0
    source.close()


def test_memory_source_yields_events_in_order() -> None:
    events = [DetectionEvent.model_validate(_event("a")), DetectionEvent.model_validate(_event("b"))]

    items = list(MemorySource(events))

    assert [index for index, _, _, _ in items] == [1, 2]
    assert [event.unit_id for _, event, _, _ in items] == ["a", "b"]
    assert MemorySource(events).kind == "memory"


def test_jsonl_source_stamps_ts_receive(tmp_path) -> None:
    """Task 1: cada item trae el instante monotonico de recepcion en ms."""
    path = tmp_path / "detections.jsonl"
    path.write_text(json.dumps(_event("u-9")) + "\n", encoding="utf-8")

    items = list(JsonlSource(path, "control-run"))

    assert len(items) == 1
    index, event, error, ts_receive_ms = items[0]
    assert event.unit_id == "u-9"
    assert isinstance(ts_receive_ms, float) and ts_receive_ms > 0


def test_iter_media_jsonl_alias_delegates_to_jsonl_source(tmp_path) -> None:
    path = tmp_path / "detections.jsonl"
    path.write_text(json.dumps(_event()) + "\n", encoding="utf-8")

    items = list(iter_media_jsonl(path, "control-run"))

    assert len(items) == 1
    assert items[0][1].unit_id == "unit-1"
