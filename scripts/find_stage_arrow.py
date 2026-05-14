from __future__ import annotations

import subprocess
import time
from pathlib import Path

from PIL import Image, ImageChops


ADB = r"C:\LDPlayer\LDPlayer9\adb.exe"
DEVICE = "emulator-5554"
OUT = Path("screenshots/arrow_probe.png")
SELECTOR_BOX = (40, 300, 320, 470)


CANDIDATES = [
    (130, 335),
    (225, 335),
    (150, 395),
    (210, 395),
    (72, 410),
    (288, 410),
    (72, 430),
    (288, 430),
    (96, 430),
    (264, 430),
]


def main() -> None:
    before = capture()
    for x, y in CANDIDATES:
        tap(x, y)
        time.sleep(0.25)
        after = capture()
        diff = selector_diff(before, after)
        print(f"candidate ({x}, {y}) diff={diff}")
        if diff > 5000:
            print(f"changed: ({x}, {y})")
            return
        before = after

    print("no candidate changed selector")


def capture() -> Image.Image:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [ADB, "-s", DEVICE, "exec-out", "screencap", "-p"],
        check=True,
        capture_output=True,
    )
    OUT.write_bytes(result.stdout)
    return Image.open(OUT).convert("RGB")


def tap(x: int, y: int) -> None:
    subprocess.run([ADB, "-s", DEVICE, "shell", "input", "tap", str(x), str(y)], check=True)


def selector_diff(before: Image.Image, after: Image.Image) -> int:
    diff = ImageChops.difference(before.crop(SELECTOR_BOX), after.crop(SELECTOR_BOX))
    return sum(sum(pixel) for pixel in diff.getdata())


if __name__ == "__main__":
    main()
