from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tools.candidates import Candidate


@dataclass(frozen=True)
class RepairCandidate:
    id: str
    x: int
    y: int
    strategy: str
    reason: str
    parent_candidate_id: str | None
    bbox: list[int] | None = None
    visual_center: list[int] | None = None
    tap_point: list[int] | None = None


def generate_repair_candidates(
    *,
    original_candidate: Candidate | None,
    candidates: list[Candidate],
    repair_memory: list[dict[str, Any]],
    max_candidates: int = 24,
) -> list[RepairCandidate]:
    modal = modal_candidate(candidates)
    if modal is None or modal.bbox is None:
        return []

    modal_bbox = list(modal.bbox)
    generated: list[RepairCandidate] = []
    generated.extend(memory_repair_candidates(repair_memory, modal_bbox, original_candidate))
    generated.extend(button_bbox_grid_candidates(candidates, original_candidate))
    generated.extend(modal_close_candidates(modal_bbox, original_candidate))
    generated.extend(modal_button_row_grid(modal_bbox, original_candidate))
    generated.extend(original_offset_candidates(original_candidate, modal_bbox))
    return dedupe_repair_candidates(generated)[:max_candidates]


def modal_candidate(candidates: list[Candidate]) -> Candidate | None:
    for candidate in candidates:
        if candidate.layer == "modal" and candidate.kind in {"modal", "popup"} and candidate.bbox is not None:
            return candidate
    for candidate in candidates:
        if candidate.layer == "modal" and candidate.bbox is not None:
            return candidate
    return None


def memory_repair_candidates(memory: list[dict[str, Any]], modal_bbox: list[int], original_candidate: Candidate | None) -> list[RepairCandidate]:
    candidates: list[RepairCandidate] = []
    for index, record in enumerate(reversed(memory[-20:])):
        if record.get("context") != "modal":
            continue
        if original_candidate is not None and record.get("original_candidate_kind") != original_candidate.kind:
            continue
        relative = record.get("successful_relative_position")
        if not valid_pair(relative):
            continue
        x, y = point_from_relative(modal_bbox, float(relative[0]), float(relative[1]))
        candidates.append(
            RepairCandidate(
                id=f"repair_memory_{index}",
                x=x,
                y=y,
                strategy="memory_relative_position",
                reason="Reuse successful modal-relative repair tap point.",
                parent_candidate_id=original_candidate.id if original_candidate else None,
                bbox=modal_bbox,
                visual_center=[x, y],
                tap_point=[x, y],
            )
        )
    return candidates


def original_offset_candidates(original_candidate: Candidate | None, modal_bbox: list[int]) -> list[RepairCandidate]:
    if original_candidate is None:
        return []
    offsets = [(0, -18), (0, 18), (-18, 0), (18, 0), (-14, -14), (14, -14), (-14, 14), (14, 14)]
    candidates: list[RepairCandidate] = []
    for dx, dy in offsets:
        x = original_candidate.x + dx
        y = original_candidate.y + dy
        if not point_in_bbox(x, y, modal_bbox):
            continue
        candidates.append(
            RepairCandidate(
                id=f"repair_offset_{dx}_{dy}",
                x=x,
                y=y,
                strategy="original_tap_offset",
                reason=f"Try offset ({dx},{dy}) around failed tap point.",
                parent_candidate_id=original_candidate.id,
                bbox=list(original_candidate.bbox) if original_candidate.bbox else None,
                visual_center=list(original_candidate.visual_center or (original_candidate.x, original_candidate.y)),
                tap_point=[x, y],
            )
        )
    return candidates


def button_bbox_grid_candidates(candidates: list[Candidate], original_candidate: Candidate | None) -> list[RepairCandidate]:
    generated: list[RepairCandidate] = []
    button_candidates = sorted(
        [candidate for candidate in candidates if candidate.layer == "modal" and candidate.kind == "popup_button" and candidate.bbox is not None],
        key=lambda candidate: modal_button_priority(candidate, original_candidate),
    )
    sample_points = (
        (0.50, 0.50),
        (0.35, 0.50),
        (0.65, 0.50),
        (0.50, 0.35),
        (0.50, 0.65),
        (0.25, 0.25),
        (0.75, 0.25),
        (0.25, 0.75),
        (0.75, 0.75),
    )
    for candidate in button_candidates:
        x, y, width, height = candidate.bbox
        for ix, iy in sample_points:
            tap_x = round(x + width * ix)
            tap_y = round(y + height * iy)
            generated.append(
                RepairCandidate(
                    id=f"repair_grid_{candidate.id}_{ix:.2f}_{iy:.2f}",
                    x=tap_x,
                    y=tap_y,
                    strategy="popup_button_bbox_grid",
                    reason="Sample point inside detected popup_button bbox.",
                    parent_candidate_id=original_candidate.id if original_candidate else candidate.id,
                    bbox=list(candidate.bbox),
                    visual_center=list(candidate.visual_center or (candidate.x, candidate.y)),
                    tap_point=[tap_x, tap_y],
                )
            )
    return generated


