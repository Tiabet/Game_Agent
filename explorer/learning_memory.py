from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_LEARNING_MEMORY_PATH = Path("runtime/learning_memory.json")
MEMORY_VERSION = 1


def empty_learning_memory() -> dict[str, Any]:
    return {"version": MEMORY_VERSION, "patterns": {}}


def load_learning_memory(path: str | Path = DEFAULT_LEARNING_MEMORY_PATH) -> dict[str, Any]:
    memory_path = Path(path)
    if not memory_path.exists():
        return empty_learning_memory()
    try:
        raw = json.loads(memory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_learning_memory()
    if not isinstance(raw, dict):
        return empty_learning_memory()
    patterns = raw.get("patterns")
    if not isinstance(patterns, dict):
        raw["patterns"] = {}
    raw["version"] = raw.get("version", MEMORY_VERSION)
    return raw


def save_learning_memory(path: str | Path, memory: dict[str, Any]) -> None:
    memory_path = Path(path)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")


def update_learning_memory(
    path: str | Path,
    *,
    action_type: str,
    candidate: object | None,
    changed: bool,
    active_layer_before: str,
    active_layer_after: str,
    before_state_id: str,
    after_state_id: str,
) -> dict[str, Any]:
    memory = load_learning_memory(path)
    features = pattern_features(candidate, action_type=action_type, active_layer=active_layer_before)
    key = pattern_key(features)
    patterns = memory.setdefault("patterns", {})
    record = patterns.setdefault(key, new_pattern_record(features))

    success = bool(changed)
    modal_dismiss_success = action_type == "back" and active_layer_before == "modal" and active_layer_after != "modal"
    if modal_dismiss_success:
        success = True

    record["success_count"] = int(record.get("success_count", 0)) + (1 if success else 0)
    record["fail_count"] = int(record.get("fail_count", 0)) + (0 if success else 1)
    record["changed_true_count"] = int(record.get("changed_true_count", 0)) + (1 if changed else 0)
    record["changed_false_count"] = int(record.get("changed_false_count", 0)) + (0 if changed else 1)
    if modal_dismiss_success:
        record["modal_dismiss_success_count"] = int(record.get("modal_dismiss_success_count", 0)) + 1
    record["last_outcome"] = {
        "changed": changed,
        "success": success,
        "modal_dismiss_success": modal_dismiss_success,
        "before_state_id": before_state_id,
        "after_state_id": after_state_id,
    }
    save_learning_memory(path, memory)
    return memory


def new_pattern_record(features: dict[str, str]) -> dict[str, Any]:
    return {
        "features": features,
        "success_count": 0,
        "fail_count": 0,
        "changed_true_count": 0,
        "changed_false_count": 0,
        "modal_dismiss_success_count": 0,
        "last_outcome": None,
    }


def pattern_features(candidate: object | None, *, action_type: str = "tap_candidate", active_layer: str = "normal") -> dict[str, str]:
    if candidate is None:
        kind = action_type
        layer = active_layer
        group = "modal" if active_layer == "modal" else "none"
        x = None
        y = None
        bbox = None
    else:
        kind = string_field(candidate, "kind") or action_type
        layer = string_field(candidate, "layer") or active_layer
        group = string_field(candidate, "group") or string_field(candidate, "parent") or "none"
        x = numeric_field(candidate, "x")
        y = numeric_field(candidate, "y")
        bbox = bbox_field(candidate)

    return {
        "kind": kind,
        "layer": layer,
        "group_or_parent": group,
        "relative_position_bucket": relative_position_bucket(x, y),
        "bbox_size_bucket": bbox_size_bucket(bbox),
    }


def pattern_key(features: dict[str, str]) -> str:
    return "|".join(
        [
            features.get("kind", "unknown"),
            features.get("layer", "unknown"),
            features.get("group_or_parent", "none"),
            features.get("relative_position_bucket", "unknown"),
            features.get("bbox_size_bucket", "unknown"),
        ]
    )


def stats_for_candidate(memory: dict[str, Any], candidate: object, *, action_type: str = "tap_candidate", active_layer: str = "normal") -> dict[str, Any] | None:
    features = pattern_features(candidate, action_type=action_type, active_layer=active_layer)
    patterns = memory.get("patterns")
    if not isinstance(patterns, dict):
        return None
    stats = patterns.get(pattern_key(features))
    return stats if isinstance(stats, dict) else None


def learning_adjustment_for_candidate(memory: dict[str, Any], candidate: object, *, active_layer: str = "normal") -> tuple[float, list[tuple[str, float]]]:
    stats = stats_for_candidate(memory, candidate, active_layer=active_layer)
    if stats is None:
        return 0.0, []
    success_count = int(stats.get("success_count", 0))
    fail_count = int(stats.get("fail_count", 0))
    changed_true_count = int(stats.get("changed_true_count", 0))
    changed_false_count = int(stats.get("changed_false_count", 0))

    adjustments: list[tuple[str, float]] = []
    if success_count:
        adjustments.append(("learning_success_pattern_bonus", min(0.40, 0.08 * success_count + 0.03 * changed_true_count)))
    if changed_false_count >= 2:
        adjustments.append(("learning_repeated_false_pattern_penalty", max(-0.65, -0.08 * changed_false_count)))
    if fail_count > success_count:
        adjustments.append(("learning_fail_dominant_pattern_penalty", max(-0.45, -0.06 * (fail_count - success_count))))
    elif success_count > fail_count:
        adjustments.append(("learning_success_dominant_pattern_bonus", min(0.25, 0.05 * (success_count - fail_count))))
    return sum(value for _, value in adjustments), adjustments


def summarize_learning_memory(memory: dict[str, Any], candidates: list[object] | None = None, *, limit: int = 5) -> dict[str, Any]:
    patterns = memory.get("patterns") if isinstance(memory, dict) else None
    if not isinstance(patterns, dict):
        patterns = {}
    records = [(key, value) for key, value in patterns.items() if isinstance(value, dict)]
    top_success = sorted(records, key=lambda item: int(item[1].get("success_count", 0)), reverse=True)[:limit]
    top_failure = sorted(records, key=lambda item: int(item[1].get("changed_false_count", 0)), reverse=True)[:limit]
    summary: dict[str, Any] = {
        "version": memory.get("version", MEMORY_VERSION) if isinstance(memory, dict) else MEMORY_VERSION,
        "pattern_count": len(records),
        "top_success_patterns": [summary_record(key, record) for key, record in top_success if int(record.get("success_count", 0)) > 0],
        "top_failure_patterns": [summary_record(key, record) for key, record in top_failure if int(record.get("changed_false_count", 0)) > 0],
    }
    if candidates is not None:
        summary["current_candidate_patterns"] = current_candidate_pattern_summary(memory, candidates, limit=20)
    return summary


def current_candidate_pattern_summary(memory: dict[str, Any], candidates: list[object], *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for candidate in candidates[:limit]:
        features = pattern_features(candidate)
        key = pattern_key(features)
        stats = stats_for_candidate(memory, candidate)
        item: dict[str, Any] = {
            "candidate_id": string_field(candidate, "candidate_id") or string_field(candidate, "id"),
            "pattern_key": key,
            "features": features,
        }
        if stats is not None:
            item["stats"] = compact_stats(stats)
        items.append(item)
    return items


def summary_record(key: str, record: dict[str, Any]) -> dict[str, Any]:
    return {"pattern_key": key, "features": record.get("features", {}), "stats": compact_stats(record)}


def compact_stats(record: dict[str, Any]) -> dict[str, int]:
    return {
        "success_count": int(record.get("success_count", 0)),
        "fail_count": int(record.get("fail_count", 0)),
        "changed_true_count": int(record.get("changed_true_count", 0)),
        "changed_false_count": int(record.get("changed_false_count", 0)),
        "modal_dismiss_success_count": int(record.get("modal_dismiss_success_count", 0)),
    }


def relative_position_bucket(x: float | None, y: float | None, *, width: int = 360, height: int = 640) -> str:
    if x is None or y is None:
        return "action"
    x_ratio = max(0.0, min(0.999, x / max(width, 1)))
    y_ratio = max(0.0, min(0.999, y / max(height, 1)))
    x_bucket = ("left", "center", "right")[min(2, int(x_ratio * 3))]
    y_bucket = ("top", "upper_mid", "lower_mid", "bottom")[min(3, int(y_ratio * 4))]
    return f"{y_bucket}_{x_bucket}"


def bbox_size_bucket(bbox: list[float] | tuple[float, ...] | None) -> str:
    if bbox is None or len(bbox) != 4:
        return "none"
    area = max(0.0, float(bbox[2]) * float(bbox[3]))
    if area < 250:
        return "tiny"
    if area < 900:
        return "small"
    if area < 4_000:
        return "medium"
    if area < 20_000:
        return "large"
    return "huge"


def string_field(item: object, name: str) -> str | None:
    value = field_value(item, name)
    return value if isinstance(value, str) else None


def numeric_field(item: object, name: str) -> float | None:
    value = field_value(item, name)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def bbox_field(item: object) -> list[float] | None:
    value = field_value(item, "bbox")
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    numbers = []
    for part in value:
        try:
            numbers.append(float(part))
        except (TypeError, ValueError):
            return None
    return numbers


def field_value(item: object, name: str) -> object:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)
