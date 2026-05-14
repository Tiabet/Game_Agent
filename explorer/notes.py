from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ScreenNote:
    time: str
    screen_hash: str
    screenshot: str
    source_action: str
    source_x: int | None
    source_y: int | None
    screen_kind: str
    meaning_guess: str
    safe_buttons: list[dict[str, object]]
    risky_buttons: list[dict[str, object]]
    observations: list[str]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_note(log_path: str | Path, note: ScreenNote) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(asdict(note), ensure_ascii=False) + "\n")
