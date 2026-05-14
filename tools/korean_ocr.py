from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageEnhance


DEFAULT_MODEL_DIR = Path("runtime/ocr_models")


@dataclass(frozen=True)
class OCRBlock:
    text: str
    confidence: float
    bbox: tuple[int, int, int, int]
    engine: str = "easyocr"

    @property
    def x(self) -> int:
        return self.bbox[0]

    @property
    def y(self) -> int:
        return self.bbox[1]

    @property
    def w(self) -> int:
        return self.bbox[2]

    @property
    def h(self) -> int:
        return self.bbox[3]

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2


def ocr_image(
    image_path: str | Path,
    *,
    languages: Iterable[str] = ("ko", "en"),
    crop: tuple[int, int, int, int] | None = None,
    scale: int = 1,
    min_confidence: float = 0.01,
    model_storage_directory: str | Path = DEFAULT_MODEL_DIR,
) -> list[OCRBlock]:
    image = Image.open(image_path).convert("RGB")
    offset_x = 0
    offset_y = 0
    if crop is not None:
        left, top, right, bottom = clamp_crop(crop, image.size)
        image = image.crop((left, top, right, bottom))
        offset_x = left
        offset_y = top
    processed = preprocess_image(image, scale=scale)
    reader = easyocr_reader(tuple(languages), str(model_storage_directory))
    raw_results = reader.readtext(
        np.array(processed),
        detail=1,
        paragraph=False,
        text_threshold=0.35,
        low_text=0.2,
    )
    blocks: list[OCRBlock] = []
    for raw in raw_results:
        block = parse_easyocr_result(raw, offset_x=offset_x, offset_y=offset_y, scale=scale)
        if block is None or block.confidence < min_confidence:
            continue
        blocks.append(block)
    return sorted(blocks, key=lambda item: (item.y, item.x))


@lru_cache(maxsize=4)
def easyocr_reader(languages: tuple[str, ...], model_storage_directory: str) -> Any:
    try:
        import easyocr
    except ImportError as exc:
        raise RuntimeError("EasyOCR is not installed. Install it with `python -m pip install easyocr`.") from exc
    Path(model_storage_directory).mkdir(parents=True, exist_ok=True)
    return easyocr.Reader(
        list(languages),
        gpu=False,
        verbose=False,
        model_storage_directory=model_storage_directory,
    )


def preprocess_image(image: Image.Image, *, scale: int) -> Image.Image:
    scale = max(1, int(scale))
    processed = ImageEnhance.Contrast(image).enhance(1.35)
    processed = ImageEnhance.Sharpness(processed).enhance(1.4)
    if scale > 1:
        processed = processed.resize((processed.width * scale, processed.height * scale), Image.Resampling.LANCZOS)
    return processed


def parse_easyocr_result(raw: object, *, offset_x: int, offset_y: int, scale: int) -> OCRBlock | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        return None
    points, text, confidence = raw[0], raw[1], raw[2]
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        point_pairs = [(float(point[0]), float(point[1])) for point in points]
        confidence_value = float(confidence)
    except (TypeError, ValueError, IndexError):
        return None
    xs = [point[0] for point in point_pairs]
    ys = [point[1] for point in point_pairs]
    scale = max(1, int(scale))
    left = round(min(xs) / scale) + offset_x
    top = round(min(ys) / scale) + offset_y
    right = round(max(xs) / scale) + offset_x
    bottom = round(max(ys) / scale) + offset_y
    return OCRBlock(
        text=normalize_text(text),
        confidence=confidence_value,
        bbox=(left, top, max(1, right - left), max(1, bottom - top)),
    )


def normalize_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").split())


def clamp_crop(crop: tuple[int, int, int, int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    left, top, right, bottom = crop
    left = max(0, min(width - 1, left))
    top = max(0, min(height - 1, top))
    right = max(left + 1, min(width, right))
    bottom = max(top + 1, min(height, bottom))
    return left, top, right, bottom


def knowledge_panel_crop(size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    return (
        round(width * 0.04),
        round(height * 0.20),
        round(width * 0.97),
        round(height * 0.91),
    )


def blocks_to_dicts(blocks: list[OCRBlock]) -> list[dict[str, object]]:
    return [asdict(block) for block in blocks]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reusable Korean OCR utility for game screenshots")
    parser.add_argument("image", help="Image path to OCR")
    parser.add_argument("--preset", choices=("full", "knowledge_panel"), default="full")
    parser.add_argument("--scale", type=int, default=1)
    parser.add_argument("--min-confidence", type=float, default=0.01)
    parser.add_argument("--output", default="", help="Optional JSON output path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    image_path = Path(args.image)
    crop = None
    if args.preset == "knowledge_panel":
        with Image.open(image_path) as image:
            crop = knowledge_panel_crop(image.size)
    blocks = ocr_image(image_path, crop=crop, scale=args.scale, min_confidence=args.min_confidence)
    payload = {"image": str(image_path), "preset": args.preset, "blocks": blocks_to_dicts(blocks)}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
