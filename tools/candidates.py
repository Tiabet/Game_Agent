from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


BBox = tuple[int, int, int, int]
Point = tuple[int, int]
DEFAULT_REPAIR_MEMORY = Path("runtime/repair_memory.json")


@dataclass(frozen=True)
class Candidate:
    id: str
    x: int
    y: int
    kind: str
    score: float
    bbox: BBox | None
    label_guess: str = ""
    layer: str = "normal"
    parent: str | None = None
    group: str | None = None
    visual_center: Point | None = None
    tap_point: Point | None = None
    layer_hint: str | None = None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["bbox"] = list(self.bbox) if self.bbox is not None else None
        data["visual_center"] = list(self.visual_center or (self.x, self.y))
        data["tap_point"] = list(self.tap_point or (self.x, self.y))
        data["layer_hint"] = self.layer_hint or self.layer
        return data


BASE_CANDIDATES: tuple[tuple[str, float, float, str], ...] = (
    ("center", 0.50, 0.50, "main interactive area"),
    ("top_right", 0.90, 0.10, "close/reward/menu"),
    ("top_left", 0.10, 0.10, "back/profile/menu"),
    ("mid_right", 0.88, 0.50, "right side button"),
    ("mid_left", 0.12, 0.50, "left side button"),
    ("upper_center", 0.50, 0.25, "upper content"),
    ("lower_center", 0.50, 0.75, "lower content"),
    ("close_top_right", 0.95, 0.05, "close button"),
    ("close_top_left", 0.05, 0.05, "back/close button"),
    ("top_center", 0.50, 0.08, "top title or reward"),
    ("popup_confirm", 0.68, 0.68, "popup confirm"),
    ("popup_cancel", 0.32, 0.68, "popup cancel"),
    ("popup_close", 0.82, 0.22, "popup close"),
    ("dialog_center", 0.50, 0.58, "dialog main button"),
    ("left_button", 0.28, 0.82, "left action button"),
    ("right_button", 0.72, 0.82, "right action button"),
    ("left_mid_upper", 0.25, 0.38, "left mid content"),
    ("right_mid_upper", 0.75, 0.38, "right mid content"),
    ("left_mid_lower", 0.25, 0.62, "left lower content"),
    ("right_mid_lower", 0.75, 0.62, "right lower content"),
)


def find_candidates(screen_path: str | Path = "runtime/screenshots/current.png") -> list[Candidate]:
    """Return fixed fallback and OpenCV-derived UI click candidates."""
    with Image.open(screen_path) as image:
        width, height = image.size

    candidates: list[Candidate] = []
    candidates.extend(fixed_candidates(width, height))
    contours = contour_candidates(screen_path, width, height)
    candidates.extend(contours)
    candidates.extend(bright_region_candidates(screen_path, width, height))
    candidates.extend(popup_candidates(screen_path, width, height))
    candidates.extend(popup_candidates_from_contours(contours, width, height))
    candidates.extend(modal_layer_candidates(screen_path, width, height))
    return validate_candidate_coordinates(apply_repair_memory(dedupe_candidates(candidates)), width, height)


def local_screen_analysis(screen_path: str | Path) -> dict[str, object]:
    with Image.open(screen_path) as image:
        width, height = image.size
    modal_boxes = detect_modal_boxes(screen_path, width, height)
    local_grid_like = detect_grid_like_layout(screen_path, width, height)
    local_modal_score = modal_score_from_boxes(modal_boxes, width, height)
    local_active_layer_hint = "modal" if local_modal_score >= 0.70 or (local_modal_score >= 0.50 and not local_grid_like) else "normal"
    return {
        "local_active_layer_hint": local_active_layer_hint,
        "local_modal_score": round(local_modal_score, 4),
        "local_grid_like": local_grid_like,
        "screen_bounds": {"width": width, "height": height},
    }


def modal_score_from_boxes(boxes: list[BBox], width: int, height: int) -> float:
    if not boxes:
        return 0.0
    screen_area = width * height
    largest = max(box[2] * box[3] for box in boxes)
    return min(1.0, largest / max(screen_area, 1) * 4.0)


def detect_grid_like_layout(screen_path: str | Path, width: int, height: int) -> bool:
    contours = contour_candidates(screen_path, width, height)
    boxes = [candidate.bbox for candidate in contours if candidate.bbox is not None]
    card_like: list[BBox] = []
    for x, y, box_width, box_height in boxes:
        area = box_width * box_height
        center_y = y + box_height / 2
        if not (height * 0.12 <= center_y <= height * 0.88):
            continue
        if area < 250 or area > width * height * 0.08:
            continue
        if box_width < 12 or box_height < 12:
            continue
        card_like.append((x, y, box_width, box_height))
    if len(card_like) < 8:
        return False
    row_buckets = {round((y + box_height / 2) / 32) for _, y, _, box_height in card_like}
    column_buckets = {round((x + box_width / 2) / 32) for x, _, box_width, _ in card_like}
    return len(row_buckets) >= 2 and len(column_buckets) >= 3


