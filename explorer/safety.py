from __future__ import annotations

from tools.candidates import Candidate


RISKY_ID_PARTS = (
    "top_left",
    "top_right",
    "close_top",
    "top_center",
    "popup_confirm",
    "dialog_center",
    "right_button",
    "bottom_nav_5",
)


def is_risky_candidate(candidate: Candidate) -> bool:
    """Skip candidates likely to hit logout, exit, settings, or purchase flows."""
    if candidate.kind == "popup_button":
        safe_words = ("cancel", "close", "right", "no", "back", "x")
        text = f"{candidate.id} {candidate.label_guess}".lower()
        if any(word in text for word in safe_words):
            return False

    if any(part in candidate.id for part in RISKY_ID_PARTS):
        return True

    # Top bar buttons commonly contain profile/settings/shop/exit affordances.
    if candidate.y <= 80 and (candidate.x <= 150 or candidate.x >= 230):
        return True

    # Popup bottom-right confirmation buttons are unsafe without text/OCR.
    if candidate.x >= 220 and 420 <= candidate.y <= 610:
        return True

    return False
