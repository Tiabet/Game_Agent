from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_MERCENARY_MEMORY_PATH = Path("runtime/mercenary_memory.json")


def append_mercenary_memory(
    path: str | Path = DEFAULT_MERCENARY_MEMORY_PATH,
    *,
    state_id: str,
    screenshot_path: str,
    selected_active_layer: str,
    observations: list[str],
) -> None:
    cleaned = [item.strip() for item in observations if isinstance(item, str) and item.strip()]
    if not cleaned:
        return

    memory_path = Path(path)
    data = load_memory(memory_path)
    entries = data.setdefault("entries", [])
    entries.append(
        {
            "time": datetime.now(timezone.utc).isoformat(),
            "state_id": state_id,
            "screenshot_path": screenshot_path,
            "selected_active_layer": selected_active_layer,
            "observations": cleaned,
        }
    )
    data["version"] = 1
    apply_knowledge_updates(data, cleaned)
    data["latest_observations"] = summarize_latest(entries)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_memory(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "entries": [], "latest_observations": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "entries": [], "latest_observations": []}
    if not isinstance(data, dict):
        return {"version": 1, "entries": [], "latest_observations": []}
    if not isinstance(data.get("entries"), list):
        data["entries"] = []
    if not isinstance(data.get("knowledge"), dict):
        data["knowledge"] = empty_knowledge()
    return data


def empty_knowledge() -> dict[str, Any]:
    return {"mercenaries": {}, "synergies": {}, "recipes": {}}


def apply_knowledge_updates(data: dict[str, Any], observations: list[str]) -> None:
    knowledge = data.setdefault("knowledge", empty_knowledge())
    if not isinstance(knowledge, dict):
        knowledge = empty_knowledge()
        data["knowledge"] = knowledge
    knowledge.setdefault("mercenaries", {})
    knowledge.setdefault("synergies", {})
    knowledge.setdefault("recipes", {})
    for observation in observations:
        update = parse_structured_observation(observation)
        if update is None:
            continue
        kind = update.pop("kind")
        if kind == "MERCENARY":
            name = update.pop("name", "")
            if is_meaningful_knowledge_key(name):
                merge_record(knowledge["mercenaries"], name, update)
                link_mercenary_to_synergies(knowledge, name, update.get("synergies"))
        elif kind == "SYNERGY":
            name = update.pop("name", "")
            if is_meaningful_knowledge_key(name):
                normalize_synergy_update(update)
                merge_record(knowledge["synergies"], name, update)
        elif kind == "RECIPE":
            result = update.pop("result", "")
            if is_meaningful_knowledge_key(result):
                merge_record(knowledge["recipes"], result, update)


def is_meaningful_knowledge_key(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip().lower()
    if not text:
        return False
    return text not in {"unknown", "unreadable", "n/a", "none", "?"}


def parse_structured_observation(value: str) -> dict[str, Any] | None:
    text = value.strip()
    if ":" not in text:
        return None
    prefix, body = text.split(":", 1)
    kind = prefix.strip().upper()
    if kind not in {"MERCENARY", "SYNERGY", "RECIPE"}:
        return None
    fields: dict[str, Any] = {"kind": kind}
    for chunk in body.split(";"):
        if "=" not in chunk:
            continue
        key, raw = chunk.split("=", 1)
        key = key.strip().lower()
        raw = raw.strip()
        if not key:
            continue
        fields[key] = parse_field_value(raw)
    return fields


def parse_field_value(value: str) -> Any:
    if "," in value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def merge_record(records: dict[str, Any], key: str, update: dict[str, Any]) -> None:
    record = records.setdefault(key, {})
    if not isinstance(record, dict):
        record = {}
        records[key] = record
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    for field, value in update.items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, list):
            existing = record.get(field)
            items = existing if isinstance(existing, list) else []
            for item in value:
                if item not in items:
                    items.append(item)
            record[field] = items
        else:
            record[field] = value


def normalize_synergy_update(update: dict[str, Any]) -> None:
    count = update.get("count")
    if isinstance(count, str) and "/" in count:
        active, required = count.split("/", 1)
        update.setdefault("active_count", active.strip())
        update.setdefault("required_count", required.strip())
    members = update.get("members")
    required_members = update.get("required_members")
    if not is_known_member_value(required_members) and is_known_member_value(members):
        update["required_members"] = members
    update.setdefault("required_members", "unknown")


def link_mercenary_to_synergies(knowledge: dict[str, Any], mercenary_name: str, synergies: object) -> None:
    if not is_meaningful_knowledge_key(mercenary_name):
        return
    synergy_names = normalize_name_list(synergies)
    if not synergy_names:
        return
    records = knowledge.setdefault("synergies", {})
    for synergy_name in synergy_names:
        record = records.setdefault(synergy_name, {})
        if not isinstance(record, dict):
            record = {}
            records[synergy_name] = record
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        existing = normalize_name_list(record.get("required_members"))
        if mercenary_name not in existing:
            existing.append(mercenary_name)
        record["required_members"] = existing


def normalize_name_list(value: object) -> list[str]:
    if isinstance(value, list):
        values = value
    elif isinstance(value, str):
        values = [item.strip() for item in value.split(",")]
    else:
        return []
    result: list[str] = []
    for item in values:
        if is_meaningful_knowledge_key(item) and item not in result and not is_placeholder_member(str(item)):
            result.append(str(item))
    return result


def is_known_member_value(value: object) -> bool:
    return bool(normalize_name_list(value))


def is_placeholder_member(value: str) -> bool:
    text = value.strip().lower()
    return text in {"visible icons", "unreadable_visible_icons", "unknown", "unreadable"}


def summarize_latest(entries: list[object], *, limit: int = 50) -> list[str]:
    seen: set[str] = set()
    summary: list[str] = []
    for entry in reversed(entries):
        if not isinstance(entry, dict):
            continue
        observations = entry.get("observations")
        if not isinstance(observations, list):
            continue
        for observation in observations:
            if not isinstance(observation, str):
                continue
            normalized = " ".join(observation.split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            summary.append(normalized)
            if len(summary) >= limit:
                return list(reversed(summary))
    return list(reversed(summary))
