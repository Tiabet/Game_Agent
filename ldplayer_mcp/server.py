from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mcp.server.fastmcp import FastMCP
from PIL import Image

from agent.behaviors import execute_behavior, get_behavior
from agent.env import GameEnvironment
from agent.image_utils import save_resized_image
from agent.stage import clicks_to_target, read_current_stage, save_stage_template
from agent.stage_goal import navigate_to_stage_and_enter
from agent.target_config import save_target


RUNTIME_DIR = REPO_ROOT / "runtime"
ADB_PATH = r"C:\LDPlayer\LDPlayer9\adb.exe"
DEVICE = "emulator-5554"

mcp = FastMCP("ldplayer")


def _env() -> GameEnvironment:
    return GameEnvironment(adb_path=ADB_PATH, device=DEVICE)


@mcp.tool()
def observe_screen(preview_scale: float = 0.5, include_base64: bool = True) -> dict[str, Any]:
    """Capture the current LDPlayer screen and return file paths plus image metadata."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    screenshot_path = RUNTIME_DIR / "current_screen.png"
    preview_path = RUNTIME_DIR / "current_screen_preview.png"

    _env().capture_screenshot(screenshot_path)
    save_resized_image(screenshot_path, preview_path, scale=preview_scale)

    with Image.open(screenshot_path) as screenshot:
        screenshot_size = screenshot.size
    with Image.open(preview_path) as preview:
        preview_size = preview.size

    result: dict[str, Any] = {
        "screenshot_path": str(screenshot_path),
        "preview_path": str(preview_path),
        "screen_width": screenshot_size[0],
        "screen_height": screenshot_size[1],
        "preview_width": preview_size[0],
        "preview_height": preview_size[1],
        "coordinate_system": "Use original screenshot coordinates. Top-left is (0,0).",
    }

    if include_base64:
        result["preview_base64_png"] = base64.b64encode(preview_path.read_bytes()).decode("ascii")

    return result


@mcp.tool()
def tap(x: int, y: int) -> dict[str, Any]:
    """Tap an LDPlayer screen coordinate using ADB input."""
    _env().tap(x, y)
    return {"ok": True, "action": "tap", "x": x, "y": y}


@mcp.tool()
def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> dict[str, Any]:
    """Swipe between two LDPlayer screen coordinates using ADB input."""
    _env().swipe(x1, y1, x2, y2, duration_ms)
    return {
        "ok": True,
        "action": "swipe",
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "duration_ms": duration_ms,
    }


@mcp.tool()
def back() -> dict[str, Any]:
    """Send Android back keyevent to LDPlayer."""
    _env().back()
    return {"ok": True, "action": "back"}


@mcp.tool()
def wait(seconds: float = 1.0) -> dict[str, Any]:
    """Wait for a number of seconds."""
    _env().wait(seconds)
    return {"ok": True, "action": "wait", "seconds": seconds}


@mcp.tool()
def run_behavior(name: str) -> dict[str, Any]:
    """Run a named high-level game behavior, such as enter_sewer."""
    behavior = get_behavior(name)
    execute_behavior(_env(), behavior, dry_run=False)
    return {"ok": True, "action": "run_behavior", "name": name}


@mcp.tool()
def set_target(name: str, x: int, y: int, description: str = "") -> dict[str, Any]:
    """Save a confirmed target coordinate used by a high-level behavior."""
    saved_path = save_target(
        name,
        x,
        y,
        description=f"confirmed: {description or f'calibrated target for {name}'}",
        path=REPO_ROOT / "config" / "targets.json",
    )
    return {"ok": True, "target": name, "x": x, "y": y, "path": str(saved_path)}


@mcp.tool()
def read_stage_number(target_stage: int = 249) -> dict[str, Any]:
    """Read the current stage number from the screen using saved image templates."""
    result = read_current_stage(_env())
    response: dict[str, Any] = {
        "stage": result.stage,
        "confidence": result.confidence,
        "roi_path": str(result.roi_path),
        "reason": result.reason,
    }
    if result.stage is not None:
        response["clicks_to_target"] = clicks_to_target(result.stage, target_stage)
    return response


@mcp.tool()
def save_stage_number_template(stage: int) -> dict[str, Any]:
    """Save the current stage number ROI as a template for future stage recognition."""
    result = read_current_stage(_env(), min_confidence=2.0)
    path = save_stage_template(stage, result.roi_path)
    return {"ok": True, "stage": stage, "template_path": str(path), "roi_path": str(result.roi_path)}


@mcp.tool()
def navigate_to_stage_and_enter_tool(target_stage: int = 249) -> dict[str, Any]:
    """Read current stage, navigate to target stage, verify target stage, then enter."""
    navigate_to_stage_and_enter(_env(), target_stage, execute=True)
    return {"ok": True, "target_stage": target_stage}


@mcp.tool()
def list_behaviors() -> dict[str, Any]:
    """List available high-level behaviors."""
    return {"behaviors": ["enter_sewer", "go_to_stage_249_and_enter"]}


if __name__ == "__main__":
    mcp.run()
