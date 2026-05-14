from __future__ import annotations

from dataclasses import dataclass

from explorer.state_graph import StateGraph
from tools.candidates import Candidate


@dataclass(frozen=True)
class ExploreAction:
    type: str
    candidate_id: str | None = None
    x: int | None = None
    y: int | None = None
    label_guess: str = ""

    def to_edge_action(self) -> dict[str, object]:
        if self.type == "tap":
            return {
                "type": "tap",
                "candidate_id": self.candidate_id,
                "x": self.x,
                "y": self.y,
                "label_guess": self.label_guess,
            }
        return {"type": self.type}


def choose_action(graph: StateGraph, state_id: str, candidates: list[Candidate]) -> ExploreAction:
    tried = graph.tried_candidate_ids(state_id)
    for candidate in candidates:
        if candidate.id not in tried:
            return ExploreAction(
                type="tap",
                candidate_id=candidate.id,
                x=candidate.x,
                y=candidate.y,
                label_guess=candidate.label_guess,
            )
    return ExploreAction(type="back")
