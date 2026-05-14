from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_GOAL_ID = "explore_safely"
DEFAULT_GOAL_PROGRESS_PATH = Path("runtime/goal_progress.json")


GOALS: dict[str, dict[str, Any]] = {
    "explore_safely": {
        "goal_id": "explore_safely",
        "description": "Explore safely while avoiding irreversible or risky actions.",
        "priority": 1,
        "success_signals": ["changed_true", "new_state", "useful_screen_change"],
        "avoid_signals": ["repeated_changed_false", "exit_confirm", "purchase_or_logout_risk"],
        "preferred_candidate_kinds": ["popup_button", "bottom_menu", "contour", "bright_region"],
        "preferred_layers": ["modal", "normal"],
    },
    "dismiss_modal": {
        "goal_id": "dismiss_modal",
        "description": "Dismiss the active modal safely without confirming exit or destructive choices.",
        "priority": 10,
        "success_signals": ["modal_dismissed", "active_layer_changed_from_modal", "changed_true"],
        "avoid_signals": ["modal_still_active", "confirm_or_exit_candidate", "repeated_changed_false"],
        "preferred_candidate_kinds": ["popup_button", "fixed", "back"],
        "preferred_layers": ["modal"],
    },
    "find_progression": {
        "goal_id": "find_progression",
        "description": "Find actions that advance to a new screen, next state, stage, or progress flow.",
        "priority": 7,
        "success_signals": ["changed_true", "new_state", "large_screen_change"],
        "avoid_signals": ["repeated_changed_false", "modal_not_dismissed"],
        "preferred_candidate_kinds": ["contour", "bright_region", "popup_button", "fixed"],
        "preferred_layers": ["normal", "modal"],
    },
    "collect_rewards": {
        "goal_id": "collect_rewards",
        "description": "Prefer highlighted or reward-like safe actions while avoiding purchase or exit risk.",
        "priority": 5,
        "success_signals": ["changed_true", "reward_screen_change", "new_state"],
        "avoid_signals": ["purchase_or_logout_risk", "exit_confirm", "repeated_changed_false"],
        "preferred_candidate_kinds": ["bright_region", "popup_button", "contour", "fixed"],
        "preferred_layers": ["normal", "modal"],
    },
    "explore_menu": {
        "goal_id": "explore_menu",
        "description": "Explore bottom navigation and menu surfaces to discover available screens.",
        "priority": 6,
        "success_signals": ["changed_true", "new_state", "menu_screen_change"],
        "avoid_signals": ["repeated_changed_false", "modal_not_dismissed"],
        "preferred_candidate_kinds": ["bottom_menu", "fixed", "contour"],
        "preferred_layers": ["normal"],
    },
    "inspect_mercenary_synergy": {
        "goal_id": "inspect_mercenary_synergy",
        "description": "Increase game understanding by opening and studying all Mercenary information and all Synergy information. The Korean UI terms to look for are '용병' and '시너지'. Search the full screen for a real entry point such as a 용병/Mercenary label, character/roster/card/book/menu button, or synergy/detail panel, then inspect every mercenary card/detail and every synergy icon/detail. Do not assume the target is a bottom navigation tab and do not rely on synthetic 5-split bottom navigation geometry.",
        "priority": 9,
        "success_signals": ["changed_true", "new_state", "mercenary_tab_opened", "synergy_detail_opened", "mercenary_info_collected", "synergy_info_collected"],
        "avoid_signals": ["repeated_changed_false", "modal_not_dismissed", "modal_opened", "tiny_icon_mistap"],
        "preferred_candidate_kinds": ["bottom_menu", "contour", "bright_region", "fixed"],
        "preferred_layers": ["normal"],
    },
}