def modal_button_row_grid(modal_bbox: list[int], original_candidate: Candidate | None) -> list[RepairCandidate]:
    points = []
    for rx in (0.55, 0.62, 0.70, 0.78, 0.86):
        for ry in (0.16, 0.23, 0.30, 0.37):
            points.append((rx, ry))
    for rx in (0.62, 0.70, 0.78, 0.86):
        for ry in (0.62, 0.70, 0.78):
            points.append((rx, ry))
    generated: list[RepairCandidate] = []
    for rx, ry in points:
        x, y = point_from_relative(modal_bbox, rx, ry)
        strategy = "modal_upper_right_button_row_grid" if ry < 0.50 else "modal_bottom_right_grid"
        reason = "Probe modal upper/right cancel/no button area." if ry < 0.50 else "Probe modal bottom-right cancel/no button area."
        generated.append(
            RepairCandidate(
                id=f"repair_modal_row_{rx:.2f}_{ry:.2f}",
                x=x,
                y=y,
                strategy=strategy,
                reason=reason,
                parent_candidate_id=original_candidate.id if original_candidate else None,
                bbox=modal_bbox,
                visual_center=[x, y],
                tap_point=[x, y],
            )
        )
    return generated


def modal_close_candidates(modal_bbox: list[int], original_candidate: Candidate | None) -> list[RepairCandidate]:
    points = [(0.82, 0.18), (0.90, 0.18), (0.94, 0.12)]
    candidates: list[RepairCandidate] = []
    for rx, ry in points:
        x, y = point_from_relative(modal_bbox, rx, ry)
        candidates.append(
            RepairCandidate(
                id=f"repair_modal_close_{rx:.2f}_{ry:.2f}",
                x=x,
                y=y,
                strategy="modal_close_area",
                reason="Probe modal close/X area.",
                parent_candidate_id=original_candidate.id if original_candidate else None,
                bbox=modal_bbox,
                visual_center=[x, y],
                tap_point=[x, y],
            )
        )
    return candidates


def modal_button_priority(candidate: Candidate, original_candidate: Candidate | None) -> tuple[int, int, int, int, float]:
    text = f"{candidate.id} {candidate.label_guess}".lower()
    danger = "confirm" in text or "exit" in text or candidate.id in {"left_mid_lower", "popup_confirm"}
    safe = any(word in text for word in ("cancel", "close", "right", "no", "back", "x"))
    close = "close" in text or text.endswith(" x")
    original_safe_match = original_candidate is not None and candidate.id == original_candidate.id and not danger
    return (
        0 if original_safe_match else 1,
        0 if safe and not close else 1,
        0 if close else 1,
        1 if danger else 0,
        -candidate.score,
    )


def load_repair_memory(path: str | Path) -> list[dict[str, Any]]:
    memory_path = Path(path)
    if not memory_path.exists():
        return []
    try:
        raw = json.loads(memory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return raw if isinstance(raw, list) else []


def save_successful_repair(
    path: str | Path,
    *,
    repair_candidate: RepairCandidate,
    original_candidate: Candidate | None,
    state_id: str,
    modal_bbox: list[int],
) -> None:
    memory = load_repair_memory(path)
    rx, ry = relative_position(modal_bbox, repair_candidate.x, repair_candidate.y)
    dx = repair_candidate.x - original_candidate.x if original_candidate else 0
    dy = repair_candidate.y - original_candidate.y if original_candidate else 0
    memory.append(
        {
            "context": "modal",
            "original_candidate_kind": original_candidate.kind if original_candidate else None,
            "successful_offset": [dx, dy],
            "successful_relative_position": [rx, ry],
            "state_id": state_id,
            "modal_bbox": modal_bbox,
            "repair_strategy": repair_candidate.strategy,
        }
    )
    memory_path = Path(path)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(json.dumps(memory[-200:], ensure_ascii=False, indent=2), encoding="utf-8")


def repair_candidate_to_dict(candidate: RepairCandidate) -> dict[str, Any]:
    return asdict(candidate)


def dedupe_repair_candidates(candidates: list[RepairCandidate], *, min_distance: int = 8) -> list[RepairCandidate]:
    kept: list[RepairCandidate] = []
    for candidate in candidates:
        if any(((candidate.x - existing.x) ** 2 + (candidate.y - existing.y) ** 2) ** 0.5 < min_distance for existing in kept):
            continue
        kept.append(candidate)
    return kept


def point_from_relative(bbox: list[int], rx: float, ry: float) -> tuple[int, int]:
    x, y, width, height = bbox
    return round(x + width * rx), round(y + height * ry)


def relative_position(bbox: list[int], x: int, y: int) -> tuple[float, float]:
    left, top, width, height = bbox
    return (x - left) / max(width, 1), (y - top) / max(height, 1)


def point_in_bbox(x: int, y: int, bbox: list[int]) -> bool:
    left, top, width, height = bbox
    return left <= x <= left + width and top <= y <= top + height


def valid_pair(value: object) -> bool:
    return isinstance(value, list) and len(value) == 2 and all(isinstance(item, (int, float)) for item in value)
