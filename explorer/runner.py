from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from explorer.evaluator import evaluate_action_result
from explorer.goals import DEFAULT_GOAL_PROGRESS_PATH, append_goal_progress, goal_description, resolve_goal
from explorer.learning_memory import DEFAULT_LEARNING_MEMORY_PATH, update_learning_memory
from explorer.memory import ActionRecord, append_record, screen_changed, utc_now
from explorer.mercenary_knowledge import extract_mercenary_knowledge_from_image
from explorer.mercenary_memory import append_mercenary_memory
from explorer.mercenary_inspection import inspection_started, next_knowledge_panel_target, next_list_target
from explorer.planner import BasePlanner, ExternalFilePlanner, MockPlanner, PlannerAction, append_planner_decision
from explorer.prompt_builder import build_planner_prompt, prompt_summary
from explorer.repair import RepairCandidate, generate_repair_candidates, load_repair_memory, modal_candidate, repair_candidate_to_dict, save_successful_repair
from explorer.safety import is_risky_candidate
from explorer.screen_hash import perceptual_hash
from explorer.state_graph import StateGraph
from tools.candidates import Candidate, find_candidates, is_safe_popup_candidate, local_screen_analysis, save_candidates_debug
from tools.capture import ADBConfig, capture_screen
from tools.input import back, swipe, tap, wait


