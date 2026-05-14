from __future__ import annotations

import json
from typing import Any

from explorer.goals import goal_description, resolve_goal
from explorer.learning_memory import load_learning_memory, summarize_learning_memory
from explorer.state_graph import StateGraph
from tools.candidates import Candidate, local_screen_analysis


def build_planner_prompt(
    *,
    graph: StateGraph,
    state_id: str,
    candidates: list[Candidate],
    current_goal: dict[str, Any] | str | None = None,
    recent_edge_limit: int = 12,
) -> str:
    tried_actions = sorted(graph.tried_candidate_ids(state_id))
    learning_memory = load_learning_memory()
    goal = resolve_goal(current_goal)
    payload = {
        "task": "Choose the next exploration action for the current Android game screen.",
        "rules": [
            "Select exactly one action.",
            "Prefer candidates that have not been tried from the current state.",
            "If every candidate was tried, choose back.",
            "Choose wait only when the current screen appears to need time before interaction.",
            "Do not assume hardcoded game rules; use only the provided state graph and candidates.",
        ],
        "action_schema": {
            "type": "tap_candidate | tap_xy | back | wait",
            "candidate_id": "string | null",
            "x": "int | null",
            "y": "int | null",
            "selected_active_layer": "normal | modal | unknown",
            "reason": "string",
        },
        "active_layer": "unknown",
        "local_active_layer_hint": "unknown",
        "current_goal": goal,
        "current_state_id": state_id,
        "candidates": [candidate_to_prompt(candidate) for candidate in candidates],
        "candidates_all": [candidate_to_prompt(candidate) for candidate in candidates],
        "candidates_by_layer": candidates_by_layer(candidates),
        "already_tried_candidate_ids": tried_actions,
        "learning_memory_summary": summarize_learning_memory(learning_memory, list(candidates)),
        "state_graph_summary": summarize_graph(graph, recent_edge_limit=recent_edge_limit),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_planner_request(
    *,
    graph: StateGraph,
    state_id: str,
    screenshot_path: str,
    debug_image_path: str,
    candidates: list[Candidate],
    goal: Any,
    current_goal: dict[str, Any] | str | None = None,
    recent_edge_limit: int = 12,
    direct_vision: bool = False,
) -> dict[str, Any]:
    learning_memory = load_learning_memory()
    resolved_goal = resolve_goal(current_goal or goal)
    local_analysis = safe_local_screen_analysis(screenshot_path)
    candidates_all = [candidate_to_prompt(candidate) for candidate in candidates]
    recent_edges = [summarize_edge(edge) for edge in graph.edges[-recent_edge_limit:]]
    direct_action_summary = summarize_direct_action_pressure(graph, state_id) if direct_vision else {}
    return {
        "state_id": state_id,
        "direct_vision": direct_vision,
        "active_layer": "unknown",
        "local_active_layer_hint": local_analysis["local_active_layer_hint"],
        "local_modal_score": local_analysis["local_modal_score"],
        "local_grid_like": local_analysis["local_grid_like"],
        "screen_bounds": local_analysis["screen_bounds"],
        "screenshot_path": screenshot_path,
        "debug_image_path": debug_image_path,
        "current_goal": resolved_goal,
        "candidates": candidates_all,
        "candidates_all": candidates_all,
        "candidates_by_layer": candidates_by_layer(candidates),
        "tried_candidates": sorted(graph.tried_candidate_ids(state_id)),
        "learning_memory_summary": {} if direct_vision else summarize_learning_memory(learning_memory, list(candidates)),
        "recent_edges": [] if direct_vision else recent_edges,
        "recent_direct_actions": summarize_recent_direct_actions(graph, state_id, limit=20) if direct_vision else [],
        "failed_tap_xy_this_state": summarize_failed_tap_xy(graph, state_id, limit=12) if direct_vision else [],
        "bottom_tap_transitions_this_state": summarize_bottom_tap_transitions(graph, state_id, limit=12) if direct_vision else [],
        "direct_action_summary": direct_action_summary,
        "goal": goal_description(resolved_goal),
        "action_schema": {
            "type": "tap_candidate | tap_xy | swipe | back | wait",
            "candidate_id": "string | null",
            "x": "int | null",
            "y": "int | null",
            "x2": "int | null",
            "y2": "int | null",
            "duration_ms": "int | null",
            "selected_active_layer": "normal | modal | unknown",
            "reason": "string",
        },
    }


def summarize_direct_action_pressure(graph: StateGraph, state_id: str) -> dict[str, Any]:
    current_edges = [edge for edge in graph.edges if edge.get("from_state") == state_id]
    failed_taps = 0
    failed_bottom_taps = 0
    failed_swipes = 0
    changed_bottom_taps = 0
    for edge in current_edges:
        action = edge.get("action")
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("type") or "")
        changed = edge.get("changed") is True
        y = numeric_int(action.get("y"))
        if action_type == "tap_xy" and not changed:
            failed_taps += 1
            if y is not None and y >= 560:
                failed_bottom_taps += 1
        if action_type == "swipe" and not changed:
            failed_swipes += 1
        if action_type == "tap_xy" and changed and y is not None and y >= 560:
            changed_bottom_taps += 1
    return {
        "current_state_edge_count": len(current_edges),
        "failed_tap_xy_count": failed_taps,
        "failed_bottom_tap_xy_count": failed_bottom_taps,
        "failed_swipe_count": failed_swipes,
        "changed_bottom_tap_xy_count": changed_bottom_taps,
        "bottom_band_blocked": failed_bottom_taps >= 2,
        "state_exhausted_hint": failed_taps >= 5 and failed_swipes >= 1,
    }


