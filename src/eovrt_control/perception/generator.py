"""Orquestacion de inferencia + escritura de detections.jsonl."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import cv2
from PIL import Image

from eovrt_control.perception.backends import BackendConfig, PerceptionBackend, create_backend
from eovrt_control.perception.events import build_detection_event, serialize_event
from eovrt_control.perception.normalizer import normalize_detections
from eovrt_control.perception.tracking import SimpleIoUTracker, apply_person_tracking
from eovrt_control.perception.weights import DEFAULT_YOLOE_MODEL_ID, DEFAULT_WEIGHTS_FILENAME

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}


@dataclass(frozen=True)
class GenerationConfig:
    input_path: Path
    output_path: Path
    backend: str = "gdino"
    model_id: str | None = None
    device: str = "cuda"
    confidence: float = 0.25
    min_box_area_px: float = 100.0
    track: bool = False
    max_units: int | None = None
    stride: int = 1
    run_id: str | None = None
    source_id: str | None = None
    prompt_set_id: str | None = None


@dataclass(frozen=True)
class GenerationResult:
    run_id: str
    output_path: Path
    units_written: int
    source_type: str


def _default_model_id(backend: str) -> str:
    normalized = backend.strip().lower().replace("_", "-")
    if normalized in {"yolo", "yolo-ppe", "construction-ppe"}:
        return DEFAULT_WEIGHTS_FILENAME
    if normalized in {"yoloe", "yoloe-26s"}:
        return DEFAULT_YOLOE_MODEL_ID
    return "IDEA-Research/grounding-dino-tiny"


def _default_prompt_set_id(backend: str) -> str:
    normalized = backend.strip().lower().replace("_", "-")
    if normalized in {"yoloe", "yoloe-26s"}:
        return "cr01_cr02_bench_v2"
    return "hf_perception_cr01_cr02"


def _default_run_id() -> str:
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"run_{date}_001"


def _iter_image_paths(folder: Path) -> list[Path]:
    paths = [
        path
        for path in sorted(folder.iterdir())
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    if not paths:
        raise FileNotFoundError(f"No hay imagenes en {folder}")
    return paths


def _iter_video_frames(
    video_path: Path,
    *,
    stride: int,
    max_units: int | None,
) -> Iterator[tuple[int, float, Image.Image]]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"No se pudo abrir el video: {video_path}")

    fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    frame_index = -1
    emitted = 0
    try:
        while True:
            ok, frame_bgr = capture.read()
            if not ok:
                break
            frame_index += 1
            if frame_index % stride != 0:
                continue
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(frame_rgb)
            timestamp_ms = frame_index * (1000.0 / fps)
            yield frame_index, timestamp_ms, image
            emitted += 1
            if max_units is not None and emitted >= max_units:
                break
    finally:
        capture.release()


def _write_unit(
    handle,
    *,
    config: GenerationConfig,
    backend: PerceptionBackend,
    run_id: str,
    source_id: str,
    prompt_set_id: str,
    unit_id: str,
    source_type: str,
    frame_index: int | None,
    timestamp_ms: float | None,
    image: Image.Image,
    tracker: SimpleIoUTracker | None = None,
) -> None:
    width, height = image.size
    frame_started = time.perf_counter()

    infer_started = time.perf_counter()
    raw = backend.predict(image)
    raw = apply_person_tracking(raw, tracker)
    inference_ms = (time.perf_counter() - infer_started) * 1000.0

    post_started = time.perf_counter()
    detections = normalize_detections(
        raw,
        width=width,
        height=height,
        model_name=backend.model_name,
        min_confidence=config.confidence,
        min_box_area_px=config.min_box_area_px,
    )
    postprocess_ms = (time.perf_counter() - post_started) * 1000.0

    write_prep_started = time.perf_counter()
    provisional = build_detection_event(
        run_id=run_id,
        unit_id=unit_id,
        source_id=source_id,
        source_type=source_type,
        frame_index=frame_index,
        timestamp_ms=timestamp_ms,
        width=width,
        height=height,
        model_name=backend.model_name,
        model_id=backend.model_id,
        device=backend.resolved_device,
        prompt_set_id=prompt_set_id,
        detections=detections,
        inference_ms=inference_ms,
        postprocess_ms=postprocess_ms,
        write_ms=0.0,
        total_ms=0.0,
    )
    serialize_event(provisional)
    write_ms = (time.perf_counter() - write_prep_started) * 1000.0
    total_ms = (time.perf_counter() - frame_started) * 1000.0

    event = build_detection_event(
        run_id=run_id,
        unit_id=unit_id,
        source_id=source_id,
        source_type=source_type,
        frame_index=frame_index,
        timestamp_ms=timestamp_ms,
        width=width,
        height=height,
        model_name=backend.model_name,
        model_id=backend.model_id,
        device=backend.resolved_device,
        prompt_set_id=prompt_set_id,
        detections=detections,
        inference_ms=inference_ms,
        postprocess_ms=postprocess_ms,
        write_ms=write_ms,
        total_ms=total_ms,
    )
    handle.write(serialize_event(event) + "\n")


def generate_detections_jsonl(config: GenerationConfig) -> GenerationResult:
    input_path = config.input_path.resolve()
    output_path = config.output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    backend_name = config.backend
    model_id = config.model_id or _default_model_id(backend_name)
    prompt_set_id = config.prompt_set_id or _default_prompt_set_id(backend_name)
    backend: PerceptionBackend = create_backend(
        backend_name,
        BackendConfig(
            model_id=model_id,
            device=config.device,
            confidence=config.confidence,
        ),
    )
    backend.load()

    run_id = config.run_id or _default_run_id()
    source_id = config.source_id or input_path.stem
    units_written = 0
    tracker = SimpleIoUTracker() if config.track else None
    if config.track:
        logger.info(
            "Tracking activo: personas emiten detection_id estables (subject_NNN) entre frames"
        )

    with output_path.open("w", encoding="utf-8") as handle:
        if input_path.is_dir():
            source_type = "image_folder"
            image_paths = _iter_image_paths(input_path)
            if config.max_units is not None:
                image_paths = image_paths[: config.max_units]
            for frame_index, image_path in enumerate(image_paths):
                if frame_index % config.stride != 0:
                    continue
                image = Image.open(image_path).convert("RGB")
                _write_unit(
                    handle,
                    config=config,
                    backend=backend,
                    run_id=run_id,
                    source_id=source_id,
                    prompt_set_id=prompt_set_id,
                    unit_id=f"{image_path.stem}_{frame_index:06d}",
                    source_type=source_type,
                    frame_index=frame_index,
                    timestamp_ms=frame_index * 500.0,
                    image=image,
                    tracker=tracker,
                )
                units_written += 1

        elif input_path.suffix.lower() in VIDEO_EXTENSIONS:
            source_type = "video_frame"
            for frame_index, timestamp_ms, image in _iter_video_frames(
                input_path,
                stride=config.stride,
                max_units=config.max_units,
            ):
                _write_unit(
                    handle,
                    config=config,
                    backend=backend,
                    run_id=run_id,
                    source_id=source_id,
                    prompt_set_id=prompt_set_id,
                    unit_id=f"frame_{frame_index:06d}",
                    source_type=source_type,
                    frame_index=frame_index,
                    timestamp_ms=timestamp_ms,
                    image=image,
                    tracker=tracker,
                )
                units_written += 1

        elif input_path.suffix.lower() in IMAGE_EXTENSIONS:
            source_type = "image"
            image = Image.open(input_path).convert("RGB")
            _write_unit(
                handle,
                config=config,
                backend=backend,
                run_id=run_id,
                source_id=source_id,
                prompt_set_id=prompt_set_id,
                unit_id=input_path.stem,
                source_type=source_type,
                frame_index=0,
                timestamp_ms=0.0,
                image=image,
            )
            units_written = 1
        else:
            raise ValueError(
                f"Entrada no soportada: {input_path}. Usar imagen, carpeta o video."
            )

    logger.info("Escritas %s unidades en %s", units_written, output_path)
    return GenerationResult(
        run_id=run_id,
        output_path=output_path,
        units_written=units_written,
        source_type=source_type,
    )
