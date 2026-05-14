from __future__ import annotations

from pathlib import Path


def find_template_center(
    screenshot_path: str | Path,
    template_path: str | Path,
    *,
    threshold: float = 0.8,
) -> tuple[int, int] | None:
    import cv2

    screenshot = cv2.imread(str(screenshot_path), cv2.IMREAD_COLOR)
    template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)

    if screenshot is None:
        raise FileNotFoundError(f"Screenshot not readable: {screenshot_path}")
    if template is None:
        raise FileNotFoundError(f"Template not readable: {template_path}")

    result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
    _, max_value, _, max_location = cv2.minMaxLoc(result)

    if max_value < threshold:
        return None

    height, width = template.shape[:2]
    return max_location[0] + width // 2, max_location[1] + height // 2
