"""Dibuja las cajas de alertas del plano de control sobre frames de video.

Herramienta de inspeccion visual de experimentos (labs). Lee las anotaciones con
`eovrt_control.sinks.alerts_csv` y usa OpenCV para renderizar los frames.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from eovrt_control.sinks.alerts_csv import (
    Annotation,
    read_alert_annotations,
    write_alert_details_csv,
)

cv2 = None

STAGE_CHOICES = ("confirm", "candidate", "both", "all")
IMAGE_EXT_CHOICES = ("jpg", "png")


@dataclass(frozen=True)
class AlertFrameConfig:
    video_path: Path
    alerts_path: Path
    output_dir: Path = Path("alert_frame_previews")
    stage: str = "confirm"
    image_ext: str = "jpg"
    line_thickness: int = 2
    details_csv_path: Path | None = None


@dataclass(frozen=True)
class AlertFrameResult:
    output_dir: Path
    index_path: Path
    details_csv_path: Path
    images_written: int
    annotations_count: int


def require_cv2():
    global cv2
    if cv2 is not None:
        return cv2
    try:
        import cv2 as cv2_module
    except ImportError as exc:  # pragma: no cover - utility script guard
        raise RuntimeError(
            "Missing dependency: opencv-python. Install the labs extra or run "
            "from an environment that provides cv2."
        ) from exc
    cv2 = cv2_module
    return cv2


def clamp_bbox(
    bbox: tuple[float, float, float, float], width: int, height: int
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    return (
        max(0, min(width - 1, round(x1))),
        max(0, min(height - 1, round(y1))),
        max(0, min(width - 1, round(x2))),
        max(0, min(height - 1, round(y2))),
    )


def text_background(
    image,
    text: str,
    origin: tuple[int, int],
    font_scale: float = 0.45,
    thickness: int = 1,
) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    x, y = origin
    (w, h), baseline = cv2.getTextSize(text, font, font_scale, thickness)
    top_left = (x, max(0, y - h - baseline - 4))
    bottom_right = (min(image.shape[1] - 1, x + w + 6), min(image.shape[0] - 1, y + 4))
    cv2.rectangle(image, top_left, bottom_right, (0, 0, 0), -1)
    cv2.putText(image, text, (x + 3, y - 3), font, font_scale, (255, 255, 255), thickness)


def _stage_color(stage: str) -> tuple[int, int, int]:
    if stage == "candidate":
        return (0, 215, 255)
    if stage == "resolved":
        return (0, 180, 0)
    if stage == "sustained":
        return (255, 0, 255)
    return (0, 0, 255)


def _short_subject(subject_id: str) -> str:
    if len(subject_id) <= 34:
        return subject_id
    return f"...{subject_id[-31:]}"


def _label(annotation: Annotation) -> str:
    parts = [
        annotation.stage,
        f"#{annotation.alert_order}" if annotation.alert_order else "",
        annotation.condition_id or annotation.pattern_id,
        _short_subject(annotation.subject_id),
    ]
    if annotation.missing_class:
        parts.append(f"missing={annotation.missing_class}")
    if annotation.severity:
        parts.append(annotation.severity)
    if annotation.confidence is not None:
        parts.append(f"conf={annotation.confidence:.3f}")
    if annotation.orientation:
        parts.append(annotation.orientation)
    return " ".join(part for part in parts if part)


def draw_annotations(image, annotations: list[Annotation], thickness: int) -> None:
    height, width = image.shape[:2]
    for annotation in annotations:
        x1, y1, x2, y2 = clamp_bbox(annotation.bbox_xyxy, width, height)
        color = _stage_color(annotation.stage)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, thickness)

        text_y = y1 - 6 if y1 > 24 else y2 + 20
        text_background(image, _label(annotation), (x1, text_y))


def load_frame(capture, frame_index: int):
    capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    ok, frame = capture.read()
    if ok:
        return frame
    return None


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "unknown"


def draw_alert_frames(config: AlertFrameConfig) -> AlertFrameResult:
    if config.stage not in STAGE_CHOICES:
        raise ValueError(f"Invalid stage: {config.stage}. Expected one of {STAGE_CHOICES}")
    if config.image_ext not in IMAGE_EXT_CHOICES:
        raise ValueError(f"Invalid image extension: {config.image_ext}")
    if not config.video_path.exists():
        raise FileNotFoundError(f"Video not found: {config.video_path}")
    if not config.alerts_path.exists():
        raise FileNotFoundError(f"Alert file not found: {config.alerts_path}")

    require_cv2()
    annotations = read_alert_annotations(config.alerts_path, stage=config.stage)
    if not annotations:
        raise ValueError("No drawable annotations found for the requested filters.")

    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    details_csv_path = config.details_csv_path or output_dir / "alerts_details.csv"
    write_alert_details_csv(annotations, details_csv_path)

    capture = cv2.VideoCapture(str(config.video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {config.video_path}")

    grouped: dict[tuple[str, str, int], list[Annotation]] = {}
    for annotation in annotations:
        key = (annotation.variant, annotation.stage, annotation.frame_index)
        grouped.setdefault(key, []).append(annotation)

    index_path = output_dir / "index.csv"
    index_rows: list[dict[str, str]] = []

    try:
        for (variant, stage, frame_index), group in sorted(grouped.items(), key=lambda item: item[0]):
            frame = load_frame(capture, frame_index)
            if frame is None:
                print(f"warning: could not read frame {frame_index}", file=sys.stderr)
                continue

            draw_annotations(frame, group, config.line_thickness)

            variant_dir = output_dir / safe_name(variant)
            variant_dir.mkdir(parents=True, exist_ok=True)
            second = next((ann.second for ann in group if ann.second is not None), None)
            second_part = "unknown" if second is None else f"{second:07.3f}s"
            output_path = (
                variant_dir / f"{stage}_frame_{frame_index:06d}_{second_part}.{config.image_ext}"
            )
            cv2.imwrite(str(output_path), frame)

            index_rows.append(
                {
                    "variant": variant,
                    "stage": stage,
                    "frame_index": str(frame_index),
                    "second": "" if second is None else f"{second:.6f}",
                    "annotations_count": str(len(group)),
                    "pattern_ids": ";".join(sorted({ann.pattern_id for ann in group if ann.pattern_id})),
                    "condition_ids": ";".join(
                        sorted({ann.condition_id for ann in group if ann.condition_id})
                    ),
                    "risk_conditions": ";".join(
                        sorted({ann.risk_condition for ann in group if ann.risk_condition})
                    ),
                    "severities": ";".join(sorted({ann.severity for ann in group if ann.severity})),
                    "subjects": ";".join(ann.subject_id for ann in group),
                    "missing_classes": ";".join(
                        sorted({ann.missing_class for ann in group if ann.missing_class})
                    ),
                    "output_path": str(output_path),
                }
            )
    finally:
        capture.release()

    with index_path.open("w", encoding="utf-8", newline="") as fh:
        fieldnames = [
            "variant",
            "stage",
            "frame_index",
            "second",
            "annotations_count",
            "pattern_ids",
            "condition_ids",
            "risk_conditions",
            "severities",
            "subjects",
            "missing_classes",
            "output_path",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(index_rows)

    return AlertFrameResult(
        output_dir=output_dir,
        index_path=index_path,
        details_csv_path=details_csv_path,
        images_written=len(index_rows),
        annotations_count=len(annotations),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract video frames referenced by a control-plane alert file and draw "
            "the associated risk-condition subject bboxes."
        )
    )
    parser.add_argument("--video", required=True, type=Path, help="Path to the source video.")
    parser.add_argument(
        "--alerts",
        required=True,
        type=Path,
        help=(
            "CSV or JSONL with alert details. Supports alerts.csv, control alerts.jsonl, "
            "and pattern_events.jsonl."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("alert_frame_previews"),
        help="Directory where annotated frames, index.csv, and alerts_details.csv will be written.",
    )
    parser.add_argument(
        "--stage",
        choices=STAGE_CHOICES,
        default="confirm",
        help="Which stage to draw. Use both for candidate+confirm or all for every JSONL state.",
    )
    parser.add_argument(
        "--image-ext",
        choices=IMAGE_EXT_CHOICES,
        default="jpg",
        help="Output image extension.",
    )
    parser.add_argument(
        "--line-thickness",
        type=int,
        default=2,
        help="Bounding-box line thickness in pixels.",
    )
    parser.add_argument(
        "--details-csv",
        type=Path,
        default=None,
        help="Optional path for the normalized alert-detail CSV.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = draw_alert_frames(
            AlertFrameConfig(
                video_path=args.video,
                alerts_path=args.alerts,
                output_dir=args.output_dir,
                stage=args.stage,
                image_ext=args.image_ext,
                line_thickness=args.line_thickness,
                details_csv_path=args.details_csv,
            )
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Wrote {result.images_written} annotated frame images")
    print(f"Annotations: {result.annotations_count}")
    print(f"Index: {result.index_path}")
    print(f"Alert details CSV: {result.details_csv_path}")
    print(f"Output dir: {result.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
