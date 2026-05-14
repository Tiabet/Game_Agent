from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from tools.korean_ocr import OCRBlock, blocks_to_dicts, knowledge_panel_crop, ocr_image


DEFAULT_OCR_LOG_PATH = Path("runtime/ocr_observations.jsonl")
COUNT_RE = re.compile(r"(?P<a>\d)\s*[/|lI1]\s*(?P<b>\d)")
SYNERGY_HINTS = (
    "공격",
    "피해",
    "확률",
    "회복",
    "시간",
    "동안",
    "무적",
    "부활",
    "상태",
    "증가",
    "감소",
    "체력",
    "생성",
    "기절",
    "스턴",
    "몬스터",
    "%",
)
GRADE_HINTS = ("일반", "희귀", "전설", "신화")
RECIPE_HINTS = ("조합", "필요", "재료", "조합 가능", "가능 용병")
ATTACK_LABELS = ("공격력",)
HEALTH_LABELS = ("체력",)
ATTACK_SPEED_LABELS = ("공격속", "공격숙도")
MOVE_SPEED_LABELS = ("이동속", "디동속도", "동속도")
STAT_LABELS = ATTACK_LABELS + HEALTH_LABELS + ATTACK_SPEED_LABELS + MOVE_SPEED_LABELS


def extract_mercenary_knowledge_from_image(
    image_path: str | Path,
    *,
    state_id: str,
    log_path: str | Path = DEFAULT_OCR_LOG_PATH,
) -> list[str]:
    image_path = Path(image_path)
    if not image_path.exists():
        return []
    try:
        with Image.open(image_path) as image:
            crop = knowledge_panel_crop(image.size)
        blocks = ocr_image(image_path, crop=crop, scale=1, min_confidence=0.01)
    except Exception as exc:
        append_ocr_log(log_path, state_id=state_id, image_path=image_path, blocks=[], updates=[], error=repr(exc))
        return []
    updates = extract_mercenary_knowledge(blocks)
    append_ocr_log(log_path, state_id=state_id, image_path=image_path, blocks=blocks, updates=updates, error="")
    return updates


def extract_mercenary_knowledge(blocks: list[OCRBlock]) -> list[str]:
    useful_blocks = [block for block in blocks if block.confidence >= 0.05 and not is_noise(block.text)]
    if looks_like_mercenary_detail(useful_blocks):
        return unique_keep_order(extract_mercenaries(blocks))

    updates: list[str] = []
    updates.extend(extract_synergies(blocks))
    updates.extend(extract_recipes(blocks))
    return unique_keep_order(updates)


def extract_mercenaries(blocks: list[OCRBlock]) -> list[str]:
    useful_blocks = [block for block in blocks if block.confidence >= 0.05 and not is_noise(block.text)]
    if not looks_like_mercenary_detail(useful_blocks):
        return []
    name = choose_mercenary_name(useful_blocks)
    if not name:
        return []
    grade = choose_mercenary_grade(useful_blocks)
    ability = choose_mercenary_ability(useful_blocks)
    attack = nearest_value_after_label(useful_blocks, ATTACK_LABELS)
    health = nearest_value_after_label(useful_blocks, HEALTH_LABELS)
    attack_speed = nearest_value_after_label(useful_blocks, ATTACK_SPEED_LABELS)
    move_speed = nearest_value_after_label(useful_blocks, MOVE_SPEED_LABELS)
    fields = [
        f"name={name}",
        f"grade={grade}",
        "level=unknown",
        "role=unknown",
        f"ability={ability or 'unknown'}",
        "synergies=unknown",
    ]
    if attack:
        fields.append(f"attack={attack}")
    if health:
        fields.append(f"health={health}")
    if attack_speed:
        fields.append(f"attack_speed={attack_speed}")
    if move_speed:
        fields.append(f"move_speed={move_speed}")
    return ["MERCENARY:" + ";".join(fields)]


