from __future__ import annotations

import json
import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from explorer.prompt_builder import build_planner_request
from explorer.state_graph import StateGraph
from tools.candidates import Candidate


DIRECT_VISION_PROTOCOL_VERSION = "direct_vision_v3"


@dataclass(frozen=True)
class PlannerAction:
    type: str
    candidate_id: str | None
    reason: str
    response_source: str = "mock"
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    duration_ms: int | None = None
    selected_active_layer: str = "unknown"
    memory_updates: list[str] | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class BasePlanner(ABC):
    @abstractmethod
    def choose_action(
        self,
        *,
        prompt: str,
        graph: StateGraph,
        state_id: str,
        candidates: list[Candidate],
        screenshot_path: str | None = None,
        debug_image_path: str | None = None,
        goal: Any = "Explore the game safely and identify useful screens and actions.",
        direct_vision: bool = False,
    ) -> PlannerAction:
        raise NotImplementedError


class MockPlanner(BasePlanner):
    def choose_action(
        self,
        *,
        prompt: str,
        graph: StateGraph,
        state_id: str,
        candidates: list[Candidate],
        screenshot_path: str | None = None,
        debug_image_path: str | None = None,
        goal: Any = "Explore the game safely and identify useful screens and actions.",
        direct_vision: bool = False,
    ) -> PlannerAction:
        tried = graph.tried_candidate_ids(state_id)
        for candidate in candidates:
            if candidate.id not in tried:
                return PlannerAction(
                    type="tap_candidate",
                    candidate_id=candidate.id,
                    reason="MockPlanner selected the first candidate not yet tried from this state.",
                )
        return PlannerAction(
            type="back",
            candidate_id=None,
            reason="MockPlanner found no untried candidates in the current state.",
        )