def resolve_goal(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        goal_id = str(value.get("goal_id") or DEFAULT_GOAL_ID)
        goal = deepcopy(GOALS.get(goal_id, GOALS[DEFAULT_GOAL_ID]))
        goal.update({key: val for key, val in value.items() if key in goal})
        return goal
    goal_id = str(value or DEFAULT_GOAL_ID)
    return deepcopy(GOALS.get(goal_id, GOALS[DEFAULT_GOAL_ID]))


def goal_description(goal: dict[str, Any]) -> str:
    return str(goal.get("description") or "Explore the game safely and identify useful screens and actions.")


def load_goal_progress(path: str | Path = DEFAULT_GOAL_PROGRESS_PATH) -> dict[str, Any]:
    progress_path = Path(path)
    if not progress_path.exists():
        return {"version": 1, "goals": {}}
    try:
        raw = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "goals": {}}
    if not isinstance(raw, dict):
        return {"version": 1, "goals": {}}
    if not isinstance(raw.get("goals"), dict):
        raw["goals"] = {}
    raw["version"] = raw.get("version", 1)
    return raw


def append_goal_progress(
    path: str | Path,
    *,
    goal: dict[str, Any],
    action_type: str,
    candidate_id: str | None,
    changed: bool,
    before_state_id: str,
    after_state_id: str,
    active_layer_before: str,
    active_layer_after: str,
) -> dict[str, Any]:
    progress = load_goal_progress(path)
    goal_id = str(goal.get("goal_id") or DEFAULT_GOAL_ID)
    goals = progress.setdefault("goals", {})
    record = goals.setdefault(
        goal_id,
        {
            "goal": goal,
            "attempt_count": 0,
            "success_count": 0,
            "fail_count": 0,
            "success_signals_seen": {},
            "avoid_signals_seen": {},
            "recent_attempts": [],
        },
    )
    success_signals, avoid_signals = classify_goal_outcome(
        changed=changed,
        before_state_id=before_state_id,
        after_state_id=after_state_id,
        active_layer_before=active_layer_before,
        active_layer_after=active_layer_after,
    )
    success = bool(success_signals) and not avoid_signals
    record["attempt_count"] = int(record.get("attempt_count", 0)) + 1
    record["success_count"] = int(record.get("success_count", 0)) + (1 if success else 0)
    record["fail_count"] = int(record.get("fail_count", 0)) + (0 if success else 1)
    increment_signal_counts(record.setdefault("success_signals_seen", {}), success_signals)
    increment_signal_counts(record.setdefault("avoid_signals_seen", {}), avoid_signals)
    recent = record.setdefault("recent_attempts", [])
    recent.append(
        {
            "action_type": action_type,
            "candidate_id": candidate_id,
            "changed": changed,
            "before_state_id": before_state_id,
            "after_state_id": after_state_id,
            "active_layer_before": active_layer_before,
            "active_layer_after": active_layer_after,
            "success_signals": success_signals,
            "avoid_signals": avoid_signals,
        }
    )
    del recent[:-25]
    progress_path = Path(path)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    return progress


def classify_goal_outcome(
    *,
    changed: bool,
    before_state_id: str,
    after_state_id: str,
    active_layer_before: str,
    active_layer_after: str,
) -> tuple[list[str], list[str]]:
    success: list[str] = []
    avoid: list[str] = []
    if changed:
        success.append("changed_true")
    else:
        avoid.append("changed_false")
    if before_state_id != after_state_id:
        success.append("new_state")
    if active_layer_before == "modal" and active_layer_after != "modal":
        success.append("modal_dismissed")
        success.append("active_layer_changed_from_modal")
    if active_layer_before == "modal" and active_layer_after == "modal" and not changed:
        avoid.append("modal_still_active")
        avoid.append("modal_not_dismissed")
    if active_layer_before != "modal" and active_layer_after == "modal":
        avoid.append("modal_opened")
    return success, avoid


def increment_signal_counts(counts: dict[str, int], signals: list[str]) -> None:
    for signal in signals:
        counts[signal] = int(counts.get(signal, 0)) + 1
