from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from explorer.goals import resolve_goal
from explorer.learning_memory import DEFAULT_LEARNING_MEMORY_PATH, learning_adjustment_for_candidate, load_learning_memory, summarize_learning_memory


DEFAULT_REQUEST_PATH = Path("runtime/planner_request.json")
DEFAULT_RESPONSE_PATH = Path("runtime/planner_response.json")
DEFAULT_ACTIONS_LOG_PATH = Path("runtime/actions.jsonl")
DEFAULT_STATE_GRAPH_PATH = Path("runtime/state_graph.json")
DEFAULT_OPENCODE_PROMPT_PATH = Path("runtime/opencode_planner_prompt.md")
DEFAULT_OPENCODE_ERROR_LOG_PATH = Path("runtime/opencode_bridge_error.log")
DEFAULT_OPENCODE_LAST_LOG_PATH = Path("runtime/opencode_bridge_last.log")
DEFAULT_OPENCODE_KNOWLEDGE_LOG_PATH = Path("runtime/opencode_knowledge_last.log")
KIND_WEIGHTS = {
    "bottom_menu": 0.20,
    "contour": 0.15,
    "fixed": 0.05,
    "bright_region": -0.10,
    "popup": 0.05,
    "modal": 0.05,
    "popup_button": 0.45,
}


@dataclass(frozen=True)
class Ranking:
    candidate: dict[str, Any]
    candidate_id: str
    kind: str
    raw_score: float
    adjusted_score: float
    breakdown: list[tuple[str, float]]


