"""Orquestacion de inferencia + escritura de detections.jsonl."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from eovrt_labs.perception.backends import BackendConfig, PerceptionBackend, create_backend
from eovrt_labs.perception.events import build_detection_event, serialize_event
from eovrt_control.contracts.media import DetectionEvent
from eovrt_labs.perception.normalizer import normalize_detections, postprocess_raw_detections
from eovrt_labs.perception.tracking import SimpleIoUTracker, apply_person_tracking
from eovrt_labs.perception.tuning import TuningConfig, load_tuning_config
from eovrt_labs.perception.weights import DEFAULT_YOLOE_MODEL_ID, DEFAULT_WEIGHTS_FILENAME

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}

__all__ = [
    "GenerationConfig",
    "GenerationResult",
    "TuningConfig",
    "generate_detections_jsonl",
    "load_tuning_config",
]


@dataclass(frozen=True)
class GenerationConfig:
    input_path: Path
    output_path: Path
    backend: str = "gdino"
    model_id: str | None = None
    device: str = "cuda"
    confidence: float = 0.25
    track: bool = False
    stride: int = 1
    max_units: int | None = None
    run_id: str | None = None
    source_id: str | None = None
    prompt_set_id: str | None = None
    progress_interval: int = 25
    strict_contract_validation: bool = True
    tuning: TuningConfig = field(default_factory=TuningConfig)


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


def _class_confidence_thresholds(config: GenerationConfig) -> dict[str, float]:
    return {
        "person": config.tuning.person_confidence,
        "helmet": config.tuning.helmet_confidence,
        "vest": config.tuning.vest_confidence,
    }


def _nms_iou_thresholds(config: GenerationConfig) -> dict[str, float]:
    return {
        "person": config.tuning.nms_iou_person,
        "helmet": config.tuning.nms_iou_epp,
        "vest": config.tuning.nms_iou_epp,
    }


def _safe_normalized_histogram(values: np.ndarray, bins: int, value_range: tuple[int, int]) -> np.ndarray:
    hist, _ = np.histogram(values, bins=bins, range=value_range)
    hist = hist.astype("float32")
    total = float(hist.sum())
    if total <= 0.0:
        return hist
    return hist / total


def _person_appearance_feature(
    image: Image.Image,
    box_xyxy: list[float],
) -> list[float] | None:
    width, height = image.size
    x1, y1, x2, y2 = box_xyxy
    box_w = max(0.0, x2 - x1)
    box_h = max(0.0, y2 - y1)
    if box_w < 8.0 or box_h < 16.0:
        return None

    crop_x1 = int(max(0.0, min(float(width), x1 + box_w * 0.10)))
    crop_x2 = int(max(0.0, min(float(width), x2 - box_w * 0.10)))
    crop_y1 = int(max(0.0, min(float(height), y1 + box_h * 0.18)))
    crop_y2 = int(max(0.0, min(float(height), y1 + box_h * 0.85)))
    if crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
        return None

    rgb = np.asarray(image, dtype=np.uint8)
    crop = rgb[crop_y1:crop_y2, crop_x1:crop_x2]
    if crop.size == 0:
        return None

    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    h_hist = _safe_normalized_histogram(hsv[:, :, 0], 12, (0, 180))
    s_hist = _safe_normalized_histogram(hsv[:, :, 1], 4, (0, 256))
    v_hist = _safe_normalized_histogram(hsv[:, :, 2], 4, (0, 256))
    flat_rgb = crop.reshape(-1, 3).astype("float32") / 255.0
    rgb_mean = flat_rgb.mean(axis=0)
    rgb_std = flat_rgb.std(axis=0)

    feature = np.concatenate([h_hist, s_hist, v_hist, rgb_mean, rgb_std])
    norm = float(np.linalg.norm(feature))
    if norm <= 0.0:
        return None
    return (feature / norm).astype("float32").tolist()


def _person_appearance_features(
    image: Image.Image,
    raw: list,
    config: GenerationConfig,
) -> list[list[float] | None] | None:
    if not config.track or not config.tuning.track_appearance:
        return None
    persons = [item for item in raw if item.label == "person"]
    if not persons:
        return []
    return [_person_appearance_feature(image, item.bbox_xyxy) for item in persons]


def _iter_image_paths(folder: Path) -> list[Path]:
    paths = [
        path
        for path in sorted(folder.iterdir())
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    if not paths:
        raise FileNotFoundError(f"No hay imagenes en {folder}")
    return paths


def _select_strided(items: list, *, stride: int, max_units: int | None) -> list:
    """Aplica stride primero y luego el tope max_units (semantica unica img/video)."""
    strided = items[::stride] if stride > 1 else list(items)
    if max_units is not None:
        strided = strided[:max_units]
    return strided


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
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    logger.info(
        "Video abierto: path=%s fps=%.2f frames=%s stride=%s max_units=%s",
        video_path,
        fps,
        frame_count or "desconocido",
        stride,
        max_units if max_units is not None else "sin limite",
    )
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
    validate_contract: bool = False,
) -> int:
    width, height = image.size
    frame_started = time.perf_counter()

    infer_started = time.perf_counter()
    raw = backend.predict(image)
    inference_ms = (time.perf_counter() - infer_started) * 1000.0

    post_started = time.perf_counter()
    raw = postprocess_raw_detections(
        raw,
        width=width,
        height=height,
        min_confidence=config.confidence,
        min_box_area_px=config.tuning.min_box_area_px,
        class_confidence_thresholds=_class_confidence_thresholds(config),
        nms_iou_thresholds=_nms_iou_thresholds(config),
    )
    appearance_features = _person_appearance_features(image, raw, config)
    raw = apply_person_tracking(
        raw,
        tracker,
        appearance_features=appearance_features,
        frame_index=frame_index,
        timestamp_ms=timestamp_ms,
    )
    postprocess_ms = (time.perf_counter() - post_started) * 1000.0

    normalize_started = time.perf_counter()
    detections = normalize_detections(
        raw,
        width=width,
        height=height,
        model_name=backend.model_name,
        min_confidence=0.0,
        min_box_area_px=0.0,
        apply_postprocessing=False,
    )
    normalize_ms = (time.perf_counter() - normalize_started) * 1000.0

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
        normalize_ms=normalize_ms,
        inference_ms=inference_ms,
        postprocess_ms=postprocess_ms,
        write_ms=0.0,
        total_ms=total_ms,
    )
    if validate_contract:
        DetectionEvent.model_validate(event.model_dump())
    handle.write(serialize_event(event) + "\n")
    return len(detections)


def _should_log_progress(units_written: int, config: GenerationConfig) -> bool:
    if units_written == 1:
        return True
    if config.progress_interval <= 0:
        return False
    return units_written % config.progress_interval == 0


def _log_progress(
    *,
    units_written: int,
    config: GenerationConfig,
    unit_id: str,
    detections_count: int,
) -> None:
    if not _should_log_progress(units_written, config):
        return
    if config.max_units is None:
        logger.info(
            "Progreso: unidades=%s ultima_unidad=%s detecciones=%s",
            units_written,
            unit_id,
            detections_count,
        )
        return
    logger.info(
        "Progreso: unidades=%s/%s ultima_unidad=%s detecciones=%s",
        units_written,
        config.max_units,
        unit_id,
        detections_count,
    )


def generate_detections_jsonl(config: GenerationConfig) -> GenerationResult:
    input_path = config.input_path.resolve()
    output_path = config.output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Preparando generacion de detecciones")
    logger.info("Entrada: %s", input_path)
    logger.info("Salida JSONL: %s", output_path)

    backend_name = config.backend
    model_id = config.model_id or _default_model_id(backend_name)
    prompt_set_id = config.prompt_set_id or _default_prompt_set_id(backend_name)
    logger.info(
        "Configuracion: backend=%s model_id=%s device=%s stride=%s max_units=%s track=%s",
        backend_name,
        model_id,
        config.device,
        config.stride,
        config.max_units if config.max_units is not None else "sin limite",
        config.track,
    )
    backend: PerceptionBackend = create_backend(
        backend_name,
        BackendConfig(
            model_id=model_id,
            device=config.device,
            confidence=config.confidence,
            iou_threshold=config.tuning.model_iou,
            image_size=config.tuning.image_size,
        ),
    )
    logger.info("Cargando backend de inferencia")
    backend.load()
    logger.info(
        "Backend listo: model_name=%s model_id=%s device=%s",
        backend.model_name,
        backend.model_id,
        backend.resolved_device,
    )

    run_id = config.run_id or _default_run_id()
    source_id = config.source_id or input_path.stem
    units_written = 0
    logger.info(
        "Run preparado: run_id=%s source_id=%s prompt_set_id=%s",
        run_id,
        source_id,
        prompt_set_id,
    )
    tracker = (
        SimpleIoUTracker(
            iou_threshold=config.tuning.track_iou_threshold,
            max_lost_ms=config.tuning.track_max_lost_ms,
            max_lost_frames=config.tuning.track_max_lost_frames,
            center_gate_ratio=config.tuning.track_center_gate_ratio,
            area_ratio_min=config.tuning.track_area_ratio_min,
            min_score=config.tuning.track_min_score,
            appearance_enabled=config.tuning.track_appearance,
            appearance_weight=config.tuning.track_appearance_weight,
            appearance_min_similarity=config.tuning.track_appearance_min_similarity,
        )
        if config.track
        else None
    )
    if config.track:
        logger.info(
            (
                "Tracking activo: iou=%.2f max_lost_ms=%.0f max_lost_frames=%s "
                "center_gate=%.2f area_ratio_min=%.2f appearance=%s"
            ),
            config.tuning.track_iou_threshold,
            config.tuning.track_max_lost_ms,
            config.tuning.track_max_lost_frames,
            config.tuning.track_center_gate_ratio,
            config.tuning.track_area_ratio_min,
            config.tuning.track_appearance,
        )

    interval_ms = config.tuning.image_folder_frame_interval_ms

    with output_path.open("w", encoding="utf-8") as handle:
        if input_path.is_dir():
            source_type = "image_folder"
            image_paths = _iter_image_paths(input_path)
            selected = _select_strided(
                list(enumerate(image_paths)),
                stride=config.stride,
                max_units=config.max_units,
            )
            logger.info(
                "Procesando carpeta de imagenes: imagenes=%s seleccionadas=%s stride=%s",
                len(image_paths),
                len(selected),
                config.stride,
            )
            for frame_index, image_path in selected:
                image = Image.open(image_path).convert("RGB")
                unit_id = f"{image_path.stem}_{frame_index:06d}"
                detections_count = _write_unit(
                    handle,
                    config=config,
                    backend=backend,
                    run_id=run_id,
                    source_id=source_id,
                    prompt_set_id=prompt_set_id,
                    unit_id=unit_id,
                    source_type=source_type,
                    frame_index=frame_index,
                    timestamp_ms=frame_index * interval_ms,
                    image=image,
                    tracker=tracker,
                    validate_contract=(units_written == 0),
                )
                units_written += 1
                _log_progress(
                    units_written=units_written,
                    config=config,
                    unit_id=unit_id,
                    detections_count=detections_count,
                )

        elif input_path.suffix.lower() in VIDEO_EXTENSIONS:
            source_type = "video_frame"
            logger.info("Procesando video")
            for frame_index, timestamp_ms, image in _iter_video_frames(
                input_path,
                stride=config.stride,
                max_units=config.max_units,
            ):
                unit_id = f"frame_{frame_index:06d}"
                detections_count = _write_unit(
                    handle,
                    config=config,
                    backend=backend,
                    run_id=run_id,
                    source_id=source_id,
                    prompt_set_id=prompt_set_id,
                    unit_id=unit_id,
                    source_type=source_type,
                    frame_index=frame_index,
                    timestamp_ms=timestamp_ms,
                    image=image,
                    tracker=tracker,
                    validate_contract=(units_written == 0),
                )
                units_written += 1
                _log_progress(
                    units_written=units_written,
                    config=config,
                    unit_id=unit_id,
                    detections_count=detections_count,
                )

        elif input_path.suffix.lower() in IMAGE_EXTENSIONS:
            source_type = "image"
            logger.info("Procesando imagen unica")
            image = Image.open(input_path).convert("RGB")
            detections_count = _write_unit(
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
                validate_contract=True,
            )
            units_written = 1
            _log_progress(
                units_written=units_written,
                config=config,
                unit_id=input_path.stem,
                detections_count=detections_count,
            )
        else:
            raise ValueError(
                f"Entrada no soportada: {input_path}. Usar imagen, carpeta o video."
            )

    logger.info(
        "Generacion finalizada: unidades=%s source_type=%s salida=%s",
        units_written,
        source_type,
        output_path,
    )
    return GenerationResult(
        run_id=run_id,
        output_path=output_path,
        units_written=units_written,
        source_type=source_type,
    )
