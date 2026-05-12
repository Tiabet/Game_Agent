from __future__ import annotations

import argparse
from pathlib import Path

from agent.env import GameEnvironment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LDPlayer ADB control smoke test")
    parser.add_argument("--adb-path", default=r"C:\LDPlayer\LDPlayer9\adb.exe")
    parser.add_argument("--device", default="emulator-5554")
    parser.add_argument("--screenshot", default="screenshots/latest.png")
    parser.add_argument("--tap-x", type=int, default=500)
    parser.add_argument("--tap-y", type=int, default=500)
    parser.add_argument(
        "--tap",
        action="store_true",
        help="Actually sends a tap input. Disabled by default for dry-run safety.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    env = GameEnvironment(adb_path=args.adb_path, device=args.device)

    screenshot_path = env.capture_screenshot(args.screenshot)
    print(f"Saved screenshot: {Path(screenshot_path).resolve()}")

    if args.tap:
        env.tap(args.tap_x, args.tap_y)
        print(f"Sent tap: ({args.tap_x}, {args.tap_y})")
    else:
        print(f"Dry run: tap disabled. Would tap ({args.tap_x}, {args.tap_y}).")


if __name__ == "__main__":
    main()
