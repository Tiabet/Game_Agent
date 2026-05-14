from __future__ import annotations

import argparse
from pathlib import Path

from explorer.memory import hash_screen, screen_changed, similar_screen_hash
from explorer.notes import ScreenNote, append_note, utc_now
from explorer.safety import is_risky_candidate
from tools.candidates import Candidate, find_candidates
from tools.capture import ADBConfig, capture_screen
from tools.input import back, tap, wait


TAB_ACTIONS: tuple[tuple[str, int, int, str], ...] = (
    ("bottom_nav_1", 43, 614, "전투/메인 컨텐츠 후보 탭"),
    ("bottom_nav_2", 108, 614, "영웅/용병/성장 후보 탭"),
    ("bottom_nav_3", 180, 614, "로비/중앙 메뉴 후보 탭"),
    ("bottom_nav_4", 252, 614, "퀘스트/보상/컨텐츠 후보 탭"),
)

SKIP_INNER_IDS = {
    "bottom_nav_1",
    "bottom_nav_2",
    "bottom_nav_3",
    "bottom_nav_4",
    "bottom_nav_5",
    "bottom_right",
    "top_left",
    "top_right",
    "close_top_left",
    "close_top_right",
    "top_center",
    "popup_confirm",
    "right_button",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe one safe semantic level inside each lobby tab.")
    parser.add_argument("--adb-path", default=r"C:\LDPlayer\LDPlayer9\adb.exe")
    parser.add_argument("--device-id", default="emulator-5554")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--settle", type=float, default=1.4)
    parser.add_argument("--inner-limit", type=int, default=6)
    parser.add_argument("--notes", default="runtime/screen_notes.jsonl")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = ADBConfig(args.adb_path, args.device_id, args.timeout)
    screenshot_dir = Path("runtime/screenshots/semantic_expand")
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    for tab_id, tab_x, tab_y, tab_meaning in TAB_ACTIONS:
        tap(config, tab_x, tab_y, dry_run=False)
        wait(args.settle)
        base_path = screenshot_dir / f"{tab_id}_base.png"
        capture_screen(config, base_path)
        base_hash = hash_screen(base_path)

        candidates = rank_inner_candidates(find_candidates(base_path))[: args.inner_limit]
        write_note(
            args.notes,
            base_hash,
            base_path,
            tab_id,
            tab_x,
            tab_y,
            "lobby_tab_base",
            tab_meaning,
            candidates,
            ["탭 진입 직후 기준 화면", "이 화면에서 안전한 내부 후보만 확장 탐색"],
        )

        for index, candidate in enumerate(candidates, 1):
            before_path = screenshot_dir / f"{tab_id}_{index:02d}_{candidate.id}_before.png"
            after_path = screenshot_dir / f"{tab_id}_{index:02d}_{candidate.id}_after.png"
            capture_screen(config, before_path)
            before_hash = hash_screen(before_path)

            tap(config, candidate.x, candidate.y, dry_run=False)
            wait(args.settle)
            capture_screen(config, after_path)
            after_hash = hash_screen(after_path)
            changed = screen_changed(before_path, after_path) and not similar_screen_hash(before_hash, after_hash)
            print(
                f"{tab_id} -> {candidate.id} ({candidate.x},{candidate.y}) "
                f"changed={changed} before={before_hash[:12]} after={after_hash[:12]}"
            )

            after_candidates = rank_inner_candidates(find_candidates(after_path))[:8]
            write_note(
                args.notes,
                after_hash,
                after_path,
                f"{tab_id}/{candidate.id}",
                candidate.x,
                candidate.y,
                "inner_probe_result",
                f"{tab_meaning} 내부 후보 {candidate.id} 결과",
                after_candidates,
                [
                    f"부모 탭: {tab_id}",
                    f"클릭 후보: {candidate.id} @ ({candidate.x},{candidate.y})",
                    f"화면 전환 감지: {changed}",
                    "위험 후보는 클릭하지 않고 목록에만 남김",
                ],
            )

            if changed:
                back(config, dry_run=False)
                wait(args.settle)
                tap(config, tab_x, tab_y, dry_run=False)
                wait(args.settle)


def rank_inner_candidates(candidates: list[Candidate]) -> list[Candidate]:
    filtered = [candidate for candidate in candidates if is_safe_inner(candidate)]

    def score(candidate: Candidate) -> tuple[int, int]:
        visual_rank = 0 if candidate.id.startswith("visual_") else 1
        # Favor content area over global navigation.
        center_distance = abs(candidate.x - 180) + abs(candidate.y - 350)
        return visual_rank, center_distance

    return sorted(filtered, key=score)


def is_safe_inner(candidate: Candidate) -> bool:
    if candidate.id in SKIP_INNER_IDS:
        return False
    if is_risky_candidate(candidate):
        return False
    if candidate.y < 90 or candidate.y > 590:
        return False
    return True


def write_note(
    notes_path: str,
    screen_hash: str,
    screenshot: Path,
    source_action: str,
    source_x: int | None,
    source_y: int | None,
    screen_kind: str,
    meaning_guess: str,
    candidates: list[Candidate],
    observations: list[str],
) -> None:
    safe_buttons: list[dict[str, object]] = []
    risky_buttons: list[dict[str, object]] = []
    for candidate in candidates:
        row = {"id": candidate.id, "x": candidate.x, "y": candidate.y, "label_guess": candidate.label_guess}
        if is_risky_candidate(candidate):
            risky_buttons.append(row)
        else:
            safe_buttons.append(row)

    append_note(
        notes_path,
        ScreenNote(
            time=utc_now(),
            screen_hash=screen_hash,
            screenshot=str(screenshot),
            source_action=source_action,
            source_x=source_x,
            source_y=source_y,
            screen_kind=screen_kind,
            meaning_guess=meaning_guess,
            safe_buttons=safe_buttons,
            risky_buttons=risky_buttons,
            observations=observations,
        ),
    )


if __name__ == "__main__":
    main()