def looks_like_mercenary_detail(blocks: list[OCRBlock]) -> bool:
    text = " ".join(block.text for block in blocks)
    stat_label_hits = sum(1 for label in STAT_LABELS if label in text)
    stat_value_hits = sum(
        1
        for block in blocks
        if 130 <= block.x <= 285 and 255 <= block.cy <= 315 and looks_like_stat_value(block.text)
    )
    has_name = any(is_probable_detail_name(block) for block in blocks)
    has_grade_or_buttons = any(label in text for label in (*GRADE_HINTS, "장착", "등급"))
    has_detail_stats = stat_label_hits >= 2 or stat_value_hits >= 2
    has_strong_detail_layout = stat_label_hits >= 3 and stat_value_hits >= 3
    return has_name and has_detail_stats and (has_grade_or_buttons or has_strong_detail_layout)


def choose_mercenary_name(blocks: list[OCRBlock]) -> str:
    candidates = [
        block
        for block in blocks
        if is_probable_detail_name(block)
    ]
    if not candidates:
        return ""
    return clean_field(max(candidates, key=lambda block: (block.confidence, block.w)).text)


def choose_mercenary_grade(blocks: list[OCRBlock]) -> str:
    text = " ".join(block.text for block in blocks)
    if "신화" in text:
        return "mythic"
    if "전설" in text:
        return "legendary"
    if "일반" in text:
        return "normal"
    return "unknown"


def choose_mercenary_ability(blocks: list[OCRBlock]) -> str:
    ability_blocks = [
        block
        for block in blocks
        if 315 <= block.cy <= 490
        and contains_korean(block.text)
        and not any(label in block.text for label in ("이전 등급", "다음 등", "장착"))
    ]
    return clean_field(" ".join(block.text for block in sorted(ability_blocks, key=lambda item: (item.y, item.x))))


def nearest_value_after_label(blocks: list[OCRBlock], labels: tuple[str, ...]) -> str:
    label_blocks = [block for block in blocks if any(label in block.text for label in labels)]
    value_blocks = [(block, extract_first_number(block.text)) for block in blocks]
    value_blocks = [(block, value) for block, value in value_blocks if value]
    best: tuple[int, OCRBlock] | None = None
    for label_block in label_blocks:
        for value_block, _value in value_blocks:
            if value_block.cx <= label_block.cx:
                continue
            if abs(value_block.cy - label_block.cy) > 24:
                continue
            distance = abs(value_block.cy - label_block.cy) * 3 + abs(value_block.x - label_block.x)
            if best is None or distance < best[0]:
                best = (distance, value_block)
    if best is None:
        return ""
    return extract_first_number(best[1].text)


def extract_synergies(blocks: list[OCRBlock]) -> list[str]:
    useful_blocks = [block for block in blocks if block.confidence >= 0.05 and not is_noise(block.text)]
    if looks_like_mercenary_detail(useful_blocks):
        return []
    count_blocks = [(block, normalize_count(block.text)) for block in useful_blocks]
    count_blocks = [(block, count) for block, count in count_blocks if count]
    updates: list[str] = []
    for count_block, count in count_blocks:
        row_blocks = [
            block
            for block in useful_blocks
            if count_block.y - 80 <= block.cy <= count_block.y + 55 and block.x >= 80
        ]
        if not row_blocks:
            continue
        name = choose_synergy_name(row_blocks, count_block)
        if not name:
            continue
        effect = choose_synergy_effect(row_blocks, name=name)
        if not effect and not any(hint in name for hint in SYNERGY_HINTS):
            continue
        updates.append(f"SYNERGY:name={name};count={count};effect={effect or 'unknown'};members=visible icons")
    return updates


