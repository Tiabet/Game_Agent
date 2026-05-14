from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

from agent.env import GameEnvironment
from agent.image_utils import save_resized_image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LDPlayer ADB control smoke test")
    parser.add_argument("--adb-path", default=r"C:\LDPlayer\LDPlayer9\adb.exe")
    parser.add_argument("--device", default="emulator-5554")
    parser.add_argument("--screenshot", default="screenshots/latest.png")
    parser.add_argument("--preview", default="screenshots/latest_preview.png")
    parser.add_argument("--preview-scale", type=float, default=1.0)
    parser.add_argument("--history-dir", default="screenshots/history")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--no-preview", action="store_true")
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

    if not args.no_history:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        history_dir = Path(args.history_dir)
        history_dir.mkdir(parents=True, exist_ok=True)
        history_path = history_dir / f"screen_{timestamp}.png"
        shutil.copy2(screenshot_path, history_path)
        print(f"Saved history screenshot: {history_path.resolve()}")

    if not args.no_preview:
        preview_path = save_resized_image(
            screenshot_path,
            args.preview,
            scale=args.preview_scale,
        )
        print(f"Saved preview: {Path(preview_path).resolve()}")

        if not args.no_history:
            preview_history_path = history_dir / f"screen_{timestamp}_preview.png"
            shutil.copy2(preview_path, preview_history_path)
            print(f"Saved history preview: {preview_history_path.resolve()}")

    if args.tap:
        env.tap(args.tap_x, args.tap_y)
        print(f"Sent tap: ({args.tap_x}, {args.tap_y})")
    else:
        print(f"Dry run: tap disabled. Would tap ({args.tap_x}, {args.tap_y}).")


if __name__ == "__main__":
    main()
