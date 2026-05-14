from __future__ import annotations

import time

from tools.capture import ADBConfig, run_adb


def tap(config: ADBConfig, x: int, y: int, *, dry_run: bool = True) -> None:
    print(f"Selected action: tap x={x} y={y} dry_run={dry_run}")
    if not dry_run:
        run_adb(config, ["shell", "input", "tap", str(x), str(y)])


def swipe(
    config: ADBConfig,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration_ms: int = 300,
    *,
    dry_run: bool = True,
) -> None:
    print(
        "Selected action: swipe "
        f"x1={x1} y1={y1} x2={x2} y2={y2} duration_ms={duration_ms} dry_run={dry_run}"
    )
    if not dry_run:
        run_adb(
            config,
            ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
        )


def back(config: ADBConfig, *, dry_run: bool = True) -> None:
    print(f"Selected action: back dry_run={dry_run}")
    if not dry_run:
        run_adb(config, ["shell", "input", "keyevent", "BACK"])


def wait(seconds: float) -> None:
    time.sleep(seconds)