class ExternalFilePlanner(BasePlanner):
    def __init__(
        self,
        *,
        request_path: str | Path = "runtime/planner_request.json",
        response_path: str | Path = "runtime/planner_response.json",
        wait_for_response: bool = False,
        response_timeout_sec: float = 300.0,
        clear_response_after_use: bool = False,
    ) -> None:
        self.request_path = Path(request_path)
        self.response_path = Path(response_path)
        self.wait_for_response = wait_for_response
        self.response_timeout_sec = response_timeout_sec
        self.clear_response_after_use = clear_response_after_use

    def choose_action(
        self,
        *,
        prompt: str,
        graph: StateGraph,
        state_id: str,
        candidates: list[Candidate],
        screenshot_path: str | None = None,
        debug_image_path: str | None = None,
        goal: Any = "Explore the game safely and identify useful screens and actions.",
        direct_vision: bool = False,
    ) -> PlannerAction:
        request_id = self.write_request(
            graph=graph,
            state_id=state_id,
            screenshot_path=screenshot_path or "",
            debug_image_path=debug_image_path or "",
            candidates=candidates,
            goal=goal,
            direct_vision=direct_vision,
        )
        if self.wait_for_response:
            return self.wait_for_response_action(candidates, request_id, screen_bounds=screenshot_bounds(screenshot_path))
        if not self.response_path.exists():
            return PlannerAction("wait", None, "External planner response file was not found; waiting safely.", "external_timeout")
        action = self.read_response_action(candidates, request_id, wait_on_request_mismatch=False, screen_bounds=screenshot_bounds(screenshot_path))
        if action is None:
            return PlannerAction("wait", None, f"External planner response request_id did not match current request_id: {request_id}", "external_invalid")
        return action

    def wait_for_response_action(self, candidates: list[Candidate], request_id: str, *, screen_bounds: tuple[int, int]) -> PlannerAction:
        deadline = time.monotonic() + self.response_timeout_sec
        saw_mismatched_response = False
        while time.monotonic() < deadline:
            if self.response_path.exists():
                action = self.read_response_action(candidates, request_id, wait_on_request_mismatch=True, screen_bounds=screen_bounds)
                if action is not None:
                    return action
                saw_mismatched_response = True
            time.sleep(1.0)
        if saw_mismatched_response:
            return PlannerAction(
                "wait",
                None,
                f"External planner response timed out waiting for matching request_id: {request_id}",
                "external_timeout",
            )
        return PlannerAction("wait", None, f"External planner response timed out after {self.response_timeout_sec:.1f}s.", "external_timeout")

    def read_response_action(self, candidates: list[Candidate], request_id: str, *, wait_on_request_mismatch: bool, screen_bounds: tuple[int, int]) -> PlannerAction | None:
        try:
            raw = json.loads(self.response_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return PlannerAction("wait", None, f"External planner response could not be read: {exc}", "external_invalid")

        if isinstance(raw, dict):
            response_request_id = raw.get("request_id")
            if response_request_id != request_id:
                if wait_on_request_mismatch:
                    self.clear_response_file()
                    return None
                action = PlannerAction(
                    "wait",
                    None,
                    f"External planner response request_id mismatch: expected {request_id}, got {response_request_id}",
                    "external_invalid",
                )
                self.clear_response_file()
                return action

        action = validate_external_action(raw, candidates, screen_bounds=screen_bounds)
        self.clear_response_file()
        return action

    def clear_response_file(self) -> None:
        if not self.clear_response_after_use:
            return
        try:
            self.response_path.unlink(missing_ok=True)
        except OSError:
            pass

    def write_request(
        self,
        *,
        graph: StateGraph,
        state_id: str,
        screenshot_path: str,
        debug_image_path: str,
        candidates: list[Candidate],
        goal: Any,
        direct_vision: bool,
    ) -> str:
        payload = build_planner_request(
            graph=graph,
            state_id=state_id,
            screenshot_path=screenshot_path,
            debug_image_path=debug_image_path,
            candidates=candidates,
            goal=goal,
            current_goal=goal,
            direct_vision=direct_vision,
        )
        request_id = make_request_id(state_id, screenshot_path, direct_vision=direct_vision, goal=goal, request_payload=payload)
        payload["request_id"] = request_id
        self.request_path.parent.mkdir(parents=True, exist_ok=True)
        self.request_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return request_id


def validate_external_action(raw: object, candidates: list[Candidate], *, screen_bounds: tuple[int, int] = (0, 0)) -> PlannerAction:
    if not isinstance(raw, dict):
        return PlannerAction("wait", None, "External planner response was not a JSON object.", "external_invalid")

    action_type = raw.get("type")
    candidate_id = raw.get("candidate_id")
    selected_active_layer = str(raw.get("selected_active_layer") or "unknown")
    if selected_active_layer not in {"normal", "modal", "unknown"}:
        selected_active_layer = "unknown"
    reason = str(raw.get("reason") or "External planner selected this action.")
    memory_updates = raw.get("memory_updates") if isinstance(raw.get("memory_updates"), list) else []
    memory_updates = [str(item) for item in memory_updates if isinstance(item, str) and item.strip()]
    if action_type not in {"tap_candidate", "tap_xy", "swipe", "back", "wait"}:
        return PlannerAction("wait", None, f"External planner returned invalid action type: {action_type}", "external_invalid")
    if action_type == "tap_candidate":
        valid_ids = {candidate.id for candidate in candidates}
        if not isinstance(candidate_id, str) or candidate_id not in valid_ids:
            return PlannerAction("wait", None, f"External planner returned unknown candidate_id: {candidate_id}", "external_invalid")
        return PlannerAction("tap_candidate", candidate_id, reason, "external_file", selected_active_layer=selected_active_layer, memory_updates=memory_updates)
    if action_type == "tap_xy":
        x = int_value(raw.get("x"))
        y = int_value(raw.get("y"))
        width, height = screen_bounds
        if x is None or y is None or width <= 0 or height <= 0 or not (0 <= x < width and 0 <= y < height):
            return PlannerAction("wait", None, f"External planner returned invalid tap_xy coordinates: x={raw.get('x')} y={raw.get('y')} bounds={screen_bounds}", "external_invalid")
        return PlannerAction("tap_xy", None, reason, "external_file", x=x, y=y, selected_active_layer=selected_active_layer, memory_updates=memory_updates)
    if action_type == "swipe":
        x = int_value(raw.get("x"))
        y = int_value(raw.get("y"))
        x2 = int_value(raw.get("x2"))
        y2 = int_value(raw.get("y2"))
        duration_ms = int_value(raw.get("duration_ms")) or 350
        width, height = screen_bounds
        if any(value is None for value in (x, y, x2, y2)) or width <= 0 or height <= 0:
            return PlannerAction("wait", None, f"External planner returned invalid swipe coordinates: {raw}", "external_invalid")
        if not (0 <= x < width and 0 <= y < height and 0 <= x2 < width and 0 <= y2 < height):
            return PlannerAction("wait", None, f"External planner returned out-of-bounds swipe coordinates: {raw} bounds={screen_bounds}", "external_invalid")
        return PlannerAction("swipe", None, reason, "external_file", x=x, y=y, x2=x2, y2=y2, duration_ms=duration_ms, selected_active_layer=selected_active_layer, memory_updates=memory_updates)
    return PlannerAction(str(action_type), None, reason, "external_file", selected_active_layer=selected_active_layer, memory_updates=memory_updates)


def int_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def screenshot_bounds(screenshot_path: str | None) -> tuple[int, int]:
    if not screenshot_path:
        return (0, 0)
    try:
        with Image.open(screenshot_path) as image:
            return image.size
    except OSError:
        return (0, 0)


def make_request_id(state_id: str, screenshot_path: str, *, direct_vision: bool = False, goal: Any = None, request_payload: dict[str, Any] | None = None) -> str:
    if direct_vision:
        goal_key = goal_identity(goal)
        context_key = direct_context_identity(request_payload)
        short_hash = hashlib.sha256(f"direct:{DIRECT_VISION_PROTOCOL_VERSION}:{state_id}:{goal_key}:{context_key}".encode("utf-8")).hexdigest()[:12]
        return f"direct_{state_id}_{short_hash}"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    short_hash = hashlib.sha256(f"{timestamp}:{state_id}:{screenshot_path}".encode("utf-8")).hexdigest()[:8]
    return f"{timestamp}_{state_id}_{short_hash}"


def goal_identity(goal: Any) -> str:
    if isinstance(goal, dict):
        return str(goal.get("goal_id") or goal.get("description") or goal)
    return str(goal)


def direct_context_identity(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    context = {
        "local_active_layer_hint": payload.get("local_active_layer_hint"),
        "local_modal_score": payload.get("local_modal_score"),
        "direct_action_summary": payload.get("direct_action_summary"),
        "recent_direct_actions": payload.get("recent_direct_actions"),
        "failed_tap_xy_this_state": payload.get("failed_tap_xy_this_state"),
        "bottom_tap_transitions_this_state": payload.get("bottom_tap_transitions_this_state"),
    }
    return hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:12]


def append_planner_decision(
    log_path: str | Path,
    *,
    state_id: str,
    selected_action: PlannerAction,
    prompt_summary: str,
    local_active_layer_hint: str = "unknown",
) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state_id": state_id,
        "selected_action": selected_action.to_dict(),
        "selected_active_layer": selected_action.selected_active_layer,
        "local_active_layer_hint": local_active_layer_hint,
        "used_tap_xy": selected_action.type == "tap_xy",
        "reason": selected_action.reason,
        "response_source": selected_action.response_source,
        "prompt_summary": prompt_summary,
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