class MockBridge:
    def __init__(self, *, min_adjusted_score: float = 0.05, preference: str = "exploration") -> None:
        self.min_adjusted_score = min_adjusted_score
        self.preference = preference

    def choose_action(self, request: dict[str, Any], excluded_ids: set[str], action_records: list[dict[str, Any]], learning_memory: dict[str, Any] | None = None) -> dict[str, object]:
        candidates = request.get("candidates")
        selected_active_layer = active_layer(request, candidates)
        if not isinstance(candidates, list) or not candidates:
            if selected_active_layer == "modal":
                return modal_exhausted_back_action()
            return {
                "type": "wait",
                "candidate_id": None,
                "x": None,
                "y": None,
                "selected_active_layer": selected_active_layer,
                "reason": "MockBridge found no candidates in planner_request.json.",
            }

        valid_candidates = [candidate for candidate in candidates if self.candidate_id(candidate)]
        popup_boxes = popup_bboxes(candidates)
        popup_active = selected_active_layer == "modal"
        goal = resolve_goal(request.get("current_goal") or request.get("goal"))
        untried_candidates = [candidate for candidate in valid_candidates if not is_excluded_candidate(candidate, excluded_ids)]
        if popup_active:
            popup_candidates_only = [candidate for candidate in untried_candidates if is_popup_active_candidate(candidate, popup_boxes)]
            if not popup_candidates_only:
                return modal_exhausted_back_action()
            untried_candidates = popup_candidates_only
        else:
            normal_candidates = [candidate for candidate in untried_candidates if string_value(candidate.get("layer")) != "modal"]
            if normal_candidates:
                untried_candidates = normal_candidates
        if not untried_candidates:
            if popup_active:
                return modal_exhausted_back_action()
            return {
                "type": "back",
                "candidate_id": None,
                "x": None,
                "y": None,
                "selected_active_layer": selected_active_layer,
                "reason": "MockBridge found all candidates already tried in this state or screen hash.",
            }

        learning_memory = learning_memory or {}
        rankings = [self.rank_candidate(candidate, action_records, popup_boxes, popup_active, learning_memory, goal) for candidate in untried_candidates]
        for ranking in sorted(rankings, key=lambda item: item.adjusted_score, reverse=True)[:10]:
            print(format_ranking_log(ranking), flush=True)

        selected = max(rankings, key=lambda item: item.adjusted_score)
        if selected.adjusted_score < self.min_adjusted_score:
            return {
                "type": "back",
                "candidate_id": None,
                "reason": f"MockBridge found no candidate above min_adjusted_score={self.min_adjusted_score:.3f}; best={selected.candidate_id} adjusted_score={selected.adjusted_score:.3f}.",
            }

        reason_prefix = ""
        if popup_active:
            reason_prefix = f"popup active, {popup_safety_reason(selected.candidate_id)}; "
        return {
            "type": "tap_candidate",
            "candidate_id": selected.candidate_id,
            "x": None,
            "y": None,
            "selected_active_layer": selected_active_layer,
            "reason": f"{reason_prefix}MockBridge selected {selected.candidate_id} kind={selected.kind} bbox={format_bbox(selected.candidate)} adjusted_score={selected.adjusted_score:.3f} raw_score={selected.raw_score:.3f}; {summarize_breakdown(selected.breakdown)}.",
        }

    def rank_candidate(
        self,
        candidate: dict[str, Any],
        action_records: list[dict[str, Any]],
        popup_boxes: list[tuple[str, list[float]]],
        popup_active: bool,
        learning_memory: dict[str, Any],
        goal: dict[str, Any],
    ) -> Ranking:
        candidate_id = self.candidate_id(candidate) or ""
        kind = string_value(candidate.get("kind")) or "unknown"
        raw_score = self.candidate_score(candidate)
        breakdown = [("raw_score", raw_score)]
        breakdown.append((f"kind:{kind}", KIND_WEIGHTS.get(kind, 0.0)))
        breakdown.extend(self.bbox_penalties(candidate))
        breakdown.extend(self.position_penalties(candidate))
        breakdown.extend(self.progress_button_bonus(candidate, kind))
        breakdown.extend(self.popup_adjustments(candidate, kind, popup_boxes, popup_active))
        breakdown.extend(self.goal_adjustments(candidate, kind, popup_active, goal))
        breakdown.extend(self.learning_memory_adjustments(candidate, popup_active, learning_memory))
        breakdown.extend(self.pattern_adjustments(candidate_id, kind, action_records))

        adjusted_score = sum(value for _, value in breakdown)
        return Ranking(
            candidate=candidate,
            candidate_id=candidate_id,
            kind=kind,
            raw_score=raw_score,
            adjusted_score=adjusted_score,
            breakdown=breakdown,
        )

    def bbox_penalties(self, candidate: dict[str, Any]) -> list[tuple[str, float]]:
        bbox = candidate.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            return []
        try:
            width = float(bbox[2])
            height = float(bbox[3])
        except (TypeError, ValueError):
            return []

        area = width * height
        kind = string_value(candidate.get("kind")) or "unknown"
        penalties: list[tuple[str, float]] = []
        if area < 250:
            penalty = -0.35 if kind == "bright_region" else -0.20
            penalties.append(("tiny_bbox", self.scale_penalty(penalty)))
        elif area < 800:
            penalty = -0.20 if kind == "bright_region" else -0.08
            penalties.append(("small_bbox", self.scale_penalty(penalty)))
        elif area > 360 * 640 * 0.35:
            penalties.append(("huge_bbox", self.scale_penalty(-0.25)))
        elif area > 360 * 640 * 0.15:
            penalties.append(("large_bbox", self.scale_penalty(-0.10)))
        return penalties

    def position_penalties(self, candidate: dict[str, Any]) -> list[tuple[str, float]]:
        x = numeric_value(candidate.get("x"))
        y = numeric_value(candidate.get("y"))
        if x is None or y is None:
            return []
        penalties: list[tuple[str, float]] = []
        if y < 80:
            penalties.append(("top_status_currency_area", self.scale_penalty(-0.35)))
        if y < 32:
            penalties.append(("status_bar_or_top_edge", self.scale_penalty(-0.18)))
        elif y < 64:
            penalties.append(("near_top_edge", self.scale_penalty(-0.08)))
        if x < 18 or x > 342:
            penalties.append(("screen_side_edge", self.scale_penalty(-0.06)))
        if (x < 36 or x > 324) and y < 80:
            penalties.append(("top_corner", self.scale_penalty(-0.10)))
        return penalties

    def progress_button_bonus(self, candidate: dict[str, Any], kind: str) -> list[tuple[str, float]]:
        if kind != "contour":
            return []
        bbox = candidate.get("bbox")
        y = numeric_value(candidate.get("y"))
        if y is None or not isinstance(bbox, list) or len(bbox) != 4:
            return []
        try:
            width = float(bbox[2])
            height = float(bbox[3])
        except (TypeError, ValueError):
            return []
        area = width * height
        in_progress_band = 640 * 0.55 <= y <= 640 * 0.85
        looks_button_sized = width >= 80 and height >= 24 and 2_000 <= area <= 35_000
        if in_progress_band and looks_button_sized:
            return [("progress_button_bonus", 0.28)]
        return []

    def popup_adjustments(self, candidate: dict[str, Any], kind: str, popup_boxes: list[tuple[str, list[float]]], popup_active: bool) -> list[tuple[str, float]]:
        if not popup_active:
            return []
        adjustments: list[tuple[str, float]] = []
        candidate_id = self.candidate_id(candidate) or ""
        label = (string_value(candidate.get("label_guess")) or "").lower()
        inside_popup = is_inside_any_popup(candidate, popup_boxes)
        if "popup_cancel" in candidate_id:
            adjustments.append(("popup_cancel_top_priority", 3.20))
        if candidate_id == "right_mid_lower":
            adjustments.append(("popup_right_cancel_safe_bonus", 1.80))
        if "popup_close" in candidate_id:
            adjustments.append(("popup_close_safe_bonus", 1.60))
        if candidate_id == "left_mid_lower":
            adjustments.append(("popup_left_confirm_exit_penalty", self.scale_penalty(-2.20)))
        if "popup_confirm" in candidate_id:
            adjustments.append(("popup_confirm_exit_penalty", self.scale_penalty(-1.20)))
        if kind == "popup_button":
            adjustments.append(("popup_button_bonus", 1.20))
            if any(token in candidate_id.lower() or token in label for token in ("cancel", "close", "right", "no", "x", "back")):
                adjustments.append(("safe_popup_dismiss_bonus", 0.55))
            if "left" in candidate_id.lower() or "left" in label or "confirm" in label or "exit" in label:
                adjustments.append(("popup_confirm_caution", self.scale_penalty(-0.90)))
        elif kind in {"popup", "modal"}:
            adjustments.append(("popup_region_context", self.scale_penalty(-0.20)))
        elif not inside_popup:
            adjustments.append(("popup_outside_penalty", self.scale_penalty(-3.00)))
        else:
            adjustments.append(("inside_popup_non_button_bonus", 0.12))
        return adjustments

    def pattern_adjustments(self, candidate_id: str, kind: str, action_records: list[dict[str, Any]]) -> list[tuple[str, float]]:
        same_candidate_false = 0
        same_kind_false = 0
        same_kind_true = 0
        for record in action_records:
            changed = record.get("changed")
            record_candidate_id = string_value(record.get("candidate_id"))
            record_kind = string_value(record.get("kind")) or infer_kind_from_candidate_id(record_candidate_id)
            if record_candidate_id == candidate_id and changed is False:
                same_candidate_false += 1
            if record_kind == kind and changed is False:
                same_kind_false += 1
            elif record_kind == kind and changed is True:
                same_kind_true += 1

        adjustments: list[tuple[str, float]] = []
        if same_candidate_false:
            adjustments.append(("same_candidate_changed_false", self.scale_penalty(max(-0.60, -0.30 * same_candidate_false))))
        if same_kind_false:
            adjustments.append(("kind_changed_false_history", self.scale_penalty(max(-0.30, -0.03 * same_kind_false))))
        if same_kind_true:
            bonus = min(0.15, 0.03 * same_kind_true)
            if self.preference == "prefer_safe":
                bonus *= 0.75
            adjustments.append(("kind_changed_true_bonus", bonus))
        return adjustments

    def learning_memory_adjustments(self, candidate: dict[str, Any], popup_active: bool, learning_memory: dict[str, Any]) -> list[tuple[str, float]]:
        _, adjustments = learning_adjustment_for_candidate(learning_memory, candidate, active_layer="modal" if popup_active else "normal")
        return [(name, self.scale_penalty(value) if value < 0 else value) for name, value in adjustments]

    def goal_adjustments(self, candidate: dict[str, Any], kind: str, popup_active: bool, goal: dict[str, Any]) -> list[tuple[str, float]]:
        goal_id = string_value(goal.get("goal_id")) or "explore_safely"
        candidate_id = self.candidate_id(candidate) or ""
        label = (string_value(candidate.get("label_guess")) or "").lower()
        layer = string_value(candidate.get("layer")) or "normal"
        preferred_kinds = set(goal.get("preferred_candidate_kinds") if isinstance(goal.get("preferred_candidate_kinds"), list) else [])
        preferred_layers = set(goal.get("preferred_layers") if isinstance(goal.get("preferred_layers"), list) else [])
        adjustments: list[tuple[str, float]] = []
        if kind in preferred_kinds:
            adjustments.append(("goal_preferred_kind", 0.18))
        if layer in preferred_layers:
            adjustments.append(("goal_preferred_layer", 0.12))
        elif preferred_layers:
            adjustments.append(("goal_unpreferred_layer", self.scale_penalty(-0.35)))

        text = f"{candidate_id} {label}".lower()
        safe_modal_dismiss = any(token in text for token in ("cancel", "right", "close", "no", "back", "x"))
        confirm_or_exit = any(token in text for token in ("confirm", "exit")) or candidate_id in {"left_mid_lower", "popup_confirm"}
        if goal_id == "dismiss_modal":
            if popup_active and safe_modal_dismiss:
                adjustments.append(("goal_dismiss_modal_safe_control", 2.20))
            if popup_active and kind == "popup_button":
                adjustments.append(("goal_dismiss_modal_popup_button", 0.60))
            if confirm_or_exit:
                adjustments.append(("goal_dismiss_modal_avoid_confirm_exit", self.scale_penalty(-2.20)))
            if layer != "modal":
                adjustments.append(("goal_dismiss_modal_background_penalty", self.scale_penalty(-3.00)))
        elif goal_id == "find_progression":
            bbox = numeric_bbox(candidate.get("bbox"))
            area = bbox[2] * bbox[3] if bbox is not None else 0.0
            if kind in {"contour", "bright_region", "popup_button"}:
                adjustments.append(("goal_find_progression_action_kind", 0.45))
            if area >= 2_000:
                adjustments.append(("goal_find_progression_large_action", 0.35))
            if kind == "bright_region":
                adjustments.append(("goal_find_progression_highlight", 0.30))
            if 640 * 0.55 <= (numeric_value(candidate.get("y")) or 0) <= 640 * 0.88:
                adjustments.append(("goal_find_progression_lower_action_band", 0.18))
        elif goal_id == "explore_menu":
            if kind == "bottom_menu" or candidate_id.startswith(("bottom_menu_", "bottom_nav_")):
                adjustments.append(("goal_explore_menu_bottom_menu", 1.00))
            if layer == "modal":
                adjustments.append(("goal_explore_menu_modal_penalty", self.scale_penalty(-0.80)))
        elif goal_id == "collect_rewards":
            if kind == "bright_region":
                adjustments.append(("goal_collect_rewards_highlight", 0.65))
            if "reward" in text or "collect" in text:
                adjustments.append(("goal_collect_rewards_label_hint", 0.35))
            if confirm_or_exit:
                adjustments.append(("goal_collect_rewards_avoid_risk", self.scale_penalty(-0.80)))
        elif goal_id == "inspect_mercenary_synergy":
            bbox = numeric_bbox(candidate.get("bbox"))
            area = bbox[2] * bbox[3] if bbox is not None else 0.0
            y = numeric_value(candidate.get("y")) or 0.0
            if kind == "bottom_menu" or candidate_id.startswith(("bottom_menu_", "bottom_nav_")):
                adjustments.append(("goal_mercenary_coarse_nav_hint", 0.10))
            if kind in {"contour", "bright_region"} and 90 <= y <= 560:
                adjustments.append(("goal_mercenary_content_candidate", 0.40))
            if kind in {"contour", "bright_region"} and 180 <= area <= 2_500 and 90 <= y <= 560:
                adjustments.append(("goal_mercenary_small_synergy_icon_probe", 0.55))
            if kind == "bright_region" and 90 <= y <= 560:
                adjustments.append(("goal_mercenary_highlighted_icon_or_button", 0.25))
            if layer == "modal":
                adjustments.append(("goal_mercenary_modal_penalty", self.scale_penalty(-0.70)))
        return adjustments

    def scale_penalty(self, value: float) -> float:
        if self.preference == "prefer_safe":
            return value * 1.25
        return value

    @staticmethod
    def candidate_id(candidate: object) -> str | None:
        if not isinstance(candidate, dict):
            return None
        candidate_id = candidate.get("candidate_id") or candidate.get("id")
        return candidate_id if isinstance(candidate_id, str) else None

    @staticmethod
    def candidate_score(candidate: object) -> float:
        if not isinstance(candidate, dict):
            return float("-inf")
        score = candidate.get("score", 0.0)
        try:
            return float(score)
        except (TypeError, ValueError):
            return 0.0