DEFAULT_CONFIG = Path("config.yaml")
MODAL_EXHAUSTED_REASON = "modal active and all candidates exhausted, pressing back to dismiss modal"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autonomous LDPlayer game explorer with state graph")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--execute", action="store_true", help="Actually send input to LDPlayer.")
    parser.add_argument("--iterations", type=int, default=1, help="Loop count. Use 0 to run forever.")
    parser.add_argument("--interval", type=float, default=0.5, help="Delay between loop iterations.")
    parser.add_argument("--back-when-exhausted", action="store_true", help="Deprecated. Back is selected automatically when a state is exhausted.")
    parser.add_argument("--unsafe", action="store_true", help="Allow top/popup candidates that may hit logout, exit, or purchase flows.")
    parser.add_argument("--debug-candidates", action="store_true", help="Save candidate visualization to runtime/screenshots/candidates_debug.png.")
    parser.add_argument("--planner", choices=("mock", "external"), default="mock")
    parser.add_argument("--wait-for-response", action="store_true", help="In external planner mode, wait for planner_response.json.")
    parser.add_argument("--response-timeout", type=float, default=300.0, metavar="SEC", help="Seconds to wait for external planner response.")
    parser.add_argument("--clear-response-after-use", action="store_true", help="Delete planner_response.json after reading it.")
    parser.add_argument("--enable-repair", action="store_true", help="Try geometry-based repair taps after unchanged failed actions.")
    parser.add_argument("--max-repair-attempts", type=int, default=5, help="Maximum repair taps after one failed action.")
    parser.add_argument("--direct-vision", action="store_true", help="Fast path: send only the screenshot and goal to OpenCode, then execute returned tap_xy directly.")
    parser.add_argument("--korean-ocr", action="store_true", help="Run reusable Korean OCR on mercenary knowledge screens and store parsed knowledge.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    dry_run = not args.execute
    safe_mode = bool(config.get("safe_mode", True)) and not args.unsafe

    adb_config = ADBConfig(
        adb_path=str(config.get("adb_path", r"C:\LDPlayer\LDPlayer9\adb.exe")),
        device_id=str(config.get("device_id", "emulator-5554")),
        timeout=float(config.get("adb_timeout", 15)),
    )
    screenshot_dir = Path(str(config.get("screenshot_dir", "runtime/screenshots")))
    actions_log = Path(str(config.get("actions_log", "runtime/actions.jsonl")))
    state_graph_path = Path(str(config.get("state_graph", "runtime/state_graph.json")))
    planner_decisions_log = Path(str(config.get("planner_decisions", "runtime/planner_decisions.jsonl")))
    planner_request_path = Path(str(config.get("planner_request", "runtime/planner_request.json")))
    planner_response_path = Path(str(config.get("planner_response", "runtime/planner_response.json")))
    repair_memory_path = Path(str(config.get("repair_memory", "runtime/repair_memory.json")))
    learning_memory_path = Path(str(config.get("learning_memory", str(DEFAULT_LEARNING_MEMORY_PATH))))
    goal_progress_path = Path(str(config.get("goal_progress", str(DEFAULT_GOAL_PROGRESS_PATH))))
    current_goal = resolve_goal(config.get("current_goal", "explore_safely"))
    planner_goal = goal_description(current_goal)
    hash_threshold = int(config.get("state_hash_threshold", 6))
    settle_seconds = float(config.get("settle_seconds", 1.5))
    change_threshold = float(config.get("change_threshold", 0.015))
    graph = StateGraph.load(state_graph_path, threshold=hash_threshold)
    planner = create_planner(
        args.planner,
        planner_request_path,
        planner_response_path,
        wait_for_response=args.wait_for_response,
        response_timeout_sec=args.response_timeout,
        clear_response_after_use=args.clear_response_after_use,
    )

    start_iteration = next_screenshot_index(screenshot_dir)
    iteration = 0
    while args.iterations == 0 or iteration < args.iterations:
        iteration += 1
        run_iteration(
            adb_config,
            screenshot_dir=screenshot_dir,
            actions_log=actions_log,
            planner_decisions_log=planner_decisions_log,
            graph=graph,
            planner=planner,
            planner_goal=planner_goal,
            current_goal=current_goal,
            dry_run=dry_run,
            settle_seconds=settle_seconds,
            change_threshold=change_threshold,
            iteration=start_iteration + iteration - 1,
            safe_mode=safe_mode,
            debug_candidates=args.debug_candidates,
            enable_repair=args.enable_repair,
            max_repair_attempts=args.max_repair_attempts,
            direct_vision=args.direct_vision,
            korean_ocr=args.korean_ocr,
            repair_memory_path=repair_memory_path,
            learning_memory_path=learning_memory_path,
            goal_progress_path=goal_progress_path,
        )
        if args.iterations == 0 or iteration < args.iterations:
            wait(args.interval)


def run_iteration(
    adb_config: ADBConfig,
    *,
    screenshot_dir: Path,
    actions_log: Path,
    planner_decisions_log: Path,
    graph: StateGraph,
    planner: BasePlanner,
    planner_goal: str,
    current_goal: dict[str, Any],
    dry_run: bool,
    settle_seconds: float,
    change_threshold: float,
    iteration: int,
    safe_mode: bool,
    debug_candidates: bool,
    enable_repair: bool,
    max_repair_attempts: int,
    direct_vision: bool,
    korean_ocr: bool,
    repair_memory_path: Path,
    learning_memory_path: Path,
    goal_progress_path: Path,
) -> None:
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    before_path = screenshot_dir / f"{iteration:06d}_before.png"
    current_path = screenshot_dir / "current.png"

    capture_screen(adb_config, current_path)
    shutil.copy2(current_path, before_path)
    planner_screenshot_path = create_direct_vision_image(current_path, screenshot_dir / "current_vision.jpg") if direct_vision else current_path
    current_hash = perceptual_hash(before_path)
    current_state = graph.get_or_create_state(current_hash, before_path)
    graph.save()

    visual_candidates = find_candidates(before_path)
    candidates = [] if direct_vision else filter_candidates(visual_candidates, safe_mode=safe_mode)
    local_analysis_before = fast_local_analysis(planner_screenshot_path) if direct_vision else local_screen_analysis(before_path)
    local_active_layer_hint = str(local_analysis_before.get("local_active_layer_hint") or "unknown")
    debug_path: Path | None = None
    if not direct_vision and (debug_candidates or isinstance(planner, ExternalFilePlanner)):
        debug_path = screenshot_dir / "candidates_debug.png"
        save_candidates_debug(current_path, candidates, debug_path, local_active_layer_hint=local_active_layer_hint)
        print(f"Saved candidate debug visualization: {debug_path}")
    state_id = str(current_state["state_id"])
    prompt = "direct_vision" if direct_vision else build_planner_prompt(graph=graph, state_id=state_id, candidates=candidates, current_goal=current_goal)
    ocr_memory_updates = collect_korean_ocr_memory(
        enabled=korean_ocr,
        current_goal=current_goal,
        screenshot_path=before_path,
        state_id=state_id,
        local_active_layer_hint=local_active_layer_hint,
    )
    action = goal_policy_action(
        current_goal=current_goal,
        graph=graph,
        state_id=state_id,
        visual_candidates=visual_candidates,
        screen_bounds=screen_bounds_from_analysis(local_analysis_before),
    )
    if action is None:
        action = planner.choose_action(
            prompt=prompt,
            graph=graph,
            state_id=state_id,
            candidates=candidates,
            screenshot_path=str(planner_screenshot_path),
            debug_image_path=str(debug_path) if debug_path else "",
            goal=current_goal,
            direct_vision=direct_vision,
        )
    active_layer_before = action.selected_active_layer if action.selected_active_layer in {"normal", "modal"} else "unknown"
    action = normalize_modal_exhausted_action(action, active_layer_before, graph, state_id, candidates)
    append_mercenary_memory(
        state_id=state_id,
        screenshot_path=str(planner_screenshot_path),
        selected_active_layer=action.selected_active_layer,
        observations=[*ocr_memory_updates, *(action.memory_updates or [])],
    )
    if debug_path is not None:
        save_candidates_debug(current_path, candidates, debug_path, local_active_layer_hint=local_active_layer_hint, selected_active_layer=action.selected_active_layer)
    print(
        f"Layer decision: local_active_layer_hint={local_active_layer_hint} "
        f"selected_active_layer={action.selected_active_layer} used_tap_xy={action.type == 'tap_xy'}"
    )
    append_planner_decision(
        planner_decisions_log,
        state_id=state_id,
        selected_action=action,
        prompt_summary=prompt_summary(prompt),
        local_active_layer_hint=local_active_layer_hint,
    )
    print_action(iteration, str(current_state["state_id"]), current_hash, action, dry_run, candidates)

    if direct_vision and action.type == "wait":
        print("Direct vision planner did not return an executable action; not recording a no-op edge for this state.")
        return

    after_path: Path | None = None
    changed = False
    after_state_id = str(current_state["state_id"])
    selected_candidate: Candidate | None = None
    if dry_run:
        print("Dry run: state graph edge not recorded.")
    else:
        selected_candidate = find_candidate_by_id(candidates, action.candidate_id)
        if is_modal_exhausted_back_action(action, active_layer_before, graph, state_id, candidates):
            repaired = run_repair_loop(
                adb_config,
                screenshot_dir=screenshot_dir,
                actions_log=actions_log,
                graph=graph,
                repair_memory_path=repair_memory_path,
                parent_state_id=str(current_state["state_id"]),
                parent_screen_hash=current_hash,
                original_candidate=None,
                candidates=candidates,
                evaluation_reasons=["modal_active_candidates_exhausted"],
                settle_seconds=settle_seconds,
                change_threshold=change_threshold,
                iteration=iteration,
                max_attempts=max_repair_attempts,
                debug_path=debug_path,
                learning_memory_path=learning_memory_path,
                goal_progress_path=goal_progress_path,
                current_goal=current_goal,
            )
            if repaired:
                print("Modal exhaustion repair changed the screen; skipping back fallback.")
                return
            print("Modal exhaustion repair did not change the screen; falling back to back.")
        execute_action(adb_config, action, selected_candidate, settle_seconds=settle_seconds, screen_bounds=screen_bounds_from_analysis(local_analysis_before))
        wait(settle_seconds)
        after_path = screenshot_dir / f"{iteration:06d}_after.png"
        capture_screen(adb_config, after_path)
        after_hash = perceptual_hash(after_path)
        after_state = graph.get_or_create_state(after_hash, after_path)
        after_state_id = str(after_state["state_id"])
        changed = screen_changed(before_path, after_path, threshold=change_threshold)
        after_candidates = [] if direct_vision else filter_candidates(find_candidates(after_path), safe_mode=safe_mode)
        active_layer_after = str((fast_local_analysis(after_path) if direct_vision else local_screen_analysis(after_path)).get("local_active_layer_hint") or "unknown")
        graph.add_edge(
            from_state=str(current_state["state_id"]),
            to_state=after_state_id,
            action=edge_action(action, selected_candidate),
            changed=changed,
        )
        graph.save()
        print(f"Result: to_state={after_state_id} changed={changed}")
        evaluation = evaluate_action_result(
            changed=changed,
            before_state_id=str(current_state["state_id"]),
            after_state_id=after_state_id,
            active_layer_before=active_layer_before,
            active_layer_after=active_layer_after,
            candidate=selected_candidate,
            recent_records=load_recent_action_records(actions_log),
        )
        update_learning_memory(
            learning_memory_path,
            action_type=action.type,
            candidate=selected_candidate,
            changed=changed,
            active_layer_before=active_layer_before,
            active_layer_after=active_layer_after,
            before_state_id=str(current_state["state_id"]),
            after_state_id=after_state_id,
        )
        append_goal_progress(
            goal_progress_path,
            goal=current_goal,
            action_type=action.type,
            candidate_id=action.candidate_id,
            changed=changed,
            before_state_id=str(current_state["state_id"]),
            after_state_id=after_state_id,
            active_layer_before=active_layer_before,
            active_layer_after=active_layer_after,
        )
        if enable_repair and evaluation.failed:
            run_repair_loop(
                adb_config,
                screenshot_dir=screenshot_dir,
                actions_log=actions_log,
                graph=graph,
                repair_memory_path=repair_memory_path,
                parent_state_id=after_state_id,
                parent_screen_hash=after_hash,
                original_candidate=selected_candidate,
                candidates=after_candidates,
                evaluation_reasons=evaluation.reasons,
                settle_seconds=settle_seconds,
                change_threshold=change_threshold,
                iteration=iteration,
                max_attempts=max_repair_attempts,
                debug_path=debug_path,
                learning_memory_path=learning_memory_path,
                goal_progress_path=goal_progress_path,
                current_goal=current_goal,
            )

    append_record(
        actions_log,
        ActionRecord(
            time=utc_now(),
            screen_hash=current_hash,
            candidate_id=action.candidate_id or action.type,
            x=selected_action_x(action, candidates),
            y=selected_action_y(action, candidates),
            changed=changed,
            executed=not dry_run,
            before=str(before_path),
            after=str(after_path) if after_path else None,
            label_guess=action.reason,
            kind=selected_candidate.kind if selected_candidate else "tap_xy" if action.type == "tap_xy" else None,
            layer=selected_candidate.layer if selected_candidate else action.selected_active_layer if action.type == "tap_xy" else None,
            bbox=list(selected_candidate.bbox) if selected_candidate and selected_candidate.bbox is not None else None,
            visual_center=list(selected_candidate.visual_center or (selected_candidate.x, selected_candidate.y)) if selected_candidate else None,
            tap_point=list(selected_candidate.tap_point or (selected_candidate.x, selected_candidate.y)) if selected_candidate else None,
            is_repair=False,
        ),
    )


def goal_policy_action(
    *,
    current_goal: dict[str, Any],
    graph: StateGraph,
    state_id: str,
    visual_candidates: list[Candidate],
    screen_bounds: tuple[int, int],
) -> PlannerAction | None:
    if str(current_goal.get("goal_id") or "") != "inspect_mercenary_synergy":
        return None
    in_mercenary_tab = reached_by_mercenary_tab(graph, state_id)
    in_mercenary_detail = reached_by_mercenary_card(graph, state_id)
    knowledge_panel_close = visible_knowledge_panel_close_candidate(visual_candidates, screen_bounds=screen_bounds)

    safe_modal = safest_popup_dismiss_candidate(visual_candidates, screen_bounds=screen_bounds)
    if knowledge_panel_close is not None and inspection_started() and not in_mercenary_tab and not in_mercenary_detail:
        x, y = knowledge_panel_close
        return PlannerAction(
            "tap_xy",
            None,
            "Mercenary inspection policy closes a visible knowledge panel whose runtime state was already marked complete.",
            "goal_policy",
            x=x,
            y=y,
            selected_active_layer="normal",
        )

    if safe_modal is not None and not in_mercenary_tab:
        x, y = safe_modal.tap_point or (safe_modal.x, safe_modal.y)
        return PlannerAction(
            "tap_xy",
            None,
            f"Goal policy dismisses popup before opening Mercenary tab via {safe_modal.id}.",
            "goal_policy",
            x=x,
            y=y,
            selected_active_layer="modal",
        )

    if in_mercenary_detail:
        return PlannerAction(
            "back",
            None,
            "Mercenary inspection policy returns from mercenary detail immediately after OCR capture.",
            "goal_policy",
            selected_active_layer="normal",
        )

    if in_mercenary_tab:
        target = next_list_target(screen_bounds)
        if target is None:
            return None
        if target.get("kind") == "scroll":
            return PlannerAction(
                "swipe",
                None,
                f"Mercenary inspection policy scrolls list to reveal more cards: {target['id']}.",
                "goal_policy",
                x=int(target["x"]),
                y=int(target["y"]),
                x2=int(target["x2"]),
                y2=int(target["y2"]),
                duration_ms=int(target.get("duration_ms") or 650),
                selected_active_layer="normal",
            )
        return PlannerAction(
            "tap_xy",
            None,
            f"Mercenary inspection policy selects {target['kind']} target {target['id']} for knowledge collection.",
            "goal_policy",
            x=int(target["x"]),
            y=int(target["y"]),
            selected_active_layer="normal",
            memory_updates=[f"Selected mercenary inspection target {target['id']} at ({target['x']},{target['y']})."],
        )

    if inspection_started():
        target = next_knowledge_panel_target(screen_bounds)
        if target is None:
            return mercenary_tab_action(screen_bounds, reason="Mercenary inspection policy reopens the Mercenary tab after returning to a non-list screen.")
        if target.get("kind") == "synergy_scroll":
            return PlannerAction(
                "swipe",
                None,
                f"Mercenary inspection policy scrolls synergy panel before returning to card inspection: {target['id']}.",
                "goal_policy",
                x=int(target["x"]),
                y=int(target["y"]),
                x2=int(target["x2"]),
                y2=int(target["y2"]),
                duration_ms=int(target.get("duration_ms") or 650),
                selected_active_layer="normal",
            )
        return PlannerAction(
            "tap_xy",
            None,
            f"Mercenary inspection policy closes the synergy panel after collecting visible synergy pages: {target['id']}.",
            "goal_policy",
            x=int(target["x"]),
            y=int(target["y"]),
            selected_active_layer="normal",
        )

    if failed_mercenary_tab_from_state(graph, state_id):
        return None

    return mercenary_tab_action(screen_bounds, reason="Goal policy opens the Mercenary tab using the calibrated lower navigation coordinate.")


def mercenary_tab_action(screen_bounds: tuple[int, int], *, reason: str) -> PlannerAction:
    width, height = screen_bounds
    if width <= 0 or height <= 0:
        width, height = 360, 640
    x = round(width * 0.208)
    y = round(height * 0.944)
    return PlannerAction(
        "tap_xy",
        None,
        reason,
        "goal_policy",
        x=x,
        y=y,
        selected_active_layer="normal",
    )


def collect_korean_ocr_memory(
    *,
    enabled: bool,
    current_goal: dict[str, Any],
    screenshot_path: Path,
    state_id: str,
    local_active_layer_hint: str,
) -> list[str]:
    if not enabled:
        return []
    if str(current_goal.get("goal_id") or "") != "inspect_mercenary_synergy":
        return []
    if local_active_layer_hint != "modal" and not inspection_started():
        return []
    updates = extract_mercenary_knowledge_from_image(screenshot_path, state_id=state_id)
    if updates:
        print(f"Korean OCR extracted {len(updates)} mercenary knowledge update(s).")
    return updates


def safest_popup_dismiss_candidate(candidates: list[Candidate], *, screen_bounds: tuple[int, int]) -> Candidate | None:
    _, height = screen_bounds
    if height <= 0:
        height = 640
    safe_candidates = [
        candidate
        for candidate in candidates
        if candidate.layer == "modal" and candidate.y >= height * 0.45 and is_safe_popup_candidate(candidate)
    ]
    if not safe_candidates:
        return None
    return max(safe_candidates, key=popup_dismiss_priority)


def popup_dismiss_priority(candidate: Candidate) -> tuple[int, int, float, float, float]:
    text = f"{candidate.id} {candidate.label_guess}".lower()
    detected_button = 1 if candidate.id.startswith("modal_button") else 0
    not_close = 0 if "close" in text else 1
    near_expected_cancel = -abs(candidate.x - 240)
    lower_dialog_band = -abs(candidate.y - 375)
    return detected_button, not_close, near_expected_cancel, lower_dialog_band, candidate.score


def visible_knowledge_panel_close_candidate(candidates: list[Candidate], *, screen_bounds: tuple[int, int]) -> tuple[int, int] | None:
    width, height = screen_bounds
    if width <= 0 or height <= 0:
        width, height = 360, 640
    has_large_panel = False
    close_candidates: list[Candidate] = []
    for candidate in candidates:
        if candidate.layer != "modal":
            continue
        if candidate.kind == "popup" and candidate.bbox is not None:
            _, top, box_width, box_height = candidate.bbox
            if box_width >= width * 0.70 and box_height >= height * 0.45 and top <= height * 0.32:
                has_large_panel = True
        text = f"{candidate.id} {candidate.label_guess}".lower()
        if "close" in text or "/x" in text:
            close_candidates.append(candidate)
    if not has_large_panel:
        return None
    lower_close_candidates = [candidate for candidate in close_candidates if candidate.y >= height * 0.50]
    if lower_close_candidates:
        candidate = max(lower_close_candidates, key=lambda item: item.y)
        x, y = candidate.tap_point or (candidate.x, candidate.y)
        return int(x), int(y)
    return width // 2, round(height * 0.895)


def reached_by_mercenary_tab(graph: StateGraph, state_id: str) -> bool:
    for edge in reversed(graph.edges):
        if edge.get("to_state") != state_id or edge.get("changed") is not True:
            continue
        action = edge.get("action")
        if not isinstance(action, dict) or action.get("type") != "tap_xy":
            continue
        x = numeric_int(action.get("x"))
        y = numeric_int(action.get("y"))
        if x is not None and y is not None and 45 <= x <= 125 and y >= 560:
            return True
    return False


def reached_by_mercenary_card(graph: StateGraph, state_id: str) -> bool:
    for edge in reversed(graph.edges):
        if edge.get("to_state") != state_id or edge.get("changed") is not True:
            continue
        action = edge.get("action")
        if not isinstance(action, dict) or action.get("type") != "tap_xy":
            continue
        reason = str(action.get("label_guess") or action.get("reason") or "")
        if "mercenary_card" in reason or "Selected mercenary inspection target page_" in reason:
            return True
    return False


def failed_mercenary_tab_from_state(graph: StateGraph, state_id: str) -> bool:
    for edge in reversed(graph.edges):
        if edge.get("from_state") != state_id:
            continue
        action = edge.get("action")
        if not isinstance(action, dict) or action.get("type") != "tap_xy":
            continue
        x = numeric_int(action.get("x"))
        y = numeric_int(action.get("y"))
        if x is None or y is None:
            continue
        if 45 <= x <= 125 and y >= 560:
            return edge.get("changed") is False
    return False


def numeric_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def filter_candidates(candidates: list[Candidate], *, safe_mode: bool) -> list[Candidate]:
    # Candidate filtering is delegated to the planner/bridge. Local safety rules remain
    # available for future scoring, but should not remove visual context from the LLM.
    return candidates


def run_repair_loop(
    adb_config: ADBConfig,
    *,
    screenshot_dir: Path,
    actions_log: Path,
    graph: StateGraph,
    repair_memory_path: Path,
    parent_state_id: str,
    parent_screen_hash: str,
    original_candidate: Candidate | None,
    candidates: list[Candidate],
    evaluation_reasons: list[str],
    settle_seconds: float,
    change_threshold: float,
    iteration: int,
    max_attempts: int,
    debug_path: Path | None,
    learning_memory_path: Path,
    goal_progress_path: Path,
    current_goal: dict[str, Any],
) -> bool:
    repair_memory = load_repair_memory(repair_memory_path)
    repair_candidates = generate_repair_candidates(
        original_candidate=original_candidate,
        candidates=candidates,
        repair_memory=repair_memory,
        max_candidates=max_attempts * 3,
    )
    if not repair_candidates:
        print(f"Repair skipped: no repair candidates for reasons={evaluation_reasons}")
        return False

    if debug_path is not None:
        current_path = screenshot_dir / "current.png"
        save_candidates_debug(current_path, candidates, debug_path, repair_points=[repair_candidate_to_dict(candidate) for candidate in repair_candidates])

    attempts = min(max_attempts, len(repair_candidates))
    current_state_id = parent_state_id
    for attempt_index, repair_candidate in enumerate(repair_candidates[:attempts], start=1):
        repair_before = screenshot_dir / f"{iteration:06d}_repair_{attempt_index}_before.png"
        repair_after = screenshot_dir / f"{iteration:06d}_repair_{attempt_index}_after.png"
        capture_screen(adb_config, repair_before)
        before_hash = perceptual_hash(repair_before)
        before_state = graph.get_or_create_state(before_hash, repair_before)
        current_state_id = str(before_state["state_id"])
        print(
            f"Repair attempt {attempt_index}/{attempts}: strategy={repair_candidate.strategy} "
            f"tap=({repair_candidate.x},{repair_candidate.y}) parent={repair_candidate.parent_candidate_id} reason={repair_candidate.reason!r}"
        )
        tap(adb_config, repair_candidate.x, repair_candidate.y, dry_run=False)
        wait(settle_seconds)
        capture_screen(adb_config, repair_after)
        changed = screen_changed(repair_before, repair_after, threshold=change_threshold)
        after_hash = perceptual_hash(repair_after)
        after_state = graph.get_or_create_state(after_hash, repair_after)
        after_state_id = str(after_state["state_id"])
        after_candidates = filter_candidates(find_candidates(repair_after), safe_mode=True)
        active_layer_after = str(local_screen_analysis(repair_after).get("local_active_layer_hint") or "unknown")
        graph.add_edge(
            from_state=current_state_id,
            to_state=after_state_id,
            action={
                "type": "repair_tap",
                "candidate_id": repair_candidate.id,
                "x": repair_candidate.x,
                "y": repair_candidate.y,
                "repair_strategy": repair_candidate.strategy,
                "parent_candidate_id": repair_candidate.parent_candidate_id,
                "bbox": repair_candidate.bbox,
                "visual_center": repair_candidate.visual_center,
                "tap_point": repair_candidate.tap_point,
            },
            changed=changed,
        )
        graph.save()
        append_record(
            actions_log,
            ActionRecord(
                time=utc_now(),
                screen_hash=before_hash,
                candidate_id=repair_candidate.id,
                x=repair_candidate.x,
                y=repair_candidate.y,
                changed=changed,
                executed=True,
                before=str(repair_before),
                after=str(repair_after),
                label_guess=repair_candidate.reason,
                kind="repair_tap",
                layer="modal",
                bbox=repair_candidate.bbox,
                visual_center=repair_candidate.visual_center,
                tap_point=repair_candidate.tap_point,
                is_repair=True,
                repair_reason=", ".join(evaluation_reasons),
                repair_strategy=repair_candidate.strategy,
                parent_candidate_id=repair_candidate.parent_candidate_id,
                repair_attempt_index=attempt_index,
            ),
        )
        update_learning_memory(
            learning_memory_path,
            action_type="repair_tap",
            candidate={
                "x": repair_candidate.x,
                "y": repair_candidate.y,
                "kind": "repair_tap",
                "layer": "modal",
                "parent": repair_candidate.parent_candidate_id,
                "group": repair_candidate.parent_candidate_id,
                "bbox": repair_candidate.bbox,
            },
            changed=changed,
            active_layer_before="modal",
            active_layer_after=active_layer_after,
            before_state_id=current_state_id,
            after_state_id=after_state_id,
        )
        append_goal_progress(
            goal_progress_path,
            goal=current_goal,
            action_type="repair_tap",
            candidate_id=repair_candidate.id,
            changed=changed,
            before_state_id=current_state_id,
            after_state_id=after_state_id,
            active_layer_before="modal",
            active_layer_after=active_layer_after,
        )
        print(f"Repair result: attempt={attempt_index} to_state={after_state_id} changed={changed}")
        if changed:
            modal = modal_candidate(candidates)
            if modal is not None and modal.bbox is not None:
                save_successful_repair(
                    repair_memory_path,
                    repair_candidate=repair_candidate,
                    original_candidate=original_candidate,
                    state_id=current_state_id,
                    modal_bbox=list(modal.bbox),
                )
            return True
    return False


def infer_active_layer(candidates: list[Candidate]) -> str:
    return "modal" if any(candidate.layer == "modal" for candidate in candidates) else "normal"


def normalize_modal_exhausted_action(
    action: PlannerAction,
    active_layer: str,
    graph: StateGraph,
    state_id: str,
    candidates: list[Candidate],
) -> PlannerAction:
    if active_layer != "modal" or action.type not in {"back", "wait"}:
        return action
    if not modal_candidates_exhausted(graph, state_id, candidates):
        return action
    if action.type == "wait":
        print("Planner selected wait in exhausted modal state; overriding to back.")
    return PlannerAction("back", None, MODAL_EXHAUSTED_REASON, action.response_source, selected_active_layer="modal")


def is_modal_exhausted_back_action(
    action: PlannerAction,
    active_layer: str,
    graph: StateGraph,
    state_id: str,
    candidates: list[Candidate],
) -> bool:
    return (
        active_layer == "modal"
        and action.type == "back"
        and modal_candidates_exhausted(graph, state_id, candidates)
    )


def modal_candidates_exhausted(graph: StateGraph, state_id: str, candidates: list[Candidate]) -> bool:
    modal_candidates = [candidate for candidate in candidates if candidate.layer == "modal"]
    if not modal_candidates:
        return False
    tried = graph.tried_candidate_ids(state_id)
    return all(candidate.id in tried for candidate in modal_candidates)


def load_recent_action_records(actions_log: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    if not actions_log.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in actions_log.read_text(encoding="utf-8").splitlines()[-limit:]:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def create_planner(
    planner_name: str,
    request_path: Path,
    response_path: Path,
    *,
    wait_for_response: bool,
    response_timeout_sec: float,
    clear_response_after_use: bool,
) -> BasePlanner:
    if planner_name == "external":
        return ExternalFilePlanner(
            request_path=request_path,
            response_path=response_path,
            wait_for_response=wait_for_response,
            response_timeout_sec=response_timeout_sec,
            clear_response_after_use=clear_response_after_use,
        )
    return MockPlanner()


def print_action(iteration: int, state_id: str, screen_hash: str, action: PlannerAction, dry_run: bool, candidates: list[Candidate]) -> None:
    if action.type == "tap_candidate":
        print(
            f"Iteration {iteration}: state={state_id} hash={screen_hash} "
            f"planner_action=tap_candidate candidate={action.candidate_id} dry_run={dry_run} "
            f"candidate_detail={format_candidate_detail(action.candidate_id, candidates)} reason={action.reason!r}"
        )
        return
    if action.type == "tap_xy":
        print(
            f"Iteration {iteration}: state={state_id} hash={screen_hash} "
            f"planner_action=tap_xy x={action.x} y={action.y} dry_run={dry_run} "
            f"selected_active_layer={action.selected_active_layer} reason={action.reason!r}"
        )
        return
    if action.type == "swipe":
        print(
            f"Iteration {iteration}: state={state_id} hash={screen_hash} "
            f"planner_action=swipe from=({action.x},{action.y}) to=({action.x2},{action.y2}) dry_run={dry_run} "
            f"selected_active_layer={action.selected_active_layer} reason={action.reason!r}"
        )
        return
    print(f"Iteration {iteration}: state={state_id} hash={screen_hash} planner_action={action.type} dry_run={dry_run} reason={action.reason!r}")


def execute_action(
    adb_config: ADBConfig,
    action: PlannerAction,
    candidate: Candidate | None,
    *,
    settle_seconds: float,
    screen_bounds: tuple[int, int],
) -> None:
    if action.type == "tap_candidate":
        if candidate is None:
            raise ValueError(f"Planner selected unknown candidate: {action.candidate_id}")
        print(
            "ADB tap coordinate check: "
            f"candidate={candidate.id} adb_tap=({candidate.x},{candidate.y}) "
            f"debug_tap_point={candidate.tap_point or (candidate.x, candidate.y)} "
            f"image_coord=original bbox={list(candidate.bbox) if candidate.bbox is not None else None}"
        )
        tap(adb_config, candidate.x, candidate.y, dry_run=False)
        return
    if action.type == "tap_xy":
        if action.x is None or action.y is None or not point_in_bounds(action.x, action.y, screen_bounds):
            raise ValueError(f"Planner selected unsafe tap_xy: x={action.x} y={action.y} bounds={screen_bounds}")
        print(f"ADB tap_xy coordinate check: adb_tap=({action.x},{action.y}) image_coord=original bounds={screen_bounds}")
        tap(adb_config, action.x, action.y, dry_run=False)
        return
    if action.type == "swipe":
        if action.x is None or action.y is None or action.x2 is None or action.y2 is None:
            raise ValueError(f"Planner selected incomplete swipe: {action}")
        if not point_in_bounds(action.x, action.y, screen_bounds) or not point_in_bounds(action.x2, action.y2, screen_bounds):
            raise ValueError(f"Planner selected unsafe swipe: {action} bounds={screen_bounds}")
        print(f"ADB swipe coordinate check: from=({action.x},{action.y}) to=({action.x2},{action.y2}) duration_ms={action.duration_ms or 350} bounds={screen_bounds}")
        swipe(adb_config, action.x, action.y, action.x2, action.y2, action.duration_ms or 350, dry_run=False)
        return
    if action.type == "back":
        back(adb_config, dry_run=False)
        return
    if action.type == "wait":
        return
    raise ValueError(f"Unsupported action type: {action.type}")


def find_candidate_by_id(candidates: list[Candidate], candidate_id: str | None) -> Candidate | None:
    if candidate_id is None:
        return None
    for candidate in candidates:
        if candidate.id == candidate_id:
            return candidate
    return None


def edge_action(action: PlannerAction, candidate: Candidate | None) -> dict[str, object]:
    data = action.to_dict()
    if candidate is not None:
        data.update(
            {
                "x": candidate.x,
                "y": candidate.y,
                "kind": candidate.kind,
                "layer": candidate.layer,
                "score": candidate.score,
                "bbox": list(candidate.bbox) if candidate.bbox is not None else None,
                "visual_center": list(candidate.visual_center or (candidate.x, candidate.y)),
                "tap_point": list(candidate.tap_point or (candidate.x, candidate.y)),
                "label_guess": candidate.label_guess,
                "parent": candidate.parent,
                "group": candidate.group,
            }
        )
    elif action.type == "tap_xy":
        data.update({"x": action.x, "y": action.y, "kind": "tap_xy", "layer": action.selected_active_layer})
    elif action.type == "swipe":
        data.update({"x": action.x, "y": action.y, "x2": action.x2, "y2": action.y2, "duration_ms": action.duration_ms, "kind": "swipe", "layer": action.selected_active_layer})
    return data


def selected_action_x(action: PlannerAction, candidates: list[Candidate]) -> int:
    if action.type == "tap_xy" and action.x is not None:
        return action.x
    if action.type == "swipe" and action.x is not None:
        return action.x
    candidate = find_candidate_by_id(candidates, action.candidate_id)
    return candidate.x if candidate else -1


def selected_action_y(action: PlannerAction, candidates: list[Candidate]) -> int:
    if action.type == "tap_xy" and action.y is not None:
        return action.y
    if action.type == "swipe" and action.y is not None:
        return action.y
    candidate = find_candidate_by_id(candidates, action.candidate_id)
    return candidate.y if candidate else -1


def screen_bounds_from_analysis(local_analysis: dict[str, Any]) -> tuple[int, int]:
    bounds = local_analysis.get("screen_bounds")
    if not isinstance(bounds, dict):
        return (0, 0)
    try:
        return int(bounds.get("width", 0)), int(bounds.get("height", 0))
    except (TypeError, ValueError):
        return (0, 0)


def point_in_bounds(x: int, y: int, bounds: tuple[int, int]) -> bool:
    width, height = bounds
    return width > 0 and height > 0 and 0 <= x < width and 0 <= y < height


def fast_local_analysis(screenshot_path: Path) -> dict[str, Any]:
    try:
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


def create_direct_vision_image(source_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as image:
        image.convert("RGB").save(output_path, format="JPEG", quality=60, optimize=True)
    return output_path


def format_candidate_detail(candidate_id: str | None, candidates: list[Candidate]) -> str:
    candidate = find_candidate_by_id(candidates, candidate_id)
    if candidate is None:
        return "unknown"
    return (
        f"bbox={list(candidate.bbox) if candidate.bbox is not None else None} "
        f"visual_center={candidate.visual_center or (candidate.x, candidate.y)} "
        f"tap_point={candidate.tap_point or (candidate.x, candidate.y)}"
    )


def next_screenshot_index(screenshot_dir: Path) -> int:
    if not screenshot_dir.exists():
        return 1

    highest = 0
    for path in screenshot_dir.glob("*_before.png"):
        try:
            highest = max(highest, int(path.name.split("_", 1)[0]))
        except ValueError:
            continue
    return highest + 1


def load_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return {}

    config: dict[str, Any] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        config[key.strip()] = parse_scalar(value.strip())
    return config


def parse_scalar(value: str) -> Any:
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace("\\\\", "\\")
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


if __name__ == "__main__":
    main()
