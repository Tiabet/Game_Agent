from __future__ import annotations

import argparse
from pathlib import Path

from explorer.memory import hash_screen
from explorer.notes import ScreenNote, append_note, utc_now
from explorer.safety import is_risky_candidate
from tools.candidates import Candidate, find_candidates
from tools.capture import ADBConfig, capture_screen
from tools.input import tap, wait


SAFE_LOBBY_ACTIONS: tuple[tuple[str, int, int, str], ...] = (
    ("bottom_nav_1", 43, 614, "하단 탭 1: 전투/메인 컨텐츠 후보"),
    ("bottom_nav_2", 108, 614, "하단 탭 2: 영웅/용병/성장 후보"),
    ("bottom_nav_3", 180, 614, "하단 탭 3: 로비/중앙 메뉴 후보"),
    ("bottom_nav_4", 252, 614, "하단 탭 4: 퀘스트/보상/컨텐츠 후보"),
    ("bottom_center", 180, 576, "현재 화면의 주요 안전 액션 후보"),
    ("visual_239_372", 239, 372, "화면 내부에서 발견된 중간 우측 버튼 후보"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture semantic notes for safe lobby navigation.")
    parser.add_argument("--adb-path", default=r"C:\LDPlayer\LDPlayer9\adb.exe")
    parser.add_argument("--device-id", default="emulator-5554")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--settle", type=float, default=1.4)
    parser.add_argument("--notes", default="runtime/screen_notes.jsonl")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = ADBConfig(args.adb_path, args.device_id, args.timeout)
    screenshot_dir = Path("runtime/screenshots/semantic")
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    for action_id, x, y, action_meaning in SAFE_LOBBY_ACTIONS:
        tap(config, x, y, dry_run=False)
        wait(args.settle)

        screenshot = screenshot_dir / f"{action_id}.png"
        capture_screen(config, screenshot)
        candidates = find_candidates(screenshot)
        screen_hash = hash_screen(screenshot)
        safe, risky = classify_candidates(candidates)

        note = ScreenNote(
            time=utc_now(),
            screen_hash=screen_hash,
            screenshot=str(screenshot),
            source_action=action_id,
            source_x=x,
            source_y=y,
            screen_kind=guess_screen_kind(action_id, screen_hash),
            meaning_guess=action_meaning,
            safe_buttons=safe,
            risky_buttons=risky,
            observations=build_observations(action_id, safe, risky),
        )
        append_note(args.notes, note)
        print(f"noted {action_id} hash={screen_hash[:12]} safe={len(safe)} risky={len(risky)} screenshot={screenshot}")


def classify_candidates(candidates: list[Candidate]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    safe: list[dict[str, object]] = []
    risky: list[dict[str, object]] = []
    for candidate in candidates:
        row = {
            "id": candidate.id,
            "x": candidate.x,
            "y": candidate.y,
            "label_guess": candidate.label_guess,
        }
        if is_risky_candidate(candidate):
            risky.append(row)
        else:
            safe.append(row)
    return safe[:16], risky[:16]


def guess_screen_kind(action_id: str, screen_hash: str) -> str:
    if action_id.startswith("bottom_nav_"):
        return "lobby_tab"
    if action_id == "bottom_center":
        return "primary_action_result"
    if action_id.startswith("visual_"):
        return "detected_inner_button_result"
    return "unknown"


def build_observations(action_id: str, safe: list[dict[str, object]], risky: list[dict[str, object]]) -> list[str]:
    observations = [
        "Unity 접근성 트리에는 텍스트가 노출되지 않아 이미지/좌표 기반 추정만 가능",
        "로그아웃, 종료, 결제 가능성이 있는 상단/확인성 후보는 risky로 분류",
    ]
    if action_id.startswith("bottom_nav_"):
        observations.append("하단 탭 전환으로 로비의 주요 섹션을 식별하는 중")
    if safe:
        observations.append(f"다음 안전 탐색 후보 예: {safe[0]['id']} @ ({safe[0]['x']},{safe[0]['y']})")
    if risky:
        observations.append(f"건너뛴 위험 후보 예: {risky[0]['id']} @ ({risky[0]['x']},{risky[0]['y']})")
    return observations


if __name__ == "__main__":
    main()