def validate_candidate_coordinates(candidates: list[Candidate], width: int, height: int) -> list[Candidate]:
    valid: list[Candidate] = []
    for candidate in candidates:
        if not (0 <= candidate.x < width and 0 <= candidate.y < height):
            continue
        if candidate.bbox is not None:
            x, y, box_width, box_height = candidate.bbox
            if box_width <= 0 or box_height <= 0 or x < 0 or y < 0 or x + box_width > width or y + box_height > height:
                continue
        valid.append(candidate)
    return valid


def apply_repair_memory(candidates: list[Candidate], memory_path: Path = DEFAULT_REPAIR_MEMORY) -> list[Candidate]:
    if not candidates or not memory_path.exists():
        return candidates
    try:
        memory = json.loads(memory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return candidates
    if not isinstance(memory, list):
        return candidates
    modal = next((candidate for candidate in candidates if candidate.layer == "modal" and candidate.kind in {"modal", "popup"} and candidate.bbox), None)
    if modal is None or modal.bbox is None:
        return candidates
    repair_record = next((record for record in reversed(memory) if isinstance(record, dict) and record.get("context") == "modal" and valid_relative(record.get("successful_relative_position"))), None)
    if repair_record is None:
        return candidates
    rx, ry = repair_record["successful_relative_position"]
    x, y, width, height = modal.bbox
    tap_point = (round(x + width * float(rx)), round(y + height * float(ry)))
    adjusted: list[Candidate] = []
    for candidate in candidates:
        if candidate.layer == "modal" and candidate.kind == "fixed" and is_safe_popup_candidate(candidate):
            adjusted.append(
                Candidate(
                    id=candidate.id,
                    x=tap_point[0],
                    y=tap_point[1],
                    kind=candidate.kind,
                    score=min(0.99, candidate.score + 0.04),
                    bbox=candidate.bbox,
                    label_guess=f"{candidate.label_guess}; tap_point adjusted from repair_memory",
                    layer=candidate.layer,
                    parent=candidate.parent,
                    group=candidate.group,
                    visual_center=candidate.visual_center or (candidate.x, candidate.y),
                    tap_point=tap_point,
                )
            )
        else:
            adjusted.append(candidate)
    return adjusted


def valid_relative(value: object) -> bool:
    return isinstance(value, list) and len(value) == 2 and all(isinstance(item, (int, float)) for item in value)


def detect_active_layer(image: str | Path | np.ndarray) -> str:
    cv_image = load_cv_image(image)
    if cv_image is None:
        return "normal"
    height, width = cv_image.shape[:2]
    return "modal" if detect_modal_boxes(cv_image, width, height) else "normal"


def save_candidates_debug(
    image_path: str | Path,
    candidates: list[Candidate],
    output_path: str | Path = "runtime/screenshots/candidates_debug.png",
    repair_points: list[dict[str, object]] | None = None,
    local_active_layer_hint: str | None = None,
    selected_active_layer: str | None = None,
) -> Path:
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image_height, image_width = image.shape[:2]
    active_layer = selected_active_layer or local_active_layer_hint or active_layer_from_candidates(candidates)
    cv2.putText(
        image,
        f"IMG={image_width}x{image_height}, COORD=original, ADB=original",
        (8, image_height - 10),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        f"LOCAL_HINT={local_active_layer_hint or 'unknown'} LLM_SELECTED={selected_active_layer or 'pending'}",
        (8, 22),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (0, 0, 255) if active_layer == "modal" else (255, 255, 255),
        1,
        cv2.LINE_AA,
    )

    for index, candidate in enumerate(candidates):
        color = debug_color_for_candidate(candidate, active_layer)
        label = f"{candidate.id} {candidate.kind}/{candidate.layer} {candidate.score:.2f}"
        if "button row" in candidate.label_guess.lower():
            label = f"ROW {label}"
        if active_layer == "modal" and is_safe_popup_candidate(candidate):
            label = f"SAFE {label}"
        elif active_layer == "modal" and is_danger_popup_candidate(candidate):
            label = f"DANGER {label}"
        if candidate.bbox is not None:
            x, y, width, height = candidate.bbox
            cv2.rectangle(image, (x, y), (x + width, y + height), color, 1)
            label_x = x
            label_y = max(12, y - 4)
        else:
            radius = 6
            cv2.rectangle(
                image,
                (candidate.x - radius, candidate.y - radius),
                (candidate.x + radius, candidate.y + radius),
                color,
                1,
            )
            cv2.circle(image, (candidate.x, candidate.y), 2, color, -1)
            label_x = candidate.x + 8
            label_y = max(12, candidate.y - 8)

        visual_x, visual_y = candidate.visual_center or (candidate.x, candidate.y)
        cv2.circle(image, (visual_x, visual_y), 4, color, 1)
        cv2.drawMarker(image, (candidate.x, candidate.y), color, markerType=cv2.MARKER_CROSS, markerSize=10, thickness=1)

        cv2.putText(
            image,
            f"{index + 1}:{label}",
            (label_x, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            color,
            1,
            cv2.LINE_AA,
        )

    for index, repair_point in enumerate(repair_points or [], start=1):
        x = int(repair_point.get("x", -1))
        y = int(repair_point.get("y", -1))
        if x < 0 or y < 0:
            continue
        status = str(repair_point.get("status", "pending"))
        color = (0, 255, 0) if status == "success" else (0, 0, 255) if status == "failed" else (255, 0, 0)
        cv2.drawMarker(image, (x, y), color, markerType=cv2.MARKER_STAR, markerSize=12, thickness=1)
        cv2.putText(
            image,
            f"R{index}:{repair_point.get('strategy', 'repair')}",
            (x + 6, max(12, y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.32,
            color,
            1,
            cv2.LINE_AA,
        )

    cv2.imwrite(str(output), image)
    return output


def fixed_candidates(width: int, height: int) -> list[Candidate]:
    return [
        Candidate(
            id=candidate_id,
            x=clamp(round(width * x_ratio), 0, width - 1),
            y=clamp(round(height * y_ratio), 0, height - 1),
                kind="fixed",
                layer="normal",
                score=0.25,
            bbox=None,
            label_guess=label_guess,
        )
        for candidate_id, x_ratio, y_ratio, label_guess in BASE_CANDIDATES
    ]


def contour_candidates(screen_path: str | Path, width: int, height: int) -> list[Candidate]:
    image = cv2.imread(str(screen_path))
    if image is None:
        return []

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blurred, 50, 150)
    kernel = np.ones((3, 3), np.uint8)
    edges = cv2.dilate(edges, kernel, iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: list[Candidate] = []
    screen_area = width * height
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        area = box_width * box_height
        if should_skip_box(area, box_width, box_height, screen_area, width, height):
            continue
        center_x = x + box_width // 2
        center_y = y + box_height // 2
        fill_ratio = cv2.contourArea(contour) / max(area, 1)
        score = min(0.95, 0.35 + normalized_area(area, screen_area) + min(fill_ratio, 1.0) * 0.25)
        candidates.append(
            Candidate(
                id=f"contour_{center_x}_{center_y}_{box_width}x{box_height}",
                x=center_x,
                y=center_y,
                kind="contour",
                layer="normal",
                score=round(score, 4),
                bbox=(x, y, box_width, box_height),
                label_guess="opencv contour UI region",
            )
        )
    return candidates


def bright_region_candidates(screen_path: str | Path, width: int, height: int) -> list[Candidate]:
    image = cv2.imread(str(screen_path))
    if image is None:
        return []

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    value = hsv[:, :, 2]
    saturation = hsv[:, :, 1]
    threshold = max(180, int(np.percentile(value, 90)))
    mask = cv2.inRange(value, threshold, 255)
    mask = cv2.bitwise_and(mask, cv2.inRange(saturation, 35, 255))
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: list[Candidate] = []
    screen_area = width * height
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        area = box_width * box_height
        if should_skip_box(area, box_width, box_height, screen_area, width, height):
            continue
        center_x = x + box_width // 2
        center_y = y + box_height // 2
        brightness = float(np.mean(value[y : y + box_height, x : x + box_width])) / 255.0
        score = min(0.98, 0.45 + normalized_area(area, screen_area) + brightness * 0.25)
        candidates.append(
            Candidate(
                id=f"bright_{center_x}_{center_y}_{box_width}x{box_height}",
                x=center_x,
                y=center_y,
                kind="bright_region",
                layer="normal",
                score=round(score, 4),
                bbox=(x, y, box_width, box_height),
                label_guess="bright highlighted UI region",
            )
        )
    return candidates


def bottom_menu_candidates(width: int, height: int) -> list[Candidate]:
    y = clamp(round(height * 0.955), 0, height - 1)
    item_count = 5
    item_width = width / item_count
    candidates: list[Candidate] = []
    for index in range(item_count):
        center_x = clamp(round(item_width * (index + 0.5)), 0, width - 1)
        candidates.append(
            Candidate(
                id=f"bottom_menu_{index + 1}",
                x=center_x,
                y=y,
                kind="bottom_menu",
                layer="normal",
                score=0.35,
                bbox=(round(item_width * index), round(height * 0.90), round(item_width), height - round(height * 0.90)),
                label_guess="coarse synthetic bottom navigation region; visual center may not match real button",
            )
        )
    return candidates


def load_cv_image(image: str | Path | np.ndarray) -> np.ndarray | None:
    if isinstance(image, np.ndarray):
        return image
    return cv2.imread(str(image))


def detect_modal_boxes(image: str | Path | np.ndarray, width: int | None = None, height: int | None = None) -> list[BBox]:
    cv_image = load_cv_image(image)
    if cv_image is None:
        return []
    image_height, image_width = cv_image.shape[:2]
    width = width or image_width
    height = height or image_height
    gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 35, 120)
    kernel = np.ones((9, 9), np.uint8)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes: list[BBox] = []
    screen_area = width * height
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        area = box_width * box_height
        if looks_like_popup_box(x, y, box_width, box_height, area, screen_area, width, height):
            boxes.append((x, y, box_width, box_height))
    if not boxes:
        fine_edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 50, 150)
        fine_edges = cv2.dilate(fine_edges, np.ones((3, 3), np.uint8), iterations=1)
        fine_contours, _ = cv2.findContours(fine_edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in fine_contours:
            x, y, box_width, box_height = cv2.boundingRect(contour)
            area = box_width * box_height
            if looks_like_popup_box(x, y, box_width, box_height, area, screen_area, width, height):
                boxes.append((x, y, box_width, box_height))
    if not boxes:
        boxes.extend(detect_center_dialog_from_button_row(gray, width, height))
    return sorted(boxes, key=lambda box: box[2] * box[3], reverse=True)[:2]


def detect_center_dialog_from_button_row(gray: np.ndarray, width: int, height: int) -> list[BBox]:
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 35, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rows: list[BBox] = []
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        area = box_width * box_height
        center_x = x + box_width / 2
        center_y = y + box_height / 2
        if area < 1_200:
            continue
        if box_width < width * 0.45 or box_width > width * 0.90:
            continue
        if box_height < 24 or box_height > height * 0.18:
            continue
        if not (width * 0.25 <= center_x <= width * 0.75):
            continue
        if not (height * 0.48 <= center_y <= height * 0.72):
            continue
        rows.append((x, y, box_width, box_height))
    if not rows:
        return []

    row = max(rows, key=lambda item: item[2] * item[3])
    row_x, row_y, row_width, row_height = row
    dialog_height = clamp(round(row_height * 3.1), round(height * 0.24), round(height * 0.38))
    dialog_top = clamp(round(row_y - dialog_height * 0.48), 0, height - dialog_height)
    dialog_left = 0 if row_width > width * 0.55 else clamp(row_x - round(row_width * 0.30), 0, width - row_width)
    dialog_width = width if row_width > width * 0.55 else min(width - dialog_left, round(row_width * 1.6))
    return [(dialog_left, dialog_top, dialog_width, dialog_height)]


def modal_layer_candidates(screen_path: str | Path, width: int, height: int) -> list[Candidate]:
    boxes = detect_modal_boxes(screen_path, width, height)
    contours = contour_candidates(screen_path, width, height)
    image = cv2.imread(str(screen_path))
    if not boxes:
        boxes = [candidate.bbox for candidate in contours if candidate.bbox and looks_like_popup_box(*candidate.bbox, candidate.bbox[2] * candidate.bbox[3], width * height, width, height)][:1]

    candidates: list[Candidate] = []
    screen_area = width * height
    for index, box in enumerate(boxes, start=1):
        x, y, box_width, box_height = box
        center_x = x + box_width // 2
        center_y = y + box_height // 2
        modal_id = f"modal_{center_x}_{center_y}_{box_width}x{box_height}"
        candidates.append(
            Candidate(
                id=modal_id,
                x=center_x,
                y=center_y,
                kind="modal",
                score=round(min(0.92, 0.55 + normalized_area(box_width * box_height, screen_area)), 4),
                bbox=box,
                label_guess="active modal region",
                layer="modal",
                group=modal_id,
            )
        )
        button_pair_candidates = modal_button_pair_candidates_from_image(image, box, modal_id, index) if image is not None else []
        button_contours = button_pair_candidates
        if not button_contours and image is not None:
            button_contours = modal_button_candidates_from_image(image, box, modal_id, index)
        if button_contours:
            candidates.extend(button_contours)
        else:
            candidates.extend(modal_button_candidates_from_contours(contours, box, modal_id, index))
        candidates.extend(popup_button_candidates(box, modal_id, index, width, height))
        candidates.extend(
            modal_context_fixed_candidates(
                box,
                modal_id,
                width,
                height,
                modal_button_row_from_candidates(button_pair_candidates),
                right_button_center=right_button_center_from_candidates(button_pair_candidates),
                left_button_center=left_button_center_from_candidates(button_pair_candidates),
            )
        )
    return candidates


def modal_button_pair_candidates_from_image(image: np.ndarray, box: BBox, modal_id: str, modal_index: int) -> list[Candidate]:
    pair = detect_modal_button_pair(image, box)
    if pair is None:
        return []
    left_box, right_box = pair
    return [
        modal_button_candidate_from_bbox(left_box, modal_id, modal_index, "left", safe=False, score=0.74),
        modal_button_candidate_from_bbox(right_box, modal_id, modal_index, "right", safe=True, score=0.98),
    ]


def detect_modal_button_pair(image: np.ndarray, box: BBox) -> tuple[BBox, BBox] | None:
    modal_x, modal_y, modal_width, modal_height = box
    crop_top = modal_y
    crop_bottom = modal_y + round(modal_height * 0.60)
    crop = image[crop_top:crop_bottom, modal_x : modal_x + modal_width]
    if crop.size == 0:
        return None

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 35, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes: list[BBox] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        abs_box = (modal_x + x, crop_top + y, width, height)
        if not looks_like_modal_button(abs_box, box):
            continue
        boxes.append(abs_box)

    best: tuple[BBox, BBox] | None = None
    best_score = -1.0
    for index, left in enumerate(boxes):
        for right in boxes[index + 1 :]:
            left_center_x, left_center_y = bbox_center(left)
            right_center_x, right_center_y = bbox_center(right)
            if left_center_x > right_center_x:
                left, right = right, left
                left_center_x, left_center_y = bbox_center(left)
                right_center_x, right_center_y = bbox_center(right)
            y_delta = abs(left_center_y - right_center_y)
            height_delta = abs(left[3] - right[3])
            width_ratio = min(left[2], right[2]) / max(left[2], right[2], 1)
            gap = right[0] - (left[0] + left[2])
            if y_delta > max(12, modal_height * 0.12):
                continue
            if height_delta > max(10, modal_height * 0.10):
                continue
            if width_ratio < 0.55:
                continue
            if gap < 8 or gap > modal_width * 0.45:
                continue
            score = width_ratio * 2.0 - (y_delta / max(modal_height, 1)) - (height_delta / max(modal_height, 1))
            if score > best_score:
                best_score = score
                best = (left, right)
    return best


def looks_like_modal_button(button_box: BBox, modal_box: BBox) -> bool:
    modal_x, modal_y, modal_width, modal_height = modal_box
    x, y, width, height = button_box
    center_x, center_y = bbox_center(button_box)
    if not point_in_box(center_x, center_y, modal_box):
        return False
    if center_y < modal_y or center_y > modal_y + modal_height * 0.62:
        return False
    if width < modal_width * 0.16 or width > modal_width * 0.45:
        return False
    if height < 16 or height > modal_height * 0.55:
        return False
    if width / max(height, 1) < 1.5:
        return False
    return True


def modal_button_candidate_from_bbox(button_box: BBox, modal_id: str, modal_index: int, relative: str, *, safe: bool, score: float) -> Candidate:
    center_x, center_y = bbox_center(button_box)
    return Candidate(
        id=f"modal_button_{modal_index}_{relative}_{center_x}_{center_y}",
        x=center_x,
        y=center_y,
        kind="popup_button",
        score=score,
        bbox=button_box,
        label_guess=f"detected_button_{relative} {'cancel/no safe' if safe else 'confirm/exit risk'}",
        layer="modal",
        parent=modal_id,
        group=modal_id,
        visual_center=(center_x, center_y),
        tap_point=(center_x, center_y),
    )


def modal_button_candidates_from_image(image: np.ndarray, box: BBox, modal_id: str, modal_index: int) -> list[Candidate]:
    modal_x, modal_y, modal_width, modal_height = box
    crop_top = modal_y
    crop_bottom = modal_y + round(modal_height * 0.60)
    crop = image[crop_top:crop_bottom, modal_x : modal_x + modal_width]
    if crop.size == 0:
        return []

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    value = hsv[:, :, 2]
    saturation = hsv[:, :, 1]
    bright_threshold = max(145, int(np.percentile(value, 72)))
    bright_mask = cv2.inRange(value, bright_threshold, 255)
    color_mask = cv2.inRange(saturation, 20, 255)
    edge_mask = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 45, 130)
    mask = cv2.bitwise_or(cv2.bitwise_and(bright_mask, color_mask), edge_mask)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=1)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: list[Candidate] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        abs_x = modal_x + x
        abs_y = crop_top + y
        area = width * height
        if width < 28 or height < 10 or area < 260:
            continue
        if width > modal_width * 0.65 or height > modal_height * 0.35:
            continue
        center_x = abs_x + width // 2
        center_y = abs_y + height // 2
        if center_y < modal_y or center_y > modal_y + modal_height * 0.62:
            continue
        relative = "left" if center_x < modal_x + modal_width * 0.45 else "right" if center_x > modal_x + modal_width * 0.55 else "center"
        safe = relative == "right"
        candidates.append(
            Candidate(
                id=f"modal_button_{modal_index}_{relative}_{center_x}_{center_y}",
                x=center_x,
                y=center_y,
                kind="popup_button",
                score=0.96 if safe else 0.74,
                bbox=(abs_x, abs_y, width, height),
                label_guess=f"modal {relative} {'cancel/no safe' if safe else 'confirm/exit risk'} bright button contour",
                layer="modal",
                parent=modal_id,
                group=modal_id,
                visual_center=(center_x, center_y),
                tap_point=(center_x, center_y),
            )
        )
    return sorted(candidates, key=lambda candidate: (candidate.x, candidate.score), reverse=True)[:6]


def modal_button_candidates_from_contours(contours: list[Candidate], box: BBox, modal_id: str, modal_index: int) -> list[Candidate]:
    modal_x, modal_y, modal_width, modal_height = box
    candidates: list[Candidate] = []
    for contour in contours:
        if contour.bbox is None:
            continue
        x, y, width, height = contour.bbox
        area = width * height
        if not point_in_box(contour.x, contour.y, box):
            continue
        if contour.y < modal_y + modal_height * 0.45:
            continue
        if width < 32 or height < 12 or area < 350 or area > modal_width * modal_height * 0.45:
            continue
        relative = "left" if contour.x < modal_x + modal_width * 0.45 else "right" if contour.x > modal_x + modal_width * 0.55 else "center"
        safe = relative == "right"
        candidates.append(
            Candidate(
                id=f"modal_button_{modal_index}_{relative}_{contour.x}_{contour.y}",
                x=contour.x,
                y=contour.y,
                kind="popup_button",
                score=0.90 if safe else 0.72,
                bbox=contour.bbox,
                label_guess=f"modal {relative} {'cancel/no safe' if safe else 'confirm/exit risk'} contour button",
                layer="modal",
                parent=modal_id,
                group=modal_id,
            )
        )
    return candidates


def modal_button_row(contours: list[Candidate], box: BBox) -> BBox | None:
    modal_x, modal_y, modal_width, modal_height = box
    button_boxes: list[BBox] = []
    for contour in contours:
        if contour.bbox is None:
            continue
        x, y, width, height = contour.bbox
        if not point_in_box(contour.x, contour.y, box):
            continue
        if contour.y < modal_y + modal_height * 0.45:
            continue
        if width > modal_width * 0.75 or height > modal_height * 0.50:
            continue
        if width < 24 or height < 10 or width * height < 220:
            continue
        button_boxes.append(contour.bbox)
    if not button_boxes:
        return None
    left = min(item[0] for item in button_boxes)
    top = min(item[1] for item in button_boxes)
    right = max(item[0] + item[2] for item in button_boxes)
    bottom = max(item[1] + item[3] for item in button_boxes)
    if right - left < modal_width * 0.20:
        return None
    return (left, top, right - left, bottom - top)


def modal_button_row_from_candidates(candidates: list[Candidate]) -> BBox | None:
    boxes = [candidate.bbox for candidate in candidates if candidate.bbox is not None]
    if not boxes:
        return None
    left = min(item[0] for item in boxes)
    top = min(item[1] for item in boxes)
    right = max(item[0] + item[2] for item in boxes)
    bottom = max(item[1] + item[3] for item in boxes)
    return (left, top, right - left, bottom - top)


def right_button_center_from_candidates(candidates: list[Candidate]) -> Point | None:
    right_candidates = [candidate for candidate in candidates if candidate.kind == "popup_button" and "right" in candidate.id]
    if not right_candidates:
        return None
    candidate = max(right_candidates, key=lambda item: item.x)
    return candidate.x, candidate.y


def left_button_center_from_candidates(candidates: list[Candidate]) -> Point | None:
    left_candidates = [candidate for candidate in candidates if candidate.kind == "popup_button" and "left" in candidate.id]
    if not left_candidates:
        return None
    candidate = min(left_candidates, key=lambda item: item.x)
    return candidate.x, candidate.y


def modal_context_fixed_candidates(
    box: BBox,
    modal_id: str,
    screen_width: int,
    screen_height: int,
    button_row: BBox | None = None,
    *,
    right_button_center: Point | None = None,
    left_button_center: Point | None = None,
) -> list[Candidate]:
    x, y, width, height = box
    if button_row is not None:
        row_x, row_y, row_width, row_height = button_row
        button_y = clamp(row_y + row_height // 2, 0, screen_height - 1)
        left_x = clamp(row_x + row_width // 4, 0, screen_width - 1)
        right_x = clamp(row_x + row_width * 3 // 4, 0, screen_width - 1)
        row_bbox = button_row
    else:
        button_y = clamp(round(y + height * 0.23), 0, screen_height - 1)
        left_x = clamp(round(x + width * 0.24), 0, screen_width - 1)
        right_x = clamp(round(x + width * 0.62), 0, screen_width - 1)
        row_height = clamp(round(height * 0.39), 28, 48)
        row_width = clamp(round(width * 0.66), 120, width)
        row_bbox = (clamp(round(x + width * 0.11), 0, screen_width - row_width), clamp(round(y + height * 0.04), 0, screen_height - row_height), row_width, row_height)
    if right_button_center is not None:
        right_x, button_y = right_button_center
    if left_button_center is not None:
        left_x, _ = left_button_center
    cancel_x = right_x
    close_x = clamp(round(x + width * 0.82), 0, screen_width - 1)
    close_y = clamp(round(y + height * 0.20), 0, screen_height - 1)
    return [
        Candidate("popup_cancel", cancel_x, button_y, "fixed", 0.86, row_bbox, "modal popup cancel/right safe fallback", layer="modal", parent=modal_id, group=modal_id, visual_center=(right_x, button_y), tap_point=(cancel_x, button_y)),
        Candidate("right_mid_lower", right_x, button_y, "fixed", 0.82, row_bbox, "modal right cancel/no safe fallback", layer="modal", parent=modal_id, group=modal_id, visual_center=(right_x, button_y), tap_point=(right_x, button_y)),
        Candidate("left_mid_lower", left_x, button_y, "fixed", 0.40, row_bbox, "modal left confirm/exit risk fallback", layer="modal", parent=modal_id, group=modal_id, visual_center=(left_x, button_y), tap_point=(left_x, button_y)),
        Candidate("popup_close", close_x, close_y, "fixed", 0.80, None, "modal close safe fallback", layer="modal", parent=modal_id, group=modal_id, visual_center=(close_x, close_y), tap_point=(close_x, close_y)),
    ]


def popup_candidates(screen_path: str | Path, width: int, height: int) -> list[Candidate]:
    image = cv2.imread(str(screen_path))
    if image is None:
        return []

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 35, 120)
    kernel = np.ones((9, 9), np.uint8)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes: list[BBox] = []
    screen_area = width * height
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        area = box_width * box_height
        if not looks_like_popup_box(x, y, box_width, box_height, area, screen_area, width, height):
            continue
        boxes.append((x, y, box_width, box_height))

    boxes = sorted(boxes, key=lambda box: box[2] * box[3], reverse=True)[:2]
    candidates: list[Candidate] = []
    for index, box in enumerate(boxes, start=1):
        x, y, box_width, box_height = box
        center_x = x + box_width // 2
        center_y = y + box_height // 2
        popup_id = f"popup_{center_x}_{center_y}_{box_width}x{box_height}"
        score = min(0.92, 0.55 + normalized_area(box_width * box_height, screen_area))
        candidates.append(
            Candidate(
                id=popup_id,
                x=center_x,
                y=center_y,
                kind="popup",
                score=round(score, 4),
                bbox=box,
                label_guess="detected popup/modal region",
                layer="modal",
                group=popup_id,
            )
        )
        candidates.extend(popup_button_candidates(box, popup_id, index, width, height))
    return candidates


def popup_button_candidates(box: BBox, popup_id: str, popup_index: int, screen_width: int, screen_height: int) -> list[Candidate]:
    x, y, width, height = box
    button_y = clamp(round(y + height * 0.23), 0, screen_height - 1)
    button_height = clamp(round(height * 0.39), 28, 48)
    button_width = clamp(round(width * 0.30), 70, 130)
    button_top = clamp(button_y - button_height // 2, 0, screen_height - button_height)
    button_specs = (
        ("confirm_left", 0.24, "popup left confirm/exit risk button", 0.72),
        ("center", 0.43, "popup center button", 0.76),
        ("cancel_right", 0.62, "popup cancel/no/right safe button", 0.92),
    )

    candidates: list[Candidate] = []
    for name, ratio, label, score in button_specs:
        center_x = clamp(round(x + width * ratio), 0, screen_width - 1)
        button_left = clamp(center_x - button_width // 2, 0, screen_width - button_width)
        candidates.append(
            Candidate(
                id=f"popup_button_{popup_index}_{name}",
                x=center_x,
                y=button_y,
                kind="popup_button",
                score=score,
                bbox=(button_left, button_top, button_width, button_height),
                label_guess=label,
                layer="modal",
                parent=popup_id,
                group=popup_id,
                visual_center=(center_x, button_y),
                tap_point=(center_x, button_y),
            )
        )

    close_size = clamp(round(min(width, height) * 0.12), 22, 44)
    close_x = clamp(x + width - close_size, 0, screen_width - 1)
    close_y = clamp(y + close_size, 0, screen_height - 1)
    candidates.append(
        Candidate(
            id=f"popup_button_{popup_index}_close",
            x=close_x,
            y=close_y,
            kind="popup_button",
            score=0.88,
            bbox=(clamp(close_x - close_size // 2, 0, screen_width - close_size), clamp(close_y - close_size // 2, 0, screen_height - close_size), close_size, close_size),
            label_guess="popup close/x safe button",
            layer="modal",
            parent=popup_id,
            group=popup_id,
            visual_center=(close_x, close_y),
            tap_point=(close_x, close_y),
        )
    )
    return candidates


def popup_candidates_from_contours(contours: list[Candidate], width: int, height: int) -> list[Candidate]:
    screen_area = width * height
    candidates: list[Candidate] = []
    popup_index = 10
    for contour in contours:
        if contour.bbox is None:
            continue
        x, y, box_width, box_height = contour.bbox
        area = box_width * box_height
        if not looks_like_popup_box(x, y, box_width, box_height, area, screen_area, width, height):
            continue
        popup_index += 1
        popup_id = f"popup_{contour.x}_{contour.y}_{box_width}x{box_height}"
        candidates.append(
            Candidate(
                id=popup_id,
                x=contour.x,
                y=contour.y,
                kind="popup",
                score=0.88,
                bbox=contour.bbox,
                label_guess="detected popup/modal region from contour",
                layer="modal",
                group=popup_id,
            )
        )
        candidates.extend(popup_button_candidates(contour.bbox, popup_id, popup_index, width, height))
    return candidates


def dedupe_candidates(candidates: list[Candidate], *, min_distance: int = 18, limit: int = 80) -> list[Candidate]:
    sorted_candidates = sorted(candidates, key=lambda candidate: candidate.score, reverse=True)
    kept: list[Candidate] = []
    for candidate in sorted_candidates:
        if any(is_duplicate_candidate(candidate, existing, min_distance) for existing in kept):
            continue
        kept.append(candidate)
        if len(kept) >= limit:
            break
    return kept


def is_duplicate_candidate(candidate: Candidate, existing: Candidate, min_distance: int) -> bool:
    if distance(candidate, existing) >= min_distance:
        return False
    if candidate.layer != existing.layer:
        return False
    if candidate.kind == "bottom_menu" and existing.kind != "bottom_menu":
        return False
    return True


def should_skip_box(area: int, box_width: int, box_height: int, screen_area: int, width: int, height: int) -> bool:
    if area < max(180, int(screen_area * 0.0008)):
        return True
    if area > screen_area * 0.20:
        return True
    if box_width < 12 or box_height < 10:
        return True
    if box_width > width * 0.90 or box_height > height * 0.45:
        return True
    return False


def looks_like_popup_box(x: int, y: int, box_width: int, box_height: int, area: int, screen_area: int, width: int, height: int) -> bool:
    if area < screen_area * 0.08 or area > screen_area * 0.70:
        return False
    if box_width < width * 0.45 or box_height < height * 0.15:
        return False
    if box_width > width * 0.98 or box_height > height * 0.85:
        return False
    center_x = x + box_width / 2
    center_y = y + box_height / 2
    if not (width * 0.25 <= center_x <= width * 0.75):
        return False
    if not (height * 0.22 <= center_y <= height * 0.78):
        return False
    if x > width * 0.38 or x + box_width < width * 0.62:
        return False
    return True


def point_in_box(x: int, y: int, box: BBox) -> bool:
    left, top, width, height = box
    return left <= x <= left + width and top <= y <= top + height


def bbox_center(box: BBox) -> Point:
    x, y, width, height = box
    return x + width // 2, y + height // 2


def normalized_area(area: int, screen_area: int) -> float:
    return min(0.30, area / max(screen_area, 1) * 8.0)


def distance(left: Candidate, right: Candidate) -> float:
    return ((left.x - right.x) ** 2 + (left.y - right.y) ** 2) ** 0.5


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def color_for_kind(kind: str) -> tuple[int, int, int]:
    colors = {
        "fixed": (255, 255, 255),
        "contour": (0, 255, 0),
        "bright_region": (0, 255, 255),
        "bottom_menu": (255, 0, 255),
        "popup": (0, 128, 255),
        "modal": (0, 128, 255),
        "popup_button": (0, 0, 255),
    }
    return colors.get(kind, (255, 128, 0))


def debug_color_for_candidate(candidate: Candidate, active_layer: str) -> tuple[int, int, int]:
    if active_layer == "modal" and is_safe_popup_candidate(candidate):
        return (0, 255, 0)
    if active_layer == "modal" and is_danger_popup_candidate(candidate):
        return (0, 0, 255)
    return color_for_kind(candidate.kind)


def active_layer_from_candidates(candidates: list[Candidate]) -> str:
    if any(candidate.layer == "modal" or candidate.kind in {"popup", "modal", "popup_button"} for candidate in candidates):
        return "modal"
    return "normal"


def is_safe_popup_candidate(candidate: Candidate) -> bool:
    text = f"{candidate.id} {candidate.label_guess}".lower()
    if is_danger_popup_candidate(candidate):
        return False
    safe_tokens = ("cancel", "close", "right", " no ", " no/", "/no", "back")
    return (
        candidate.kind == "popup_button" and any(token in f" {text} " for token in safe_tokens)
    ) or "popup_cancel" in candidate.id or "popup_close" in candidate.id or candidate.id == "right_mid_lower"


def is_danger_popup_candidate(candidate: Candidate) -> bool:
    text = f"{candidate.id} {candidate.label_guess}".lower()
    return candidate.id in {"left_mid_lower", "popup_confirm"} or "confirm" in text or "exit" in text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find and optionally visualize click candidates.")
    parser.add_argument("--image", default="runtime/screenshots/current.png")
    parser.add_argument("--debug", action="store_true", help="Save candidate debug visualization.")
    parser.add_argument("--output", default="runtime/screenshots/candidates_debug.png")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    candidates = find_candidates(args.image)
    for candidate in candidates:
        print(candidate.to_dict())
    if args.debug:
        output = save_candidates_debug(args.image, candidates, args.output)
        print(f"Saved debug visualization: {output}")


if __name__ == "__main__":
    main()