class OpenCodeBridge:
    def __init__(
        self,
        *,
        mock_bridge: MockBridge,
        opencode_cmd: str = "opencode",
        timeout_sec: float = 120.0,
        opencode_model: str = "openai/gpt-5.5-fast",
    ) -> None:
        self.mock_bridge = mock_bridge
        self.opencode_cmd = opencode_cmd
        self.timeout_sec = timeout_sec
        self.opencode_model = opencode_model

    def choose_action(self, request: dict[str, Any], excluded_ids: set[str], action_records: list[dict[str, Any]], learning_memory: dict[str, Any] | None = None) -> dict[str, object]:
        prompt = build_opencode_prompt(request, excluded_ids)
        try:
            prompt_path = write_opencode_prompt(prompt)
        except OSError as exc:
            print(f"OpenCodeBridge could not write prompt file; falling back to MockBridge. exception={exc!r}", flush=True)
            if request.get("direct_vision") is True:
                return direct_vision_unavailable_action("prompt_write_failed")
            return self.mock_bridge.choose_action(request, excluded_ids, action_records, learning_memory)
        instruction = prompt if request.get("direct_vision") is True else build_opencode_instruction(prompt_path)
        command = opencode_command(self.opencode_cmd, instruction, model=self.opencode_model)
        print(f"OpenCodeBridge prompt file: {prompt_path}", flush=True)
        print(f"OpenCodeBridge subprocess command: {command}", flush=True)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=opencode_subprocess_env(),
                timeout=self.timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            write_opencode_error_log(
                "timeout",
                request=request,
                command=command,
                prompt=prompt,
                stdout=process_output_text(exc.stdout),
                stderr=process_output_text(exc.stderr),
                exception=repr(exc),
            )
            print(
                "OpenCodeBridge opencode timeout; "
                f"stdout={process_output_text(exc.stdout)!r} stderr={process_output_text(exc.stderr)!r} "
                f"debug_log={DEFAULT_OPENCODE_ERROR_LOG_PATH}",
                flush=True,
            )
            if request.get("direct_vision") is True:
                return direct_vision_unavailable_action("opencode_timeout_or_error")
            return self.mock_bridge.choose_action(request, excluded_ids, action_records, learning_memory)
        except OSError as exc:
            write_opencode_error_log("os_error", request=request, command=command, prompt=prompt, exception=repr(exc))
            print(f"OpenCodeBridge failed to run opencode; falling back to MockBridge. exception={exc!r} debug_log={DEFAULT_OPENCODE_ERROR_LOG_PATH}", flush=True)
            if request.get("direct_vision") is True:
                return direct_vision_unavailable_action("opencode_os_error")
            return self.mock_bridge.choose_action(request, excluded_ids, action_records, learning_memory)

        if completed.returncode != 0:
            write_opencode_error_log(
                "nonzero_exit",
                request=request,
                command=command,
                prompt=prompt,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
            print(
                f"OpenCodeBridge opencode exited with code {completed.returncode}; falling back to MockBridge. stdout={completed.stdout.strip()!r} stderr={completed.stderr.strip()!r} debug_log={DEFAULT_OPENCODE_ERROR_LOG_PATH}",
                flush=True,
            )
            if request.get("direct_vision") is True:
                return direct_vision_unavailable_action("opencode_nonzero_exit")
            return self.mock_bridge.choose_action(request, excluded_ids, action_records, learning_memory)

        raw_response = extract_json_object(completed.stdout)
        write_opencode_run_log(
            request=request,
            command=command,
            prompt=prompt,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if raw_response is None:
            write_opencode_error_log(
                "invalid_json",
                request=request,
                command=command,
                prompt=prompt,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
            print(f"OpenCodeBridge could not parse JSON from opencode output; falling back to MockBridge. debug_log={DEFAULT_OPENCODE_ERROR_LOG_PATH}", flush=True)
            if request.get("direct_vision") is True:
                return direct_vision_unavailable_action("invalid_json")
            return self.mock_bridge.choose_action(request, excluded_ids, action_records, learning_memory)

        raw_response = enrich_with_visible_knowledge(
            raw_response,
            request,
            opencode_cmd=self.opencode_cmd,
            opencode_model=self.opencode_model,
            timeout_sec=self.timeout_sec,
        )
        response = validate_bridge_response(raw_response, request, excluded_ids)
        if response is None:
            print(f"OpenCodeBridge received invalid response; falling back to MockBridge: {raw_response!r}", flush=True)
            if request.get("direct_vision") is True:
                return direct_vision_unavailable_action("invalid_action")
            return self.mock_bridge.choose_action(request, excluded_ids, action_records, learning_memory)

        print(f"OpenCodeBridge selected action: {response}", flush=True)
        return response


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mock external planner bridge for autonomous game explorer")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--watch", action="store_true", help="Keep watching planner_request.json for changes.")
    mode.add_argument("--once", action="store_true", help="Process planner_request.json once and exit.")
    parser.add_argument("--bridge", choices=("mock", "opencode"), default="mock", help="Bridge implementation to use. Default: mock.")
    parser.add_argument("--request", default=str(DEFAULT_REQUEST_PATH), help="Path to planner_request.json.")
    parser.add_argument("--response", default=str(DEFAULT_RESPONSE_PATH), help="Path to planner_response.json.")
    parser.add_argument("--actions-log", default=str(DEFAULT_ACTIONS_LOG_PATH), help="Path to runtime/actions.jsonl.")
    parser.add_argument("--state-graph", default=str(DEFAULT_STATE_GRAPH_PATH), help="Path to runtime/state_graph.json.")
    parser.add_argument("--learning-memory", default=str(DEFAULT_LEARNING_MEMORY_PATH), help="Path to runtime/learning_memory.json.")
    parser.add_argument("--poll-interval", type=float, default=1.0, metavar="SEC", help="Polling interval in seconds. Default: 1.")
    parser.add_argument("--opencode-timeout", type=float, default=120.0, metavar="SEC", help="OpenCode subprocess timeout in seconds. Default: 120.")
    parser.add_argument("--opencode-cmd", default="opencode", help="OpenCode executable command. Default: opencode.")
    parser.add_argument("--opencode-model", default="openai/gpt-5.5-fast", help="OpenCode model in provider/model format. Default: openai/gpt-5.5-fast.")
    parser.add_argument("--min-adjusted-score", type=float, default=0.05, metavar="FLOAT", help="Minimum adjusted score required to tap a candidate. Default: 0.05.")
    preference = parser.add_mutually_exclusive_group()
    preference.add_argument("--prefer-exploration", action="store_const", const="exploration", dest="preference", help="Favor exploratory taps. This is the default.")
    preference.add_argument("--prefer-safe", action="store_const", const="prefer_safe", dest="preference", help="Apply stronger penalties to risky-looking candidates.")
    parser.set_defaults(preference="exploration")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    request_path = Path(args.request)
    response_path = Path(args.response)
    actions_log_path = Path(args.actions_log)
    state_graph_path = Path(args.state_graph)
    learning_memory_path = Path(args.learning_memory)
    mock_bridge = MockBridge(min_adjusted_score=args.min_adjusted_score, preference=args.preference)
    bridge = create_bridge(args.bridge, mock_bridge, opencode_cmd=args.opencode_cmd, opencode_timeout=args.opencode_timeout, opencode_model=args.opencode_model)
    seen_fingerprint: tuple[int, str] | None = None

    if args.once or not args.watch:
        process_once(request_path, response_path, actions_log_path, state_graph_path, learning_memory_path, bridge, seen_fingerprint)
        return

    while True:
        seen_fingerprint = process_once(request_path, response_path, actions_log_path, state_graph_path, learning_memory_path, bridge, seen_fingerprint)
        time.sleep(max(args.poll_interval, 0.1))


def process_once(
    request_path: Path,
    response_path: Path,
    actions_log_path: Path,
    state_graph_path: Path,
    learning_memory_path: Path,
    bridge: MockBridge | OpenCodeBridge,
    seen_fingerprint: tuple[int, str] | None,
) -> tuple[int, str] | None:
    if not request_path.exists():
        return seen_fingerprint

    try:
        content = request_path.read_bytes()
        # Runner may rewrite an identical stable direct-vision request every loop.
        # Use content hash only so one semantic request triggers one OpenCode run.
        fingerprint = (0, hashlib.sha256(content).hexdigest())
    except OSError as exc:
        write_response(response_path, wait_action(f"MockBridge could not read planner_request.json: {exc}"))
        return seen_fingerprint

    if fingerprint == seen_fingerprint:
        return seen_fingerprint

    try:
        request = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        write_response(response_path, wait_action(f"MockBridge could not parse planner_request.json: {exc}"))
        return fingerprint

    if not isinstance(request, dict):
        write_response(response_path, wait_action("MockBridge expected planner_request.json to contain a JSON object."))
        return fingerprint

    if response_matches_request(response_path, string_value(request.get("request_id"))):
        return fingerprint

    action_records = read_action_records(actions_log_path)
    learning_memory = load_learning_memory(learning_memory_path)
    excluded_ids = collect_excluded_candidate_ids(request, action_records, state_graph_path)
    print(f"Excluded candidates: {sorted(excluded_ids)}", flush=True)
    response = bridge.choose_action(request, excluded_ids, action_records, learning_memory)
    request_id = string_value(request.get("request_id"))
    if request_id:
        response["request_id"] = request_id
    write_response(response_path, response)
    return fingerprint


def response_matches_request(response_path: Path, request_id: str | None) -> bool:
    if not request_id or not response_path.exists():
        return False
    try:
        response = json.loads(response_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(response, dict) and response.get("request_id") == request_id


def create_bridge(bridge_name: str, mock_bridge: MockBridge, *, opencode_cmd: str, opencode_timeout: float, opencode_model: str) -> MockBridge | OpenCodeBridge:
    if bridge_name == "opencode":
        return OpenCodeBridge(mock_bridge=mock_bridge, opencode_cmd=opencode_cmd, timeout_sec=opencode_timeout, opencode_model=opencode_model)
    return mock_bridge


def build_opencode_prompt(request: dict[str, Any], excluded_ids: set[str]) -> str:
    if request.get("direct_vision") is True:
        return build_direct_vision_prompt(request)
    candidates = request.get("candidates") if isinstance(request.get("candidates"), list) else []
    request_active_layer = string_value(request.get("active_layer")) or "unknown"
    local_active_layer_hint = string_value(request.get("local_active_layer_hint")) or active_layer(request, candidates)
    popup_active = local_active_layer_hint == "modal"
    tried_candidates = request.get("tried_candidates") if isinstance(request.get("tried_candidates"), list) else []
    current_goal = resolve_goal(request.get("current_goal") or request.get("goal"))
    action_schema = request.get("action_schema") if isinstance(request.get("action_schema"), dict) else {
        "type": "tap_candidate | back | wait",
        "candidate_id": "string | null",
        "reason": "string",
    }
    summary = {
        "request_id": request.get("request_id"),
        "state_id": request.get("state_id"),
        "screenshot_path": request.get("screenshot_path"),
        "debug_image_path": request.get("debug_image_path"),
        "goal": request.get("goal") or "Choose the highest information-value safe exploration action.",
        "candidate_count": len(candidates),
        "tried_candidate_count": len(tried_candidates),
        "active_layer": request_active_layer,
        "local_active_layer_hint": local_active_layer_hint,
        "local_modal_score": request.get("local_modal_score"),
        "local_grid_like": request.get("local_grid_like"),
        "popup_active_hint": popup_active,
        "current_goal": current_goal,
        "success_signals": current_goal.get("success_signals", []),
        "avoid_signals": current_goal.get("avoid_signals", []),
    }
    payload = {
        "summary": summary,
        "candidates": candidates,
        "candidates_all": request.get("candidates_all") if isinstance(request.get("candidates_all"), list) else candidates,
        "candidates_by_layer": request.get("candidates_by_layer") if isinstance(request.get("candidates_by_layer"), dict) else {},
        "tried_candidates": tried_candidates,
        "excluded_candidates": sorted(excluded_ids),
        "current_goal": current_goal,
        "learning_memory_summary": request.get("learning_memory_summary") or summarize_learning_memory(load_learning_memory(), candidates),
        "action_schema": action_schema,
    }
    return (
        "You are the external planner for an autonomous Android game explorer.\n"
        "Choose exactly one action with high information value. Do not use game-specific hardcoded rules.\n"
        "Use only the planner request snapshot embedded in this prompt. Do not reload runtime/planner_request.json because it may change while you are running.\n"
        "Inspect candidates_debug.png directly when possible and decide whether the current screen is normal or modal yourself. If the image cannot be viewed, rely on the embedded JSON but say so in reason.\n"
        "local_active_layer_hint, local_modal_score, and local_grid_like are local OpenCV hints only; they are not authoritative.\n"
        "Card grids, inventory screens, roster screens, and list screens are normal screens, not modals, even if OpenCV detects large boxes.\n"
        "Only small dialog boxes that cover/dim the background like confirm/cancel popups should be selected_active_layer=modal.\n"
        "Layer policy: return selected_active_layer as normal, modal, or unknown based on your visual/semantic judgment. If selected_active_layer is modal, prefer popup-internal cancel/close/right/no-style controls over background candidates.\n"
        "Popup safety policy: Prefer popup_cancel, popup close, or right-side cancel/no style candidates. Treat left_mid_lower and confirm-style popup candidates as exit/confirm risk.\n"
        "Candidate policy: choose tap_candidate when a candidate is accurate. If candidates are poorly placed or miss the real target, choose tap_xy and provide direct x/y coordinates within screen_bounds.\n"
        "Goal policy: optimize for current_goal. Prefer preferred_candidate_kinds and preferred_layers, look for success_signals, and avoid avoid_signals.\n"
        "If current_goal.goal_id is dismiss_modal and your selected_active_layer is modal, strongly prefer cancel/right/close/back-style safe dismiss actions over confirm/exit-style actions.\n"
        "If current_goal.goal_id is inspect_mercenary_synergy, do not trust synthetic 5-split bottom_menu_N geometry as authoritative. Use candidates_debug.png to visually identify the actual Mercenary/helmet/character bottom button. If no candidate center matches the real button, use tap_xy with direct coordinates. Do not press back from a normal layer for this goal because back may open an exit modal. After the Mercenary tab is open, inspect normal-layer small/medium content contours or highlighted icons carefully because synergy icons may be small.\n"
        "Learning memory policy: use learning_memory_summary to prefer candidate feature patterns with higher success_count/changed_true_count and avoid repeated changed_false/fail-dominant patterns.\n"
        "Do not choose any candidate_id in tried_candidates or excluded_candidates. If the desired visual target is only represented by tried/excluded synthetic bottom_menu_N candidates, prefer tap_xy on the visually correct coordinate instead.\n"
        "Return only one JSON object with this schema: "
        '{"type":"tap_candidate|tap_xy|back|wait","candidate_id":"string|null","x":"int|null","y":"int|null","selected_active_layer":"normal|modal|unknown","reason":"string"}.\n'
        "The response may include request_id, but it must match the provided request_id if present.\n"
        "Planner request summary and candidates:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def build_direct_vision_prompt(request: dict[str, Any]) -> str:
    current_goal = resolve_goal(request.get("current_goal") or request.get("goal"))
    screenshot_path = str(request.get("screenshot_path") or "").replace("\\", "/")
    local_active_layer_hint = string_value(request.get("local_active_layer_hint")) or "unknown"
    payload = {
        "request_id": request.get("request_id"),
        "direct_vision": True,
        "state_id": request.get("state_id"),
        "screenshot_path": request.get("screenshot_path"),
        "screen_bounds": request.get("screen_bounds"),
        "local_active_layer_hint": local_active_layer_hint,
        "local_modal_score": request.get("local_modal_score"),
        "direct_action_summary": request.get("direct_action_summary") if isinstance(request.get("direct_action_summary"), dict) else {},
        "current_goal": current_goal,
        "goal": request.get("goal") or current_goal.get("description"),
        "recent_direct_actions": request.get("recent_direct_actions") if isinstance(request.get("recent_direct_actions"), list) else [],
        "failed_tap_xy_this_state": request.get("failed_tap_xy_this_state") if isinstance(request.get("failed_tap_xy_this_state"), list) else [],
        "bottom_tap_transitions_this_state": request.get("bottom_tap_transitions_this_state") if isinstance(request.get("bottom_tap_transitions_this_state"), list) else [],
        "action_schema": {
            "type": "tap_xy | swipe | back | wait",
            "candidate_id": "null",
            "x": "int | null",
            "y": "int | null",
            "x2": "int | null",
            "y2": "int | null",
            "duration_ms": "int | null",
            "selected_active_layer": "normal | modal | unknown",
            "memory_updates": "string[]",
            "reason": "string",
        },
    }
    return (
        "CRITICAL: Return exactly one valid JSON object and nothing else. Use double-quoted JSON keys and strings. No prose, no markdown, no tool narration.\n"
        f"Screenshot: ![current screen]({screenshot_path})\n"
        "Use the screenshot as provided. Do not generate thumbnails/previews. Do not run shell commands. Do not explain.\n"
        "Return image_unavailable only if opening the provided screenshot explicitly fails.\n"
        "Do not use OpenCV candidates or synthetic 5-split bottom navigation.\n"
        "Decision order: (1) if a small confirm/cancel popup is visible, dismiss it safely; (2) if this state has repeated failed taps, avoid those regions; (3) only then pursue the current goal.\n"
        "Modal policy: only small dimmed confirm/cancel dialogs are dismissable modals. Large mercenary, synergy, recipe, tooltip, and detail panels are knowledge screens, not popups; read them, record memory_updates, and scroll inside them instead of closing them. For Korean popup buttons, prefer cancel/no/right-side controls such as cancel over confirm. Do not tap bottom navigation or background while a popup is active.\n"
        "Goal inspect_mercenary_synergy: the game UI is Korean. Look for the Korean Mercenary term Yongbyeong (Unicode U+C6A9 U+BCD1, usually rendered as 용병) and Synergy term Sineoji (Unicode U+C2DC U+B108 U+C9C0, usually rendered as 시너지). The target may be anywhere on the screen; do not assume it is a bottom navigation tab.\n"
        "Use recent_direct_actions and failed_tap_xy_this_state as negative evidence. Do not repeat changed=false tap_xy points or the same bottom-band area unless the screenshot clearly shows the exact 용병 or 시너지 text there.\n"
        "If direct_action_summary.bottom_band_blocked is true, y>=560 is forbidden unless a modal safe button is actually located there.\n"
        "Use bottom_tap_transitions_this_state as navigation-loop evidence. A bottom tap that changes to another known state is not automatically progress; it may just switch tabs. If bottom_tap_transitions_this_state is non-empty, do not tap y>=560 again from this state unless there is no other visible action and you state why. Prefer main-content taps or swipes for information gathering.\n"
        "If repeated bottom taps only bounce between screens, inspect the full screen for other likely entry points: character/roster/card/book/menu/helmet/list buttons, labeled tabs, side buttons, or scrollable panels. If already in a roster/card/list/detail screen, prefer opening visible cards/details or swiping through the list over returning to bottom navigation.\n"
        "After finding relevant Mercenary/Synergy content, inspect all mercenary cards/details and all synergy icons/details to improve game understanding. Use swipe to scroll when more entries are below. Include concise observations in memory_updates whenever you learn a mercenary name, ability, stat, role, synergy, or recipe.\n"
        "Mercenary knowledge policy: if the current screen is a mercenary detail, synergy detail, skill tooltip, recipe, combination, or synergy list, first write concrete observations into memory_updates. Use these exact formats when visible: "
        "SYNERGY:name=<name>;count=<active/required>;effect=<effect text>;members=<visible member names or icons if readable>. "
        "MERCENARY:name=<name>;grade=<normal|rare|legendary|mythic|unknown>;level=<level>;role=<role>;ability=<ability text>;synergies=<synergy names>. "
        "RECIPE:result=<target mercenary>;grade=<legendary|mythic|unknown>;requires=<required mercenaries or materials>;source=<screen/button where seen>. "
        "For Korean UI, treat 전설 as legendary, 신화 as mythic, 조합/합성/제작 as recipe or combination, 필요/재료 as requirements. "
        "Critical: when a synergy/detail/recipe panel visibly contains Korean text, memory_updates must contain at least one concrete SYNERGY, MERCENARY, or RECIPE entry before you return any swipe/tap/back action. Do not return memory_updates: [] while readable knowledge text is visible. "
        "On a synergy list screen, record every visible synergy name and effect, then swipe down/up through the list before leaving. On a recipe/combination screen, prioritize collecting requirements over navigation. If no more visible detail or scroll target remains, return back to the mercenary list. Do not tap bottom navigation from a detail screen. When a large knowledge panel is open, y>=560 bottom navigation is forbidden; return a swipe inside the panel instead.\n"
        "Card grids/inventory/roster/list screens are normal. Only small confirm/cancel dialogs are modal.\n"
        "The screenshot is already a compact 360x640 image; do not create another preview. Use its coordinates directly inside screen_bounds.\n"
        "Schema: "
        '{"type":"tap_xy|swipe|back|wait","candidate_id":null,"x":"int|null","y":"int|null","x2":"int|null","y2":"int|null","duration_ms":"int|null","selected_active_layer":"normal|modal|unknown","memory_updates":["string"],"reason":"string"}.\n'
        f"Request:\n{json.dumps(payload, ensure_ascii=True, separators=(',', ':'))}"
    )


def enrich_with_visible_knowledge(
    raw_response: dict[str, Any],
    request: dict[str, Any],
    *,
    opencode_cmd: str,
    opencode_model: str,
    timeout_sec: float,
) -> dict[str, Any]:
    if request.get("direct_vision") is not True:
        return raw_response
    if not is_mercenary_knowledge_goal(request):
        return raw_response
    if not has_large_modal_panel(request):
        return raw_response
    existing = raw_response.get("memory_updates")
    if isinstance(existing, list) and any(isinstance(item, str) and item.strip() for item in existing):
        return raw_response
    updates = extract_visible_knowledge_updates(request, opencode_cmd=opencode_cmd, opencode_model=opencode_model, timeout_sec=timeout_sec)
    if not updates:
        return raw_response
    enriched = dict(raw_response)
    enriched["memory_updates"] = merge_memory_updates(existing if isinstance(existing, list) else [], updates)
    return enriched


def extract_visible_knowledge_updates(
    request: dict[str, Any],
    *,
    opencode_cmd: str,
    opencode_model: str,
    timeout_sec: float,
) -> list[str]:
    updates = extract_visible_knowledge_updates_openai(request)
    if updates:
        return updates
    if os.environ.get("GAME_AGENT_ENABLE_OPENCODE_KNOWLEDGE") != "1":
        return []
    return extract_visible_knowledge_updates_opencode(request, opencode_cmd=opencode_cmd, opencode_model=opencode_model, timeout_sec=timeout_sec)


def extract_visible_knowledge_updates_openai(request: dict[str, Any]) -> list[str]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return []
    screenshot_path = string_value(request.get("screenshot_path"))
    if not screenshot_path:
        return []
    image_path = Path(screenshot_path)
    if not image_path.exists():
        return []
    try:
        import httpx
    except ImportError:
        return []
    prompt = build_visible_knowledge_prompt(request)
    image_url = image_data_url(image_path)
    payload = {
        "model": os.environ.get("OPENAI_KNOWLEDGE_MODEL", "gpt-4o-mini"),
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_url, "detail": "high"},
                ],
            }
        ],
        "max_output_tokens": 800,
    }
    try:
        response = httpx.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=45.0,
        )
    except Exception as exc:
        write_opencode_error_log("openai_knowledge_extract_error", request=request, command=["openai_responses_api"], prompt=prompt, exception=repr(exc))
        return []
    try:
        response_payload = response.json()
    except ValueError:
        response_payload = {"raw_text": response.text}
    write_opencode_run_log(
        request=request,
        command=["openai_responses_api", payload["model"]],
        prompt=prompt,
        returncode=response.status_code,
        stdout=json.dumps(response_payload, ensure_ascii=False),
        stderr="",
        log_path=DEFAULT_OPENCODE_KNOWLEDGE_LOG_PATH,
    )
    if response.status_code >= 400:
        return []
    text = response_output_text(response_payload)
    parsed = extract_json_object(text)
    if not isinstance(parsed, dict):
        return []
    updates = parsed.get("memory_updates")
    if not isinstance(updates, list):
        return []
    return [str(item).strip() for item in updates if isinstance(item, str) and item.strip()]