def summarize_recent_direct_actions(graph: StateGraph, state_id: str, *, limit: int) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for edge in graph.edges[-limit:]:
        action = edge.get("action")
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("type") or "")
        if action_type not in {"tap_xy", "swipe", "back", "wait"}:
            continue
        summaries.append(
            {
                "from_state": edge.get("from_state"),
                "to_state": edge.get("to_state"),
                "is_current_state": edge.get("from_state") == state_id,
                "type": action_type,
                "x": action.get("x"),
                "y": action.get("y"),
                "x2": action.get("x2"),
                "y2": action.get("y2"),
                "changed": edge.get("changed"),
                "reason": compact_reason(action.get("reason")),
            }
        )
    return summaries


def summarize_failed_tap_xy(graph: StateGraph, state_id: str, *, limit: int) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    for edge in reversed(graph.edges):
        if edge.get("from_state") != state_id or edge.get("changed") is not False:
            continue
        action = edge.get("action")
        if not isinstance(action, dict) or action.get("type") != "tap_xy":
            continue
        failed.append({"x": action.get("x"), "y": action.get("y"), "reason": compact_reason(action.get("reason"))})
        if len(failed) >= limit:
            break
    failed.reverse()
    return failed


def summarize_bottom_tap_transitions(graph: StateGraph, state_id: str, *, limit: int) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    for edge in reversed(graph.edges):
        if edge.get("from_state") != state_id:
            continue
        action = edge.get("action")
        if not isinstance(action, dict) or action.get("type") != "tap_xy":
            continue
        y = numeric_int(action.get("y"))
        if y is None or y < 560:
            continue
        transitions.append(
            {
                "x": action.get("x"),
                "y": action.get("y"),
                "to_state": edge.get("to_state"),
                "changed": edge.get("changed"),
                "reason": compact_reason(action.get("reason")),
            }
        )
        if len(transitions) >= limit:
            break
    transitions.reverse()
    return transitions


def numeric_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def compact_reason(value: object, *, max_length: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def prompt_summary(prompt: str, *, max_length: int = 500) -> str:
    compact = " ".join(prompt.split())
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 3] + "..."


def candidate_to_prompt(candidate: Candidate) -> dict[str, Any]:
    data = {
        "candidate_id": candidate.id,
        "x": candidate.x,
        "y": candidate.y,
        "kind": candidate.kind,
        "layer": candidate.layer,
        "layer_hint": candidate.layer_hint or candidate.layer,
        "score": candidate.score,
        "bbox": list(candidate.bbox) if candidate.bbox is not None else None,
        "visual_center": list(candidate.visual_center or (candidate.x, candidate.y)),
        "tap_point": list(candidate.tap_point or (candidate.x, candidate.y)),
        "label_guess": candidate.label_guess,
    }
    if candidate.parent is not None:
        data["parent"] = candidate.parent
    if candidate.group is not None:
        data["group"] = candidate.group
    return data


def candidates_by_layer(candidates: list[Candidate]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {"normal": [], "modal": [], "unknown": []}
    for candidate in candidates:
        layer = candidate.layer_hint or candidate.layer
        if layer not in grouped:
            layer = "unknown"
        grouped[layer].append(candidate_to_prompt(candidate))
    return grouped


def safe_local_screen_analysis(screenshot_path: str) -> dict[str, object]:
    try:
        return local_screen_analysis(screenshot_path)
    except (OSError, ValueError):
        return {
            "local_active_layer_hint": "unknown",
            "local_modal_score": 0.0,
            "local_grid_like": False,
            "screen_bounds": {"width": 0, "height": 0},
        }


def fast_screen_bounds_analysis(screenshot_path: str) -> dict[str, object]:
    try:
        from PIL import Image

        with Image.open(screenshot_path) as image:
            width, height = image.size
    except OSError:
        width, height = 0, 0
    return {
        "local_active_layer_hint": "unknown",
        "local_modal_score": 0.0,
        "local_grid_like": False,
        "screen_bounds": {"width": width, "height": height},
    }


def infer_active_layer(candidates: list[Candidate]) -> str:
    if any(candidate.layer == "modal" for candidate in candidates):
        return "modal"
    return "normal"


def summarize_graph(graph: StateGraph, *, recent_edge_limit: int) -> dict[str, Any]:
    recent_edges = graph.edges[-recent_edge_limit:]
    return {
        "state_count": len(graph.states),
        "edge_count": len(graph.edges),
        "recent_edges": [summarize_edge(edge) for edge in recent_edges],
    }


def summarize_edge(edge: dict[str, Any]) -> dict[str, Any]:
    action = edge.get("action", {})
    if not isinstance(action, dict):
        action = {"raw": action}
    return {
        "from_state": edge.get("from_state"),
        "to_state": edge.get("to_state"),
        "action": action,
        "changed": edge.get("changed"),
        "timestamp": edge.get("timestamp"),
    }
