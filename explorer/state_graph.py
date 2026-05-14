from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from explorer.screen_hash import similar_hash


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StateGraph:
    path: Path
    threshold: int = 6
    states: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path, *, threshold: int = 6) -> "StateGraph":
        graph_path = Path(path)
        if not graph_path.exists():
            return cls(path=graph_path, threshold=threshold)

        data = json.loads(graph_path.read_text(encoding="utf-8"))
        return cls(
            path=graph_path,
            threshold=threshold,
            states=list(data.get("states", [])),
            edges=list(data.get("edges", [])),
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"states": self.states, "edges": self.edges}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_or_create_state(self, screen_hash: str, screenshot_path: str | Path) -> dict[str, Any]:
        now = utc_now()
        state = self.find_state(screen_hash)
        if state is not None:
            state["last_seen"] = now
            state["screenshot_path"] = str(screenshot_path)
            return state

        state = {
            "state_id": self.next_state_id(),
            "hash": screen_hash,
            "first_seen": now,
            "last_seen": now,
            "screenshot_path": str(screenshot_path),
        }
        self.states.append(state)
        return state

    def find_state(self, screen_hash: str) -> dict[str, Any] | None:
        for state in self.states:
            if similar_hash(str(state.get("hash", "")), screen_hash, threshold=self.threshold):
                return state
        return None

    def add_edge(
        self,
        *,
        from_state: str,
        to_state: str,
        action: dict[str, Any],
        changed: bool,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        edge = {
            "from_state": from_state,
            "to_state": to_state,
            "action": action,
            "changed": changed,
            "timestamp": timestamp or utc_now(),
        }
        self.edges.append(edge)
        return edge

    def tried_candidate_ids(self, state_id: str) -> set[str]:
        tried: set[str] = set()
        for edge in self.edges:
            if edge.get("from_state") != state_id:
                continue
            action = edge.get("action", {})
            if isinstance(action, dict) and action.get("type") in {"tap", "tap_candidate"}:
                candidate_id = action.get("candidate_id")
                if candidate_id is not None:
                    tried.add(str(candidate_id))
        return tried

    def next_state_id(self) -> str:
        return f"state_{len(self.states) + 1:06d}"
