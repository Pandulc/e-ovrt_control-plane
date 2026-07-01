"""Backends de inferencia para generar fixtures del plano de control."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from eovrt_control.perception.events import RawModelDetection
from eovrt_control.perception.labels import (
    GDINO_PROMPTS,
    YOLOE_PROMPTS,
    normalize_gdino_label,
    normalize_yolo_label,
    to_canonical_label,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackendConfig:
    model_id: str
    device: str = "cpu"
    confidence: float = 0.25
    box_threshold: float = 0.30
    text_threshold: float = 0.25
    iou_threshold: float = 0.50
    image_size: int = 640


class PerceptionBackend(ABC):
    @abstractmethod
    def load(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def predict(self, image: Image.Image) -> list[RawModelDetection]:
        raise NotImplementedError

    @property
    @abstractmethod
    def model_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def model_id(self) -> str:
        raise NotImplementedError

    @property
    def resolved_device(self) -> str:
        return "cpu"


def _resolve_torch_device(requested: str) -> str:
    import torch

    if requested.startswith("cuda") and not torch.cuda.is_available():
        logger.warning("CUDA no disponible; usando CPU")
        return "cpu"
    if requested == "cuda":
        return "cuda:0"
    return requested


class GroundingDinoBackend(PerceptionBackend):
    """Open-vocabulary con prompts person / helmet / safety vest."""

    def __init__(self, config: BackendConfig) -> None:
        self.config = config
        self._processor = None
        self._model = None
        self._resolved_device = config.device

    def load(self) -> None:
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        self._resolved_device = _resolve_torch_device(self.config.device)

        logger.info("Cargando %s en %s", self.config.model_id, self._resolved_device)
        self._processor = AutoProcessor.from_pretrained(self.config.model_id)
        self._model = AutoModelForZeroShotObjectDetection.from_pretrained(self.config.model_id)
        device = self._resolved_device
        if device.startswith("cuda"):
            self._model = self._model.to(device)
        else:
            self._model = self._model.to("cpu")
        self._model.eval()

    def predict(self, image: Image.Image) -> list[RawModelDetection]:
        import torch

        if self._model is None or self._processor is None:
            raise RuntimeError("Backend GDINO no cargado")

        text = ". ".join(GDINO_PROMPTS) + "."
        device = self._resolved_device
        inputs = self._processor(images=image, text=text, return_tensors="pt")
        if device.startswith("cuda"):
            inputs = inputs.to(device)
        with torch.no_grad():
            outputs = self._model(**inputs)

        target_size = (image.size[1], image.size[0])
        processed = self._processor.post_process_grounded_object_detection(
            outputs,
            inputs.input_ids,
            threshold=self.config.box_threshold,
            text_threshold=self.config.text_threshold,
            target_sizes=[target_size],
        )[0]

        detections: list[RawModelDetection] = []
        for label, score, box in zip(
            processed["labels"],
            processed["scores"],
            processed["boxes"],
            strict=False,
        ):
            control_label = normalize_gdino_label(str(label), GDINO_PROMPTS)
            if control_label is None:
                continue
            if float(score) < self.config.confidence:
                continue
            detections.append(
                RawModelDetection(
                    label=control_label,
                    confidence=float(score),
                    bbox_xyxy=[float(v) for v in box.tolist()],
                )
            )
        return detections

    @property
    def model_name(self) -> str:
        return "grounding_dino_hf"

    @property
    def model_id(self) -> str:
        return self.config.model_id

    @property
    def resolved_device(self) -> str:
        return self._resolved_device


class YoloPpeBackend(PerceptionBackend):
    """Detector cerrado construction-site-safety (YOLOv8, pesos publicos)."""

    def __init__(self, config: BackendConfig) -> None:
        self.config = config
        self._model = None
        self._resolved_device = config.device
        self._weights_path: Path | None = None

    def load(self) -> None:
        from ultralytics import YOLO

        from eovrt_control.perception.weights import ensure_yolo_ppe_weights

        self._resolved_device = _resolve_torch_device(self.config.device)
        self._weights_path = ensure_yolo_ppe_weights(self.config.model_id)
        logger.info("Cargando YOLO %s", self._weights_path)
        self._model = YOLO(str(self._weights_path))

    def predict(self, image: Image.Image) -> list[RawModelDetection]:
        if self._model is None:
            raise RuntimeError("Backend YOLO no cargado")

        device = 0 if self._resolved_device.startswith("cuda") else "cpu"
        results = self._model.predict(
            source=np.array(image),
            conf=self.config.confidence,
            device=device,
            verbose=False,
        )
        if not results:
            return []

        result = results[0]
        names = result.names or {}
        detections: list[RawModelDetection] = []
        for box in result.boxes:
            class_id = int(box.cls.item())
            raw_label = names.get(class_id, str(class_id))
            control_label = normalize_yolo_label(str(raw_label))
            if control_label is None:
                continue
            xyxy = box.xyxy[0].tolist()
            detections.append(
                RawModelDetection(
                    label=control_label,
                    confidence=float(box.conf.item()),
                    bbox_xyxy=[float(v) for v in xyxy],
                )
            )
        return detections

    @property
    def model_name(self) -> str:
        return "yolo_construction_ppe"

    @property
    def model_id(self) -> str:
        if self._weights_path is not None:
            return str(self._weights_path)
        return self.config.model_id

    @property
    def resolved_device(self) -> str:
        return self._resolved_device


class YoloeBackend(PerceptionBackend):
    """YOLOE open-vocabulary con prompts person / helmet / vest."""

    def __init__(self, config: BackendConfig) -> None:
        self.config = config
        self._model = None
        self._resolved_device = config.device
        self._weights_path: Path | None = None
        self._prompts_set: list[str] | None = None

    def load(self) -> None:
        from ultralytics import YOLOE

        from eovrt_control.perception.weights import (
            DEFAULT_YOLOE_MODEL_ID,
            ensure_yoloe_weights,
        )

        self._resolved_device = _resolve_torch_device(self.config.device)
        model_ref = self.config.model_id
        if model_ref == DEFAULT_YOLOE_MODEL_ID:
            model_ref = None
        self._weights_path = ensure_yoloe_weights(model_ref)
        logger.info("Cargando YOLOE %s en %s", self._weights_path, self._resolved_device)
        self._model = YOLOE(str(self._weights_path))

    def _ensure_classes(self) -> None:
        if self._model is None:
            raise RuntimeError("Backend YOLOE no cargado")
        if self._prompts_set != YOLOE_PROMPTS:
            logger.info("Configurando clases YOLOE: %s", YOLOE_PROMPTS)
            self._model.set_classes(YOLOE_PROMPTS)
            self._prompts_set = list(YOLOE_PROMPTS)

    def predict(self, image: Image.Image) -> list[RawModelDetection]:
        if self._model is None:
            raise RuntimeError("Backend YOLOE no cargado")

        self._ensure_classes()
        device = self._resolved_device
        predict_device = 0 if device.startswith("cuda") else "cpu"
        results = self._model.predict(
            source=image,
            conf=self.config.confidence,
            iou=self.config.iou_threshold,
            imgsz=self.config.image_size,
            device=predict_device,
            verbose=False,
        )
        if not results:
            return []

        result = results[0]
        if result.boxes is None:
            return []

        names = result.names or {}
        detections: list[RawModelDetection] = []
        for box in result.boxes:
            class_id = int(box.cls.item())
            raw_label = names.get(class_id, str(class_id))
            control_label = to_canonical_label(str(raw_label), backend="yoloe")
            if control_label is None:
                continue
            xyxy = box.xyxy[0].tolist()
            detections.append(
                RawModelDetection(
                    label=control_label,
                    confidence=float(box.conf.item()),
                    bbox_xyxy=[float(v) for v in xyxy],
                )
            )
        return detections

    @property
    def model_name(self) -> str:
        return "yoloe-26s"

    @property
    def model_id(self) -> str:
        from eovrt_control.perception.weights import DEFAULT_YOLOE_MODEL_ID

        if self._weights_path is not None:
            return DEFAULT_YOLOE_MODEL_ID
        return self.config.model_id

    @property
    def resolved_device(self) -> str:
        return self._resolved_device


def create_backend(name: str, config: BackendConfig) -> PerceptionBackend:
    normalized = name.strip().lower().replace("_", "-")
    if normalized in {"gdino", "grounding-dino"}:
        return GroundingDinoBackend(config)
    if normalized in {"yolo", "yolo-ppe", "construction-ppe"}:
        return YoloPpeBackend(config)
    if normalized in {"yoloe", "yoloe-26s"}:
        return YoloeBackend(config)
    raise ValueError(
        f"Backend no soportado: {name}. Usar gdino, yolo-ppe o yoloe."
    )
