from __future__ import annotations

import argparse

from agent.env import GameEnvironment
from agent.stage import clicks_to_target, read_current_stage
from agent.target_config import load_target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Navigate to a target stage and enter only after image verification")
    parser.add_argument("--target-stage", type=int, default=249)
    parser.add_argument("--adb-path", default=r"C:\LDPlayer\LDPlayer9\adb.exe")
    parser.add_argument("--device", default="emulator-5554")
    parser.add_argument("--tap-interval", type=float, default=0.08)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    env = GameEnvironment(adb_path=args.adb_path, device=args.device)
    navigate_to_stage_and_enter(env, args.target_stage, args.tap_interval, execute=args.execute)


def navigate_to_stage_and_enter(
    env: GameEnvironment,
    target_stage: int,
    tap_interval: float = 0.08,
    *,
    execute: bool = False,
) -> None:
    left_arrow = _confirmed_target("stage_left_arrow")
    enter_button = _confirmed_target("stage_enter_button")

    before = read_current_stage(env)
    if before.stage is None:
        raise RuntimeError(f"Cannot read current stage before navigation: {before.reason} roi={before.roi_path}")

    clicks = clicks_to_target(before.stage, target_stage)
    print(f"current_stage={before.stage} target_stage={target_stage} clicks={clicks}")

    if not execute:
        print("Dry run: navigation was not sent to LDPlayer.")
        return

    for index in range(clicks):
        env.tap(left_arrow.x, left_arrow.y)
        env.wait(tap_interval)
        print(f"left_arrow_tap={index + 1}/{clicks}")

    env.wait(0.5)

    after = read_current_stage(env)
    print(f"after_stage={after.stage} confidence={after.confidence:.3f} reason={after.reason}")
    if after.stage != target_stage:
        raise RuntimeError(
            f"Refusing to enter: expected stage {target_stage}, detected {after.stage}. roi={after.roi_path}"
        )

    env.tap(enter_button.x, enter_button.y)
    env.wait(1.0)
    print(f"entered_stage={target_stage}")


def _confirmed_target(name: str):
    target = load_target(name)
    if not target.description.startswith("confirmed:"):
        raise RuntimeError(f"Target is not confirmed: {name}")
    return target


if __name__ == "__main__":
    main()
