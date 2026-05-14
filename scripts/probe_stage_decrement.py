from __future__ import annotations

import subprocess
import time
from pathlib import Path

from PIL import Image, ImageChops


ADB = r"C:\LDPlayer\LDPlayer9\adb.exe"
DEVICE = "emulator-5554"
SHOT = Path("screenshots/probe_stage.png")
ROI = (130, 250, 235, 292)


CANDIDATES = [
    (60, 410),
    (70, 410),
    (80, 410),
    (90, 410),
    (100, 410),
    (110, 410),
    (120, 410),
    (130, 410),
    (60, 430),
    (70, 430),
    (80, 430),
    (90, 430),
    (100, 430),
    (110, 430),
    (120, 430),
    (130, 430),
    (45, 430),
    (35, 430),
]


def main() -> None:
    before = capture()
    before_roi = before.crop(ROI)
    for x, y in CANDIDATES:
        tap(x, y)
        time.sleep(0.35)
        after = capture()
        diff = image_diff(before_roi, after.crop(ROI))
        print(f"candidate=({x},{y}) roi_diff={diff}")
        if diff > 1000:
            print(f"changed_candidate=({x},{y})")
            return
    print("no candidate changed stage ROI")


def capture() -> Image.Image:
    SHOT.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([ADB, "-s", DEVICE, "exec-out", "screencap", "-p"], check=True, capture_output=True)
    SHOT.write_bytes(result.stdout)
    return Image.open(SHOT).convert("RGB")


def tap(x: int, y: int) -> None:
    subprocess.run([ADB, "-s", DEVICE, "shell", "input", "tap", str(x), str(y)], check=True)


def image_diff(before: Image.Image, after: Image.Image) -> int:
    diff = ImageChops.difference(before, after)
    return sum(sum(pixel) for pixel in diff.getdata())


if __name__ == "__main__":
    main()
