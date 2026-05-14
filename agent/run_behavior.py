from __future__ import annotations

import argparse

from agent.behaviors import AVAILABLE_BEHAVIORS, create_enter_sewer_behavior, execute_behavior, get_behavior
from agent.env import GameEnvironment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a named game behavior")
    parser.add_argument("behavior", choices=AVAILABLE_BEHAVIORS)
    parser.add_argument("--adb-path", default=r"C:\LDPlayer\LDPlayer9\adb.exe")
    parser.add_argument("--device", default="emulator-5554")
    parser.add_argument("--x", type=int, help="Override target x coordinate")
    parser.add_argument("--y", type=int, help="Override target y coordinate")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually sends inputs to LDPlayer. Without this, dry-run is used.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    env = GameEnvironment(adb_path=args.adb_path, device=args.device)

    if args.behavior == "enter_sewer" and (args.x is not None or args.y is not None):
        if args.x is None or args.y is None:
            raise ValueError("Both --x and --y are required when overriding behavior coordinates.")
        behavior = create_enter_sewer_behavior(x=args.x, y=args.y)
    else:
        behavior = get_behavior(args.behavior)

    execute_behavior(env, behavior, dry_run=not args.execute)


if __name__ == "__main__":
    main()