def extract_visible_knowledge_updates_opencode(
    request: dict[str, Any],
    *,
    opencode_cmd: str,
    opencode_model: str,
    timeout_sec: float,
) -> list[str]:
    prompt = build_visible_knowledge_prompt(request)
    command = opencode_command(opencode_cmd, prompt, model=opencode_model)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=opencode_subprocess_env(),
            timeout=min(timeout_sec, 90.0),
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        write_opencode_error_log("knowledge_extract_error", request=request, command=command, prompt=prompt, exception=repr(exc))
        return []
    write_opencode_run_log(
        request=request,
        command=command,
        prompt=prompt,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        log_path=DEFAULT_OPENCODE_KNOWLEDGE_LOG_PATH,
    )
    if completed.returncode != 0:
        return []
    payload = extract_json_object(completed.stdout)
    if not isinstance(payload, dict):
        return []
    updates = payload.get("memory_updates")
    if not isinstance(updates, list):
        return []
    return [str(item).strip() for item in updates if isinstance(item, str) and item.strip()]


def image_data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = "image/png"
    if suffix in {".jpg", ".jpeg"}:
        mime = "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def response_output_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str):
        return direct
    parts: list[str] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for content_item in content:
                if not isinstance(content_item, dict):
                    continue
                text = content_item.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "\n".join(parts)


