from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops


@dataclass(frozen=True)
class ActionRecord:
    time: str
    screen_hash: str
    candidate_id: str
    x: int
    y: int
    changed: bool | None
    executed: bool
    before: str
    after: str | None
    label_guess: str = ""
    kind: str | None = None
    layer: str | None = None
    bbox: list[int] | None = None
    visual_center: list[int] | None = None
    tap_point: list[int] | None = None
    is_repair: bool = False
    repair_reason: str | None = None
    repair_strategy: str | None = None
    parent_candidate_id: str | None = None
    repair_attempt_index: int | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_screen(screen_path: str | Path) -> str:
    """Small perceptual-ish hash for grouping visually identical screens."""
    with Image.open(screen_path) as image:
        gray = image.convert("L").resize((16, 16))
        pixels = list(gray.getdata())

    mean = sum(pixels) / len(pixels)
    bits = "".join("1" if pixel >= mean else "0" for pixel in pixels)
    value = int(bits, 2)
    return f"{value:064x}"


def screen_changed(before_path: str | Path, after_path: str | Path, *, threshold: float = 0.015) -> bool:
    with Image.open(before_path) as before_image, Image.open(after_path) as after_image:
        before = before_image.convert("RGB")
        after = after_image.convert("RGB").resize(before.size)
        diff = ImageChops.difference(before, after)
        histogram = diff.histogram()

    total = before.size[0] * before.size[1] * 255 * 3
    difference = sum((value % 256) * count for value, count in enumerate(histogram))
    return (difference / total) >= threshold


def load_records(log_path: str | Path) -> list[dict[str, Any]]:
    path = Path(log_path)
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records


def tried_candidate_ids(log_path: str | Path, screen_hash: str) -> set[str]:
    return {
        str(record["candidate_id"])
        for record in load_records(log_path)
        if record.get("executed") is True and similar_screen_hash(str(record.get("screen_hash", "")), screen_hash)
    }


def similar_screen_hash(left: str, right: str, *, max_distance: int = 12) -> bool:
    if left == right:
        return True
    if len(left) != len(right):
        return False

    try:
        distance = (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return False
    return distance <= max_distance


def append_record(log_path: str | Path, record: ActionRecord) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
