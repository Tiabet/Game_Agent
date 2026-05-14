from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_INSPECTION_PATH = Path("runtime/mercenary_inspection.json")

SYNERGY_BUTTON = {"id": "synergy_button", "x_ratio": 0.908, "y_ratio": 0.145, "kind": "synergy"}
VISIBLE_CARD_SLOTS = (
    ("equipped_1", 0.172, 0.267),
    ("equipped_2", 0.306, 0.267),
    ("equipped_3", 0.439, 0.267),
    ("equipped_4", 0.572, 0.267),
    ("equipped_5", 0.700, 0.267),
    ("equipped_6", 0.858, 0.267),
    ("grid_r1c1", 0.139, 0.447),
    ("grid_r1c2", 0.261, 0.447),
    ("grid_r1c3", 0.381, 0.447),
    ("grid_r1c4", 0.500, 0.447),
    ("grid_r1c5", 0.622, 0.447),
    ("grid_r1c6", 0.742, 0.447),
    ("grid_r1c7", 0.861, 0.447),
    ("grid_r2c1", 0.139, 0.592),
    ("grid_r2c2", 0.261, 0.592),
    ("grid_r2c3", 0.381, 0.592),
    ("grid_r2c4", 0.500, 0.592),
    ("grid_r2c5", 0.622, 0.592),
    ("grid_r2c6", 0.742, 0.592),
    ("grid_r2c7", 0.861, 0.592),
    ("grid_r3c1", 0.139, 0.739),
    ("grid_r3c2", 0.261, 0.739),
    ("grid_r3c3", 0.381, 0.739),
    ("grid_r3c4", 0.500, 0.739),
    ("grid_r3c5", 0.622, 0.739),
    ("grid_r3c6", 0.742, 0.739),
    ("grid_r3c7", 0.861, 0.739),
)


def load_inspection(path: str | Path = DEFAULT_INSPECTION_PATH) -> dict[str, Any]:
    inspection_path = Path(path)
    if not inspection_path.exists():
        return empty_inspection()
    try:
        raw = json.loads(inspection_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_inspection()
    if not isinstance(raw, dict):
        return empty_inspection()
    raw.setdefault("version", 1)
    raw.setdefault("opened_synergy", False)
    raw.setdefault("visited_slots", {})
    raw.setdefault("scroll_count", 0)
    raw.setdefault("events", [])
    return raw


def empty_inspection() -> dict[str, Any]:
    return {"version": 1, "opened_synergy": False, "visited_slots": {}, "scroll_count": 0, "events": []}


def save_inspection(data: dict[str, Any], path: str | Path = DEFAULT_INSPECTION_PATH) -> None:
    inspection_path = Path(path)
    inspection_path.parent.mkdir(parents=True, exist_ok=True)
    inspection_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def inspection_started(path: str | Path = DEFAULT_INSPECTION_PATH) -> bool:
    data = load_inspection(path)
    return bool(data.get("opened_synergy") or data.get("visited_slots") or data.get("events"))


def next_list_target(screen_bounds: tuple[int, int], path: str | Path = DEFAULT_INSPECTION_PATH) -> dict[str, Any] | None:
    width, height = normalized_bounds(screen_bounds)
    data = load_inspection(path)
    if data.get("opened_synergy") is not True:
        target = scaled_target(SYNERGY_BUTTON, width, height)
        data["opened_synergy"] = True
        append_event(data, "select_synergy_button", target)
        save_inspection(data, path)
        return target

    visited = data.setdefault("visited_slots", {})
    page = int(data.get("scroll_count", 0))
    for slot_id, x_ratio, y_ratio in VISIBLE_CARD_SLOTS:
        key = f"page_{page}:{slot_id}"
        if key in visited:
            continue
        target = {
            "id": key,
            "kind": "mercenary_card",
            "x": round(width * x_ratio),
            "y": round(height * y_ratio),
            "slot_id": slot_id,
            "page": page,
        }
        visited[key] = {"selected_at": utc_now(), "x": target["x"], "y": target["y"]}
        append_event(data, "select_mercenary_card", target)
        save_inspection(data, path)
        return target

    if page < 3:
        data["scroll_count"] = page + 1
        target = {"id": f"scroll_page_{page + 1}", "kind": "scroll", "x": width // 2, "y": round(height * 0.77), "x2": width // 2, "y2": round(height * 0.36), "duration_ms": 650}
        append_event(data, "scroll_mercenary_list", target)
        save_inspection(data, path)
        return target
    return None


def scaled_target(target: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    return {
        "id": target["id"],
        "kind": target["kind"],
        "x": round(width * float(target["x_ratio"])),
        "y": round(height * float(target["y_ratio"])),
    }


def normalized_bounds(screen_bounds: tuple[int, int]) -> tuple[int, int]:
    width, height = screen_bounds
    if width <= 0 or height <= 0:
        return 360, 640
    return width, height


def append_event(data: dict[str, Any], event: str, target: dict[str, Any]) -> None:
    events = data.setdefault("events", [])
    events.append({"time": utc_now(), "event": event, "target": target})
    del events[:-200]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
