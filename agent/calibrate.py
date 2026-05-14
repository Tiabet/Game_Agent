from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from agent.env import GameEnvironment
from agent.target_config import save_target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture a screenshot and generate a coordinate grid overlay")
    parser.add_argument("--adb-path", default=r"C:\LDPlayer\LDPlayer9\adb.exe")
    parser.add_argument("--device", default="emulator-5554")
    parser.add_argument("--screenshot", default="screenshots/calibration.png")
    parser.add_argument("--grid", default="screenshots/calibration_grid.png")
    parser.add_argument("--step", type=int, default=20)
    parser.add_argument("--set-target")
    parser.add_argument("--x", type=int)
    parser.add_argument("--y", type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.set_target:
        if args.x is None or args.y is None:
            raise ValueError("--x and --y are required with --set-target")
        path = save_target(
            args.set_target,
            args.x,
            args.y,
            description=f"confirmed: calibrated target for {args.set_target}",
        )
        print(f"Saved target {args.set_target}: ({args.x}, {args.y}) -> {path}")
        return

    env = GameEnvironment(adb_path=args.adb_path, device=args.device)
    screenshot = env.capture_screenshot(args.screenshot)
    grid = save_grid_overlay(screenshot, args.grid, step=args.step)
    print(f"Saved calibration screenshot: {Path(screenshot).resolve()}")
    print(f"Saved coordinate grid: {Path(grid).resolve()}")


def save_grid_overlay(input_path: str | Path, output_path: str | Path, *, step: int = 20) -> Path:
    source = Path(input_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(source).convert("RGB") as image:
        draw = ImageDraw.Draw(image, "RGBA")
        width, height = image.size
        font = ImageFont.load_default()

        for x in range(0, width + 1, step):
            color = (255, 80, 80, 150) if x % (step * 5) == 0 else (255, 255, 255, 70)
            draw.line((x, 0, x, height), fill=color, width=1)
            if x % (step * 5) == 0:
                draw.text((x + 2, 2), str(x), fill=(255, 80, 80, 230), font=font)

        for y in range(0, height + 1, step):
            color = (80, 180, 255, 150) if y % (step * 5) == 0 else (255, 255, 255, 70)
            draw.line((0, y, width, y), fill=color, width=1)
            if y % (step * 5) == 0:
                draw.text((2, y + 2), str(y), fill=(80, 180, 255, 230), font=font)

        image.save(output)

    return output


if __name__ == "__main__":
    main()
