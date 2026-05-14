from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from agent.env import GameEnvironment


STAGE_NUMBER_ROI = (140, 258, 240, 292)
DEFAULT_TEMPLATE_DIR = Path("assets/stage_templates")


@dataclass(frozen=True)
class StageReadResult:
    stage: int | None
    confidence: float
    roi_path: Path
    reason: str


def save_stage_roi(
    screenshot_path: str | Path,
    output_path: str | Path = "runtime/stage_number_roi.png",
    *,
    roi: tuple[int, int, int, int] = STAGE_NUMBER_ROI,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(screenshot_path).convert("RGB") as image:
        image.crop(roi).save(output)

    return output


def capture_stage_roi(
    env: GameEnvironment,
    screenshot_path: str | Path = "runtime/current_screen.png",
    roi_path: str | Path = "runtime/stage_number_roi.png",
) -> Path:
    screenshot = env.capture_screenshot(screenshot_path)
    return save_stage_roi(screenshot, roi_path)


def read_current_stage(
    env: GameEnvironment,
    *,
    template_dir: str | Path = DEFAULT_TEMPLATE_DIR,
    min_confidence: float = 0.85,
) -> StageReadResult:
    roi_path = capture_stage_roi(env)
    return read_stage_from_roi(roi_path, template_dir=template_dir, min_confidence=min_confidence)


def read_stage_from_roi(
    roi_path: str | Path,
    *,
    template_dir: str | Path = DEFAULT_TEMPLATE_DIR,
    min_confidence: float = 0.85,
) -> StageReadResult:
    roi = _load_gray(roi_path)
    templates = _load_templates(template_dir)

    if not templates:
        return StageReadResult(None, 0.0, Path(roi_path), f"no templates in {template_dir}")

    best_stage: int | None = None
    best_score = -1.0

    for stage, template in templates.items():
        score = _compare_same_size(roi, template)
        if score > best_score:
            best_stage = stage
            best_score = score

    if best_stage is None or best_score < min_confidence:
        return StageReadResult(
            None,
            best_score,
            Path(roi_path),
            f"best template confidence below threshold: {best_score:.3f}",
        )

    return StageReadResult(best_stage, best_score, Path(roi_path), "ok")


def save_stage_template(
    stage: int,
    roi_path: str | Path = "runtime/stage_number_roi.png",
    template_dir: str | Path = DEFAULT_TEMPLATE_DIR,
) -> Path:
    output_dir = Path(template_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{stage}.png"

    with Image.open(roi_path).convert("RGB") as roi:
        roi.save(output_path)

    return output_path


def clicks_to_target(current_stage: int, target_stage: int) -> int:
    clicks = current_stage - target_stage
    if clicks < 0:
        raise ValueError(f"target_stage must be <= current_stage: {current_stage} -> {target_stage}")
    return clicks


def _load_templates(template_dir: str | Path) -> dict[int, np.ndarray]:
    path = Path(template_dir)
    if not path.exists():
        return {}

    templates: dict[int, np.ndarray] = {}
    for file in path.glob("*.png"):
        try:
            stage = int(file.stem)
        except ValueError:
            continue
        templates[stage] = _load_gray(file)
    return templates


def _load_gray(path: str | Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(f"Image not readable: {path}")
    return _preprocess(image)


def _preprocess(image: np.ndarray) -> np.ndarray:
    resized = cv2.resize(image, (160, 54), interpolation=cv2.INTER_AREA)
    blurred = cv2.GaussianBlur(resized, (3, 3), 0)
    return cv2.equalizeHist(blurred)


def _compare_same_size(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape:
        right = cv2.resize(right, (left.shape[1], left.shape[0]), interpolation=cv2.INTER_AREA)

    result = cv2.matchTemplate(left, right, cv2.TM_CCOEFF_NORMED)
    return float(result[0][0])
