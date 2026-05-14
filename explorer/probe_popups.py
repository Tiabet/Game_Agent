from __future__ import annotations

import argparse
from pathlib import Path

from explorer.memory import ActionRecord, append_record, hash_screen, screen_changed, utc_now
from explorer.safety import is_risky_candidate
from tools.candidates import Candidate, find_candidates
from tools.capture import ADBConfig, capture_screen
from tools.input import back, tap, wait


TOP_POINTS: tuple[tuple[str, int, int], ...] = (
    ("top_left_20_32", 20, 32),
    ("top_left_45_32", 45, 32),
    ("top_left_70_32", 70, 32),
    ("top_left_100_32", 100, 32),
    ("top_left_130_32", 130, 32),
    ("top_right_330_32", 330, 32),
    ("top_right_350_32", 350, 32),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe top lobby buttons and one level of popup buttons.")
    parser.add_argument("--adb-path", default=r"C:\LDPlayer\LDPlayer9\adb.exe")
    parser.add_argument("--device-id", default="emulator-5554")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--settle", type=float, default=1.2)
    parser.add_argument("--inner-limit", type=int, default=8)
    parser.add_argument("--unsafe", action="store_true", help="Allow risky popup buttons. Do not use for live game exploration.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = ADBConfig(args.adb_path, args.device_id, args.timeout)
    screenshot_dir = Path("runtime/screenshots")
    log_path = Path("runtime/actions.jsonl")
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    for index, (name, x, y) in enumerate(TOP_POINTS, 1):
        before = screenshot_dir / f"popup_probe_{index:02d}_open_before.png"
        opened = screenshot_dir / f"popup_probe_{index:02d}_open_after.png"
        capture_screen(config, before)
        screen_hash = hash_screen(before)

        tap(config, x, y, dry_run=False)
        wait(args.settle)
        capture_screen(config, opened)
        changed = screen_changed(before, opened)
        print(f"open {name} x={x} y={y} changed={changed} hash={screen_hash[:12]}")
        append_record(
            log_path,
            ActionRecord(utc_now(), screen_hash, name, x, y, changed, True, str(before), str(opened), "top popup opener"),
        )
        if not changed:
            continue

        probe_inner_candidates(config, log_path, screenshot_dir, opened, index, args.inner_limit, args.settle, not args.unsafe)
        back(config, dry_run=False)
        wait(args.settle)


def probe_inner_candidates(
    config: ADBConfig,
    log_path: Path,
    screenshot_dir: Path,
    opened: Path,
    popup_index: int,
    limit: int,
    settle: float,
    safe_mode: bool,
) -> None:
    candidates = rank_popup_candidates(find_candidates(opened), safe_mode=safe_mode)[:limit]
    for inner_index, candidate in enumerate(candidates, 1):
        before = screenshot_dir / f"popup_probe_{popup_index:02d}_{inner_index:02d}_before.png"
        after = screenshot_dir / f"popup_probe_{popup_index:02d}_{inner_index:02d}_after.png"
        capture_screen(config, before)
        screen_hash = hash_screen(before)

        tap(config, candidate.x, candidate.y, dry_run=False)
        wait(settle)
        capture_screen(config, after)
        changed = screen_changed(before, after)
        print(
            f"inner popup={popup_index:02d} candidate={candidate.id} "
            f"x={candidate.x} y={candidate.y} changed={changed}"
        )
        append_record(
            log_path,
            ActionRecord(
                utc_now(),
                screen_hash,
                f"popup_{popup_index:02d}_{candidate.id}",
                candidate.x,
                candidate.y,
                changed,
                True,
                str(before),
                str(after),
                candidate.label_guess,
            ),
        )
        if changed:
            back(config, dry_run=False)
            wait(settle)


def rank_popup_candidates(candidates: list[Candidate], *, safe_mode: bool) -> list[Candidate]:
    def score(candidate: Candidate) -> tuple[int, int]:
        visual = 0 if candidate.id.startswith("visual_") else 1
        central = abs(candidate.x - 180) + abs(candidate.y - 360)
        return visual, central

    filtered = [candidate for candidate in candidates if 60 <= candidate.y <= 610]
    if safe_mode:
        filtered = [candidate for candidate in filtered if not is_risky_candidate(candidate)]
    return sorted(filtered, key=score)


if __name__ == "__main__":
    main()
