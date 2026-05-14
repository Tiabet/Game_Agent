from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.candidates import Candidate


@dataclass(frozen=True)
class Evaluation:
    outcome: str
    reasons: list[str]

    @property
    def failed(self) -> bool:
        return self.outcome != "success"


def evaluate_action_result(
    *,
    changed: bool,
    before_state_id: str,
    after_state_id: str,
    active_layer_before: str,
    active_layer_after: str,
    candidate: Candidate | None,
    recent_records: list[dict[str, Any]],
) -> Evaluation:
    if changed:
        return Evaluation("success", ["changed=True"])

    reasons: list[str] = []
    if active_layer_before == "modal" and candidate is not None and candidate.kind == "popup_button":
        reasons.append("click_target_failed")
    if candidate is not None and repeated_candidate(candidate.id, recent_records):
        reasons.append("low_value_candidate")
    if before_state_id == after_state_id and active_layer_before == "modal" and active_layer_after == "modal":
        reasons.append("modal_not_dismissed")
    if not reasons:
        reasons.append("unchanged")

    if "modal_not_dismissed" in reasons:
        return Evaluation("modal_not_dismissed", reasons)
    if "click_target_failed" in reasons:
        return Evaluation("click_target_failed", reasons)
    if "low_value_candidate" in reasons:
        return Evaluation("low_value_candidate", reasons)
    return Evaluation("failed", reasons)


def repeated_candidate(candidate_id: str, recent_records: list[dict[str, Any]], *, threshold: int = 1) -> bool:
    count = 0
    for record in reversed(recent_records):
        if record.get("candidate_id") == candidate_id and record.get("changed") is False:
            count += 1
            if count >= threshold:
                return True
    return False