def build_visible_knowledge_prompt(request: dict[str, Any]) -> str:
    screenshot_path = str(request.get("screenshot_path") or "").replace("\\", "/")
    return (
        "Return exactly one JSON object and nothing else.\n"
        f"Screenshot: ![current screen]({screenshot_path})\n"
        "Task: extract visible game knowledge from the Korean mercenary/synergy/recipe screen. Do not choose an action.\n"
        "Use the screenshot coordinates and text as-is. If some Korean text is partially unreadable, still record the readable name/effect fragment.\n"
        "Return schema: {\"memory_updates\":[\"string\"]}.\n"
        "Use only these formats:\n"
        "SYNERGY:name=<name>;count=<active/required>;effect=<effect text>;members=<visible member names or icons if readable>\n"
        "MERCENARY:name=<name>;grade=<normal|rare|legendary|mythic|unknown>;level=<level>;role=<role>;ability=<ability text>;synergies=<synergy names>\n"
        "RECIPE:result=<target mercenary>;grade=<legendary|mythic|unknown>;requires=<required mercenaries or materials>;source=<screen/button where seen>\n"
        "Korean mapping: 전설=legendary, 신화=mythic, 조합/합성/제작=recipe/combination, 필요/재료=requirements.\n"
        "If this is a synergy list, include every visible synergy row with its count and effect. If this is a recipe/combination screen, include the visible result and required materials.\n"
        "If no concrete mercenary, synergy, or recipe text is visible, return {\"memory_updates\":[]}.\n"
        f"Request context:\n{json.dumps({'state_id': request.get('state_id'), 'screen_bounds': request.get('screen_bounds')}, ensure_ascii=False)}"
    )


def merge_memory_updates(existing: list[object], updates: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*existing, *updates]:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        merged.append(text)
    return merged


def write_opencode_prompt(prompt: str, prompt_path: Path = DEFAULT_OPENCODE_PROMPT_PATH) -> Path:
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    return prompt_path


def build_opencode_instruction(prompt_path: Path) -> str:
    return (
        f"Read {prompt_path.as_posix()} and produce only the planner action JSON. "
        "Use only the request snapshot embedded in that prompt file; do not read runtime/planner_request.json. "
        "Do not include markdown or explanation."
    )