def extract_recipes(blocks: list[OCRBlock]) -> list[str]:
    text = " ".join(block.text for block in blocks if block.confidence >= 0.08)
    useful_blocks = [block for block in blocks if block.confidence >= 0.05 and not is_noise(block.text)]
    if looks_like_mercenary_detail(useful_blocks):
        return []
    if not any(hint in text for hint in RECIPE_HINTS):
        return []
    if "시너지" in text and not any(hint in text for hint in ("전설", "신화", "조합", "필요", "재료")):
        return []
    result = "unknown"
    grade = "unknown"
    if "전설" in text:
        grade = "legendary"
    if "신화" in text:
        grade = "mythic"
    return [f"RECIPE:result={result};grade={grade};requires={text};source=visible OCR text"]


def choose_synergy_name(row_blocks: list[OCRBlock], count_block: OCRBlock) -> str:
    candidates = [
        block
        for block in row_blocks
        if block.y <= count_block.y
        and block.x >= 85
        and not normalize_count(block.text)
        and not looks_like_effect(block.text)
        and contains_korean(block.text)
    ]
    if not candidates:
        candidates = [
            block
            for block in row_blocks
            if block.x >= 85 and contains_korean(block.text) and not normalize_count(block.text)
        ]
    if not candidates:
        return ""
    chosen = max(candidates, key=lambda block: (block.confidence, -len(block.text)))
    return clean_field(chosen.text)


def choose_synergy_effect(row_blocks: list[OCRBlock], *, name: str) -> str:
    parts: list[str] = []
    for block in sorted(row_blocks, key=lambda item: (item.y, item.x)):
        text = clean_field(block.text)
        if not text or text == name or normalize_count(text):
            continue
        if looks_like_effect(text) or parts:
            parts.append(text)
    return clean_field(" ".join(parts))


def normalize_count(text: str) -> str:
    compact = re.sub(r"\s+", "", text)
    if "." in compact or "," in compact:
        return ""
    if len(compact) > 5:
        return ""
    if len(compact) > 3 and not any(separator in compact for separator in {"/", "|", "l", "I"}):
        return ""
    match = COUNT_RE.search(compact)
    if match:
        return f"{match.group('a')}/{match.group('b')}"
    if len(compact) == 3 and compact[0].isdigit() and compact[2].isdigit() and compact[1] in {"1", "l", "I", "|"}:
        return f"{compact[0]}/{compact[2]}"
    return ""


def looks_like_effect(text: str) -> bool:
    return any(hint in text for hint in SYNERGY_HINTS) or bool(re.search(r"\d+\s*%|\d+초", text))


def contains_korean(text: str) -> bool:
    return any("가" <= char <= "힣" for char in text)


def is_probable_detail_name(block: OCRBlock) -> bool:
    text = clean_field(block.text)
    if not text:
        return False
    if not (110 <= block.cx <= 250 and 175 <= block.cy <= 255):
        return False
    if len(text) > 14:
        return False
    if looks_like_stat_value(text) or normalize_count(text):
        return False
    blocked_terms = (*GRADE_HINTS, "시너지", "공격", "체력", "속도", "Lv", "LV", "level")
    if any(term in text for term in blocked_terms):
        return False
    return block.confidence >= 0.08


def looks_like_stat_value(text: str) -> bool:
    return bool(extract_first_number(text))


def extract_first_number(text: str) -> str:
    match = re.search(r"\d+(?:[.,]\d+)?", text.strip())
    if not match:
        return ""
    return match.group(0).replace(",", ".")


def is_noise(text: str) -> bool:
    compact = text.strip()
    if not compact:
        return True
    if len(compact) <= 1:
        return True
    if compact.lower() in {"x", "lv", "lvl"}:
        return True
    return False


def clean_field(text: str) -> str:
    return " ".join(text.replace(";", " ").replace("=", " ").split())


def unique_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def append_ocr_log(
    path: str | Path,
    *,
    state_id: str,
    image_path: Path,
    blocks: list[OCRBlock],
    updates: list[str],
    error: str,
) -> None:
    payload: dict[str, Any] = {
        "time": datetime.now(timezone.utc).isoformat(),
        "state_id": state_id,
        "image_path": str(image_path),
        "blocks": blocks_to_dicts(blocks),
        "updates": updates,
    }
    if error:
        payload["error"] = error
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")
