from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.env import GameEnvironment


VALID_ACTIONS = {"tap", "swipe", "back", "wait", "none"}


@dataclass(frozen=True)
class Action:
    action: str
    x: int | None = None
    y: int | None = None
    x1: int | None = None
    y1: int | None = None
    x2: int | None = None
    y2: int | None = None
    duration_ms: int = 300
    seconds: float = 1.0
    reason: str = ""


def parse_action(payload: dict[str, Any]) -> Action:
    action = payload.get("action")
    if action not in VALID_ACTIONS:
        raise ValueError(f"Invalid action: {action!r}. Expected one of {sorted(VALID_ACTIONS)}")

    if action == "tap":
        return Action(
            action=action,
            x=_required_int(payload, "x"),
            y=_required_int(payload, "y"),
            reason=str(payload.get("reason", "")),
        )

    if action == "swipe":
        return Action(
            action=action,
            x1=_required_int(payload, "x1"),
            y1=_required_int(payload, "y1"),
            x2=_required_int(payload, "x2"),
            y2=_required_int(payload, "y2"),
            duration_ms=_optional_int(payload, "duration_ms", 300),
            reason=str(payload.get("reason", "")),
        )

    if action == "wait":
        return Action(
            action=action,
            seconds=_optional_float(payload, "seconds", 1.0),
            reason=str(payload.get("reason", "")),
        )

    return Action(action=action, reason=str(payload.get("reason", "")))


def execute_action(env: GameEnvironment, action: Action, *, dry_run: bool = True) -> None:
    print(format_action(action))

    if dry_run:
        print("Dry run: action was not sent to LDPlayer.")
        return

    if action.action == "tap":
        env.tap(_assert_int(action.x), _assert_int(action.y))
    elif action.action == "swipe":
        env.swipe(
            _assert_int(action.x1),
            _assert_int(action.y1),
            _assert_int(action.x2),
            _assert_int(action.y2),
            action.duration_ms,
        )
    elif action.action == "back":
        env.back()
    elif action.action == "wait":
        env.wait(action.seconds)
    elif action.action == "none":
        return
    else:
        raise ValueError(f"Unsupported action: {action.action}")


def format_action(action: Action) -> str:
    reason = f" reason={action.reason!r}" if action.reason else ""

    if action.action == "tap":
        return f"Action: tap x={action.x} y={action.y}{reason}"
    if action.action == "swipe":
        return (
            f"Action: swipe x1={action.x1} y1={action.y1} "
            f"x2={action.x2} y2={action.y2} duration_ms={action.duration_ms}{reason}"
        )
    if action.action == "wait":
        return f"Action: wait seconds={action.seconds}{reason}"
    return f"Action: {action.action}{reason}"


def _required_int(payload: dict[str, Any], key: str) -> int:
    if key not in payload:
        raise ValueError(f"Missing required field for action: {key}")
    return _to_int(payload[key], key)


def _optional_int(payload: dict[str, Any], key: str, default: int) -> int:
    if key not in payload:
        return default
    return _to_int(payload[key], key)


def _optional_float(payload: dict[str, Any], key: str, default: float) -> float:
    if key not in payload:
        return default
    try:
        return float(payload[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Field must be a number: {key}") from exc


def _to_int(value: Any, key: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Field must be an integer: {key}") from exc


def _assert_int(value: int | None) -> int:
    if value is None:
        raise ValueError("Expected integer action coordinate, got None")
    return value