def opencode_command(opencode_cmd: str, instruction: str, *, model: str = "openai/gpt-5.5-fast") -> list[str]:
    command_path = Path(opencode_cmd)
    exists = command_path.exists()
    print(f"OpenCodeBridge opencode_cmd exists={exists}: {opencode_cmd}", flush=True)
    model_args = ["--model", model] if model else []
    if command_path.suffix.lower() in {".cmd", ".bat"}:
        entrypoint = command_path.parent / "node_modules" / "opencode-ai" / "bin" / "opencode"
        node_exe = command_path.parent / "node.exe"
        node_cmd = str(node_exe) if node_exe.exists() else "node"
        if entrypoint.exists():
            return [node_cmd, str(entrypoint), "--pure", "--print-logs", "--log-level", "DEBUG", "run", *model_args, instruction]
        return ["cmd", "/c", opencode_cmd, "--pure", "--print-logs", "--log-level", "DEBUG", "run", *model_args, instruction]
    return [opencode_cmd, "--pure", "--print-logs", "--log-level", "DEBUG", "run", *model_args, instruction]


def opencode_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "OPENCODE_CLIENT",
        "OPENCODE_SERVER_USERNAME",
        "OPENCODE_SERVER_PASSWORD",
        "OPENCODE_DISABLE_EMBEDDED_WEB_UI",
    ):
        env.pop(key, None)
    venv_scripts = Path(".venv") / "Scripts"
    if venv_scripts.exists():
        env["PATH"] = str(venv_scripts.resolve()) + os.pathsep + env.get("PATH", "")
    return env


def process_output_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def write_opencode_error_log(
    error_type: str,
    *,
    request: dict[str, Any],
    command: list[str],
    prompt: str,
    returncode: int | None = None,
    stdout: str = "",
    stderr: str = "",
    exception: str = "",
    log_path: Path = DEFAULT_OPENCODE_ERROR_LOG_PATH,
) -> None:
    payload = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "error_type": error_type,
        "request_id": request.get("request_id"),
        "state_id": request.get("state_id"),
        "direct_vision": request.get("direct_vision"),
        "screenshot_path": request.get("screenshot_path"),
        "returncode": returncode,
        "exception": exception,
        "command": command,
        "cwd": str(Path.cwd()),
        "path": os.environ.get("PATH", ""),
        "stdout": stdout,
        "stderr": stderr,
        "prompt": prompt,
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"Could not write OpenCodeBridge error log: {exc!r}", flush=True)


def write_opencode_run_log(
    *,
    request: dict[str, Any],
    command: list[str],
    prompt: str,
    returncode: int,
    stdout: str,
    stderr: str,
    log_path: Path = DEFAULT_OPENCODE_LAST_LOG_PATH,
) -> None:
    payload = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "request_id": request.get("request_id"),
        "state_id": request.get("state_id"),
        "direct_vision": request.get("direct_vision"),
        "screenshot_path": request.get("screenshot_path"),
        "returncode": returncode,
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "prompt": prompt,
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"Could not write OpenCodeBridge run log: {exc!r}", flush=True)


