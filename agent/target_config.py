from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_TARGETS_PATH = Path("config/targets.json")


@dataclass(frozen=True)
class TargetPoint:
    name: str
    x: int
    y: int
    description: str = ""


def load_target(name: str, path: str | Path = DEFAULT_TARGETS_PATH) -> TargetPoint:
    config_path = Path(path)
    data = _load_json(config_path)

    if name not in data:
        raise ValueError(f"Target not found in {config_path}: {name}")

    target = data[name]
    return TargetPoint(
        name=name,
        x=_required_int(target, "x"),
        y=_required_int(target, "y"),
        description=str(target.get("description", "")),
    )


def save_target(
    name: str,
    x: int,
    y: int,
    *,
    description: str = "",
    path: str | Path = DEFAULT_TARGETS_PATH,
) -> Path:
    config_path = Path(path)
    data = _load_json(config_path) if config_path.exists() else {}
    data[name] = {"x": int(x), "y": int(y), "description": description}

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return config_path


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Target config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _required_int(payload: dict[str, Any], key: str) -> int:
    if key not in payload:
        raise ValueError(f"Missing target field: {key}")
    try:
        return int(payload[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Target field must be an integer: {key}") from exc