def extract_json_object(output: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    stripped = output.strip()
    first_object: dict[str, Any] | None = None
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            if first_object is None:
                first_object = value
            if "type" in value:
                return value
    return first_object


def validate_bridge_response(raw_response: dict[str, Any], request: dict[str, Any], excluded_ids: set[str]) -> dict[str, object] | None:
    request_id = string_value(request.get("request_id"))
    response_request_id = raw_response.get("request_id")
    if response_request_id is not None and response_request_id != request_id:
        print(f"OpenCodeBridge response request_id mismatch: expected={request_id!r} got={response_request_id!r}", flush=True)
        return None

    action_type = raw_response.get("type")
    candidate_id = raw_response.get("candidate_id")
    reason = str(raw_response.get("reason") or "OpenCodeBridge selected this action.")
    selected_active_layer = string_value(raw_response.get("selected_active_layer")) or "unknown"
    if selected_active_layer not in {"normal", "modal", "unknown"}:
        print(f"OpenCodeBridge response has invalid selected_active_layer: {selected_active_layer!r}", flush=True)
        return None
    memory_updates = raw_response.get("memory_updates") if isinstance(raw_response.get("memory_updates"), list) else []
    memory_updates = [str(item) for item in memory_updates if isinstance(item, str) and item.strip()]
    if action_type not in {"tap_candidate", "tap_xy", "swipe", "back", "wait"}:
        print(f"OpenCodeBridge response has invalid action type: {action_type!r}", flush=True)
        return None
    candidates = request.get("candidates")
    if action_type == "wait" and modal_candidates_exhausted(request, candidates, excluded_ids):
        return modal_exhausted_back_action()
    modal_guard = direct_modal_guard_response(request, action_type, None, None, selected_active_layer, memory_updates)
    if modal_guard is not None and action_type in {"wait", "swipe"}:
        return modal_guard
    if action_type == "tap_xy":
        x = int_value(raw_response.get("x"))
        y = int_value(raw_response.get("y"))
        if x is None or y is None or not point_in_screen_bounds(request, x, y):
            print(f"OpenCodeBridge response has unsafe tap_xy coordinates: x={raw_response.get('x')!r} y={raw_response.get('y')!r}", flush=True)
            return None
        modal_guard = direct_modal_guard_response(request, action_type, x, y, selected_active_layer, memory_updates)
        if modal_guard is not None:
            return modal_guard
        knowledge_guard = mercenary_knowledge_bottom_guard_response(request, x, y, selected_active_layer, memory_updates)
        if knowledge_guard is not None:
            return knowledge_guard
        if request.get("direct_vision") is True and y >= 560 and bottom_tap_loop_evidence(request):
            reason = "Overrode repeated bottom-band tap to a main-content swipe because bottom taps from this state have been navigation loops, not information gathering."
            return {"type": "swipe", "candidate_id": None, "x": 180, "y": 520, "x2": 180, "y2": 200, "duration_ms": 600, "selected_active_layer": selected_active_layer, "memory_updates": memory_updates, "reason": reason}
        return {"type": "tap_xy", "candidate_id": None, "x": x, "y": y, "x2": None, "y2": None, "duration_ms": None, "selected_active_layer": selected_active_layer, "memory_updates": memory_updates, "reason": reason}
    if action_type == "swipe":
        x = int_value(raw_response.get("x"))
        y = int_value(raw_response.get("y"))
        x2 = int_value(raw_response.get("x2"))
        y2 = int_value(raw_response.get("y2"))
        duration_ms = int_value(raw_response.get("duration_ms")) or 350
        if any(value is None for value in (x, y, x2, y2)) or not point_in_screen_bounds(request, x, y) or not point_in_screen_bounds(request, x2, y2):
            print(f"OpenCodeBridge response has unsafe swipe coordinates: {raw_response!r}", flush=True)
            return None
        modal_guard = direct_modal_guard_response(request, action_type, x, y, selected_active_layer, memory_updates)
        if modal_guard is not None:
            return modal_guard
        return {"type": "swipe", "candidate_id": None, "x": x, "y": y, "x2": x2, "y2": y2, "duration_ms": duration_ms, "selected_active_layer": selected_active_layer, "memory_updates": memory_updates, "reason": reason}
    if action_type != "tap_candidate":
        return {"type": str(action_type), "candidate_id": None, "x": None, "y": None, "x2": None, "y2": None, "duration_ms": None, "selected_active_layer": selected_active_layer, "memory_updates": memory_updates, "reason": reason}

    if not isinstance(candidate_id, str) or not isinstance(candidates, list):
        print(f"OpenCodeBridge response has invalid candidate_id: {candidate_id!r}", flush=True)
        return None
    candidate = find_candidate(candidate_id, candidates)
    if candidate is None:
        print(f"OpenCodeBridge response candidate_id is not in request candidates: {candidate_id!r}", flush=True)
        return None
    if is_excluded_candidate(candidate, excluded_ids):
        print(f"OpenCodeBridge response candidate_id is already excluded/tried: {candidate_id!r}", flush=True)
        return None
    return {"type": "tap_candidate", "candidate_id": candidate_id, "x": None, "y": None, "x2": None, "y2": None, "duration_ms": None, "selected_active_layer": selected_active_layer, "memory_updates": memory_updates, "reason": reason}


def find_candidate(candidate_id: str, candidates: list[object]) -> object | None:
    for candidate in candidates:
        if MockBridge.candidate_id(candidate) == candidate_id:
            return candidate
    return None


def bottom_tap_loop_evidence(request: dict[str, Any]) -> bool:
    summary = request.get("direct_action_summary")
    if isinstance(summary, dict) and summary.get("bottom_band_blocked") is True:
        return True
    failed_taps = request.get("failed_tap_xy_this_state")
    if isinstance(failed_taps, list):
        failed_bottom_taps = 0
        for item in failed_taps:
            if not isinstance(item, dict):
                continue
            y = int_value(item.get("y"))
            if y is not None and y >= 560:
                failed_bottom_taps += 1
        if failed_bottom_taps >= 2:
            return True
    transitions = request.get("bottom_tap_transitions_this_state")
    if not isinstance(transitions, list):
        return False
    valid_transitions = [item for item in transitions if isinstance(item, dict)]
    if len(valid_transitions) >= 2:
        return True
    return any(item.get("changed") is False for item in valid_transitions)


def direct_modal_guard_response(
    request: dict[str, Any],
    action_type: object,
    x: int | None,
    y: int | None,
    selected_active_layer: str,
    memory_updates: list[str],
) -> dict[str, object] | None:
    if request.get("direct_vision") is not True:
        return None
    local_hint = string_value(request.get("local_active_layer_hint")) or "unknown"
    if is_mercenary_knowledge_goal(request) and has_large_modal_panel(request):
        if action_type in {"wait", "back"}:
            return mercenary_knowledge_scroll_response(request, selected_active_layer, memory_updates)
        if action_type == "tap_xy" and y is not None and y >= 560:
            return mercenary_knowledge_scroll_response(request, selected_active_layer, memory_updates)
        return None
    if local_hint == "normal" and selected_active_layer != "modal":
        return None
    safe_candidate = safest_modal_candidate(request)
    if local_hint != "modal" and selected_active_layer != "modal" and safe_candidate is None:
        return None
    if action_type == "tap_xy" and x is not None and y is not None and safe_candidate is not None and selected_active_layer == "modal":
        _, safe_x, safe_y, _ = safe_candidate
        if abs(x - safe_x) <= 36 and abs(y - safe_y) <= 36:
            return None

    if action_type == "tap_xy" and x is not None and y is not None and local_hint != "modal" and selected_active_layer != "modal":
        # A detected popup in an old request should only override clearly background/bottom taps.
        if y < 560:
            return None

    if action_type == "back" and local_hint != "modal" and selected_active_layer != "modal":
        return None

    if safe_candidate is None:
        return {
            "type": "back",
            "candidate_id": None,
            "x": None,
            "y": None,
            "x2": None,
            "y2": None,
            "duration_ms": None,
            "selected_active_layer": "modal",
            "memory_updates": memory_updates,
            "reason": "Modal guard overrode the planner action because a popup is active and no safe popup button candidate was detected; using back to dismiss.",
        }
    candidate_id, safe_x, safe_y, label = safe_candidate
    return {
        "type": "tap_xy",
        "candidate_id": None,
        "x": safe_x,
        "y": safe_y,
        "x2": None,
        "y2": None,
        "duration_ms": None,
        "selected_active_layer": "modal",
        "memory_updates": memory_updates,
        "reason": f"Modal guard overrode {action_type} to tap safe popup dismiss candidate {candidate_id} at ({safe_x},{safe_y}); {label}.",
    }


def mercenary_knowledge_bottom_guard_response(
    request: dict[str, Any],
    x: int,
    y: int,
    selected_active_layer: str,
    memory_updates: list[str],
) -> dict[str, object] | None:
    if request.get("direct_vision") is not True or y < 560:
        return None
    if not is_mercenary_knowledge_goal(request):
        return None
    local_hint = string_value(request.get("local_active_layer_hint")) or "unknown"
    modal_score = numeric_value(request.get("local_modal_score")) or 0.0
    if local_hint == "modal" or selected_active_layer == "modal" or modal_score >= 0.65 or has_large_modal_panel(request):
        return mercenary_knowledge_scroll_response(request, selected_active_layer, memory_updates)
    return None


def mercenary_knowledge_scroll_response(
    request: dict[str, Any],
    selected_active_layer: str,
    memory_updates: list[str],
) -> dict[str, object]:
    width, height = screen_bounds(request)
    x = width // 2
    return {
        "type": "swipe",
        "candidate_id": None,
        "x": x,
        "y": round(height * 0.78),
        "x2": x,
        "y2": round(height * 0.38),
        "duration_ms": 650,
        "selected_active_layer": "modal" if selected_active_layer == "modal" else "normal",
        "memory_updates": memory_updates,
        "reason": "Blocked bottom navigation on an active mercenary knowledge panel; scrolling inside the panel/list to collect more synergy or recipe information.",
    }


def is_mercenary_knowledge_goal(request: dict[str, Any]) -> bool:
    goal = resolve_goal(request.get("current_goal") or request.get("goal"))
    return string_value(goal.get("goal_id")) == "inspect_mercenary_synergy"


def has_large_modal_panel(request: dict[str, Any]) -> bool:
    screenshot_path = string_value(request.get("screenshot_path"))
    if not screenshot_path:
        return False
    width, height = screen_bounds(request)
    try:
        from tools.candidates import find_candidates

        candidates = find_candidates(screenshot_path)
    except Exception as exc:
        print(f"Knowledge panel guard could not detect modal panels: {exc!r}", flush=True)
        return False
    for candidate in candidates:
        if getattr(candidate, "layer", None) != "modal":
            continue
        if getattr(candidate, "kind", None) != "popup":
            continue
        bbox = getattr(candidate, "bbox", None)
        if not bbox or len(bbox) != 4:
            continue
        _, top, box_width, box_height = bbox
        if box_width >= width * 0.70 and box_height >= height * 0.45 and top <= height * 0.30:
            return True
    return False


def screen_bounds(request: dict[str, Any]) -> tuple[int, int]:
    bounds = request.get("screen_bounds")
    if isinstance(bounds, dict):
        width = int_value(bounds.get("width")) or 360
        height = int_value(bounds.get("height")) or 640
        return max(1, width), max(1, height)
    return 360, 640


def safest_modal_candidate(request: dict[str, Any]) -> tuple[str, int, int, str] | None:
    screenshot_path = string_value(request.get("screenshot_path"))
    if not screenshot_path:
        return None
    try:
        from tools.candidates import find_candidates, is_safe_popup_candidate

        candidates = find_candidates(screenshot_path)
    except Exception as exc:
        print(f"Modal guard could not detect popup candidates: {exc!r}", flush=True)
        return None
    safe_candidates = [candidate for candidate in candidates if candidate.layer == "modal" and is_safe_popup_candidate(candidate)]
    if not safe_candidates:
        return None
    candidate = max(safe_candidates, key=popup_dismiss_priority)
    tap_x, tap_y = candidate.tap_point or (candidate.x, candidate.y)
    return candidate.id, int(tap_x), int(tap_y), candidate.label_guess


def popup_dismiss_priority(candidate: object) -> tuple[int, int, float, float, float]:
    candidate_id = string_value(getattr(candidate, "id", None)) or ""
    label = string_value(getattr(candidate, "label_guess", None)) or ""
    text = f"{candidate_id} {label}".lower()
    x = numeric_value(getattr(candidate, "x", None)) or 0.0
    y = numeric_value(getattr(candidate, "y", None)) or 0.0
    score = numeric_value(getattr(candidate, "score", None)) or 0.0
    detected_button = 1 if candidate_id.startswith("modal_button") else 0
    not_close = 0 if "close" in text else 1
    near_expected_cancel = -abs(x - 240)
    lower_dialog_band = -abs(y - 375)
    return detected_button, not_close, near_expected_cancel, lower_dialog_band, score


def popup_bboxes(candidates: object) -> list[tuple[str, list[float]]]:
    if not isinstance(candidates, list):
        return []
    boxes: list[tuple[str, list[float]]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        kind = string_value(candidate.get("kind"))
        if kind not in {"popup", "modal"}:
            continue
        bbox = numeric_bbox(candidate.get("bbox"))
        candidate_id = MockBridge.candidate_id(candidate)
        if bbox is not None and candidate_id:
            boxes.append((candidate_id, bbox))
    return boxes


def is_popup_active(candidates: object) -> bool:
    if not isinstance(candidates, list):
        return False
    return any(isinstance(candidate, dict) and (string_value(candidate.get("layer")) == "modal" or string_value(candidate.get("kind")) in {"popup", "modal", "popup_button"}) for candidate in candidates)


def active_layer(request: dict[str, Any], candidates: object) -> str:
    request_layer = string_value(request.get("active_layer"))
    if request_layer in {"modal", "normal"}:
        return request_layer
    local_hint = string_value(request.get("local_active_layer_hint"))
    if local_hint in {"modal", "normal"}:
        return local_hint
    return "modal" if is_popup_active(candidates) else "normal"


def is_popup_active_candidate(candidate: object, popup_boxes: list[tuple[str, list[float]]]) -> bool:
    if not isinstance(candidate, dict):
        return False
    candidate_id = MockBridge.candidate_id(candidate) or ""
    kind = string_value(candidate.get("kind"))
    if string_value(candidate.get("layer")) == "modal":
        return True
    if kind == "popup_button":
        return True
    if kind in {"popup", "modal"}:
        return True
    if any(token in candidate_id for token in ("popup_cancel", "popup_close", "popup_confirm")):
        return True
    if candidate_id in {"right_mid_lower", "left_mid_lower"}:
        return True
    return is_inside_any_popup(candidate, popup_boxes)


def popup_safety_reason(candidate_id: str) -> str:
    if "popup_cancel" in candidate_id or candidate_id == "right_mid_lower":
        return "selected cancel/right-side button to avoid exiting game"
    if "popup_close" in candidate_id or "close" in candidate_id:
        return "selected close button to dismiss modal safely"
    return "selected popup-internal candidate while suppressing background actions"


def is_inside_any_popup(candidate: dict[str, Any], popup_boxes: list[tuple[str, list[float]]]) -> bool:
    parent = string_value(candidate.get("parent")) or string_value(candidate.get("group"))
    if parent and any(parent == popup_id for popup_id, _ in popup_boxes):
        return True
    x = numeric_value(candidate.get("x"))
    y = numeric_value(candidate.get("y"))
    if x is None or y is None:
        return False
    for _, bbox in popup_boxes:
        left, top, width, height = bbox
        if left <= x <= left + width and top <= y <= top + height:
            return True
    return False


def read_action_records(actions_log_path: Path) -> list[dict[str, Any]]:
    if not actions_log_path.exists():
        return []
    try:
        lines = actions_log_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"Could not read actions log for exclusions: {exc}", flush=True)
        return []

    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def collect_excluded_candidate_ids(request: dict[str, Any], action_records: list[dict[str, Any]], state_graph_path: Path) -> set[str]:
    excluded_ids = request_tried_candidate_ids(request)
    current_state_id = string_value(request.get("state_id") or request.get("current_state_id"))
    current_screen_hash = string_value(request.get("screen_hash")) or screen_hash_for_state(state_graph_path, current_state_id)

    for record in action_records:
        record_candidate_id = string_value(record.get("candidate_id"))
        if not record_candidate_id:
            continue
        record_state_id = string_value(record.get("state_id"))
        record_screen_hash = string_value(record.get("screen_hash"))
        same_state = bool(current_state_id and record_state_id == current_state_id)
        same_hash = bool(current_screen_hash and record_screen_hash == current_screen_hash)
        if same_state or same_hash:
            excluded_ids.add(record_candidate_id)

    return candidate_scoped_exclusions(request, excluded_ids, action_records)


def request_tried_candidate_ids(request: dict[str, Any]) -> set[str]:
    tried_candidates = request.get("tried_candidates")
    if not isinstance(tried_candidates, list):
        return set()
    return {candidate_id for candidate_id in tried_candidates if isinstance(candidate_id, str)}


def screen_hash_for_state(state_graph_path: Path, state_id: str | None) -> str | None:
    if not state_id or not state_graph_path.exists():
        return None
    try:
        graph = json.loads(state_graph_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read state graph for exclusions: {exc}", flush=True)
        return None
    states = graph.get("states") if isinstance(graph, dict) else None
    if not isinstance(states, list):
        return None
    for state in states:
        if not isinstance(state, dict):
            continue
        if state.get("state_id") == state_id:
            return string_value(state.get("hash"))
    return None


def candidate_scoped_exclusions(request: dict[str, Any], excluded_ids: set[str], action_records: list[dict[str, Any]] | None = None) -> set[str]:
    candidates = request.get("candidates")
    if not isinstance(candidates, list):
        return excluded_ids
    failed_same_tap_ids = failed_same_tap_candidate_ids(candidates, action_records or [])
    modal_active = active_layer(request, candidates) == "modal"
    scoped_ids: set[str] = set()
    for candidate in candidates:
        candidate_aliases = aliases_for_candidate(candidate)
        if candidate_aliases & excluded_ids:
            if modal_active and is_modal_safe_dismiss_candidate(candidate) and not (candidate_aliases & failed_same_tap_ids):
                continue
            scoped_ids.update(candidate_aliases)
    return scoped_ids


def failed_same_tap_candidate_ids(candidates: list[object], action_records: list[dict[str, Any]], *, tolerance: int = 6) -> set[str]:
    failed_ids: set[str] = set()
    for candidate in candidates:
        candidate_id = MockBridge.candidate_id(candidate)
        if not candidate_id or not isinstance(candidate, dict):
            continue
        x = numeric_value(candidate.get("x"))
        y = numeric_value(candidate.get("y"))
        if x is None or y is None:
            continue
        aliases = aliases_for_candidate(candidate)
        for record in action_records:
            if record.get("changed") is not False or string_value(record.get("candidate_id")) not in aliases:
                continue
            record_x = numeric_value(record.get("x"))
            record_y = numeric_value(record.get("y"))
            if record_x is None or record_y is None:
                continue
            if abs(record_x - x) <= tolerance and abs(record_y - y) <= tolerance:
                failed_ids.update(aliases)
                break
    return failed_ids


def is_modal_safe_dismiss_candidate(candidate: object) -> bool:
    if not isinstance(candidate, dict):
        return False
    candidate_id = MockBridge.candidate_id(candidate) or ""
    label = (string_value(candidate.get("label_guess")) or "").lower()
    layer = string_value(candidate.get("layer"))
    if layer != "modal":
        return False
    return any(token in candidate_id.lower() or token in label for token in ("popup_cancel", "cancel", "right", "close", "no", "x", "back"))


def is_excluded_candidate(candidate: object, excluded_ids: set[str]) -> bool:
    return bool(aliases_for_candidate(candidate) & excluded_ids)


def is_goal_forced_candidate(candidate: object, goal: dict[str, Any], popup_active: bool) -> bool:
    return False


def forced_goal_candidate(candidates: list[object], goal: dict[str, Any], popup_active: bool) -> dict[str, Any] | None:
    return None


def modal_candidates_exhausted(request: dict[str, Any], candidates: object, excluded_ids: set[str]) -> bool:
    if active_layer(request, candidates) != "modal" or not isinstance(candidates, list):
        return False
    modal_candidates = [candidate for candidate in candidates if is_popup_active_candidate(candidate, popup_bboxes(candidates))]
    return not modal_candidates or all(is_excluded_candidate(candidate, excluded_ids) for candidate in modal_candidates)


def modal_exhausted_back_action() -> dict[str, object]:
    return {
        "type": "back",
        "candidate_id": None,
        "x": None,
        "y": None,
        "selected_active_layer": "modal",
        "reason": "modal active and all candidates exhausted, pressing back to dismiss modal",
    }


def direct_vision_unavailable_action(reason: str) -> dict[str, object]:
    return {
        "type": "wait",
        "candidate_id": None,
        "x": None,
        "y": None,
        "x2": None,
        "y2": None,
        "duration_ms": None,
        "selected_active_layer": "unknown",
        "memory_updates": [],
        "reason": f"direct_vision_unavailable:{reason}",
    }


def aliases_for_candidate(candidate: object) -> set[str]:
    candidate_id = MockBridge.candidate_id(candidate)
    if not candidate_id:
        return set()
    aliases = {candidate_id}
    if candidate_id.startswith("bottom_menu_"):
        aliases.add(candidate_id.replace("bottom_menu_", "bottom_nav_", 1))
    elif candidate_id.startswith("bottom_nav_"):
        aliases.add(candidate_id.replace("bottom_nav_", "bottom_menu_", 1))
    return aliases


def string_value(value: object) -> str | None:
    return value if isinstance(value, str) else None


def numeric_value(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def int_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def point_in_screen_bounds(request: dict[str, Any], x: int, y: int) -> bool:
    bounds = request.get("screen_bounds")
    if not isinstance(bounds, dict):
        return False
    width = int_value(bounds.get("width"))
    height = int_value(bounds.get("height"))
    if width is None or height is None or width <= 0 or height <= 0:
        return False
    return 0 <= x < width and 0 <= y < height


def numeric_bbox(value: object) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    numbers = [numeric_value(item) for item in value]
    if any(item is None for item in numbers):
        return None
    return [float(item) for item in numbers if item is not None]


def infer_kind_from_candidate_id(candidate_id: str | None) -> str | None:
    if not candidate_id:
        return None
    if candidate_id.startswith("bottom_menu") or candidate_id.startswith("bottom_nav"):
        return "bottom_menu"
    if candidate_id.startswith("contour"):
        return "contour"
    if candidate_id.startswith("bright"):
        return "bright_region"
    return "fixed"


def format_ranking_log(ranking: Ranking) -> str:
    return (
        f"Candidate ranking: id={ranking.candidate_id} kind={ranking.kind} "
        f"bbox={format_bbox(ranking.candidate)} raw_score={ranking.raw_score:.3f} adjusted_score={ranking.adjusted_score:.3f} "
        f"breakdown={format_breakdown(ranking.breakdown)}"
    )


def format_breakdown(breakdown: list[tuple[str, float]]) -> str:
    return ", ".join(f"{name}={value:+.3f}" for name, value in breakdown)


def summarize_breakdown(breakdown: list[tuple[str, float]]) -> str:
    important = [(name, value) for name, value in breakdown if name != "raw_score" and abs(value) >= 0.05]
    if not important:
        return "no major adjustments"
    return "adjustments: " + format_breakdown(important[:4])


def format_bbox(candidate: dict[str, Any]) -> str:
    bbox = candidate.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return "none"
    return "[" + ",".join(str(value) for value in bbox) + "]"


def wait_action(reason: str) -> dict[str, object]:
    return {"type": "wait", "candidate_id": None, "x": None, "y": None, "selected_active_layer": "unknown", "reason": reason}


def write_response(response_path: Path, response: dict[str, object]) -> None:
    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
