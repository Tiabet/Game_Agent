from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent.actions import Action, execute_actions
from agent.env import GameEnvironment
from agent.target_config import TargetPoint, load_target
from agent.stage import clicks_to_target, read_current_stage


AVAILABLE_BEHAVIORS = ["enter_sewer", "go_to_stage_249_and_enter"]


class Behavior(Protocol):
    name: str
    description: str

    def plan(self) -> list[Action]:
        ...


@dataclass(frozen=True)
class TapButtonBehavior:
    name: str
    description: str
    x: int
    y: int
    wait_seconds: float = 1.0

    def plan(self) -> list[Action]:
        return [
            Action("tap", x=self.x, y=self.y, reason=self.description),
            Action("wait", seconds=self.wait_seconds, reason="wait after button tap"),
        ]


@dataclass(frozen=True)
class StageNavigationBehavior:
    name: str
    description: str
    left_arrow: TargetPoint
    enter_button: TargetPoint
    current_stage: int = 300
    target_stage: int = 249
    tap_interval_seconds: float = 0.08
    settle_seconds: float = 0.5

    def plan(self) -> list[Action]:
        actions: list[Action] = []
        left_taps = self.current_stage - self.target_stage

        if left_taps < 0:
            raise ValueError(
                f"target_stage must be less than or equal to current_stage for left-arrow navigation: "
                f"current_stage={self.current_stage}, target_stage={self.target_stage}"
            )

        for index in range(left_taps):
            actions.append(
                Action(
                    "tap",
                    x=self.left_arrow.x,
                    y=self.left_arrow.y,
                    reason=f"stage left arrow tap {index + 1}/{left_taps}",
                )
            )
            actions.append(Action("wait", seconds=self.tap_interval_seconds, reason="short stage navigation interval"))

        actions.extend(
            [
                Action("wait", seconds=self.settle_seconds, reason="wait for stage selector to settle"),
                Action(
                    "tap",
                    x=self.enter_button.x,
                    y=self.enter_button.y,
                    reason="tap stage enter button",
                ),
                Action("wait", seconds=1.0, reason="wait after entering stage"),
            ]
        )
        return actions


@dataclass(frozen=True)
class DetectedStageNavigationBehavior:
    name: str
    description: str
    left_arrow: TargetPoint
    enter_button: TargetPoint
    current_stage: int
    target_stage: int = 249
    tap_interval_seconds: float = 0.08
    settle_seconds: float = 0.5

    def plan(self) -> list[Action]:
        left_taps = clicks_to_target(self.current_stage, self.target_stage)
        actions: list[Action] = []
        for index in range(left_taps):
            actions.append(
                Action(
                    "tap",
                    x=self.left_arrow.x,
                    y=self.left_arrow.y,
                    reason=f"stage left arrow tap {index + 1}/{left_taps}",
                )
            )
            actions.append(Action("wait", seconds=self.tap_interval_seconds, reason="short stage navigation interval"))
        actions.extend(
            [
                Action("wait", seconds=self.settle_seconds, reason="wait for stage selector to settle"),
                Action("tap", x=self.enter_button.x, y=self.enter_button.y, reason="tap stage enter button"),
                Action("wait", seconds=1.0, reason="wait after entering stage"),
            ]
        )
        return actions


def create_enter_sewer_behavior(x: int | None = None, y: int | None = None) -> Behavior:
    if (x is None) != (y is None):
        raise ValueError("Both x and y must be provided when overriding enter_sewer target.")
    explicit_target = x is not None and y is not None
    target = TargetPoint("enter_sewer", x, y) if explicit_target else load_target("enter_sewer")
    if not explicit_target and not target.description.startswith("confirmed:"):
        raise ValueError(
            "enter_sewer target is not confirmed. "
            "Run agent.calibrate --set-target enter_sewer --x <x> --y <y> after checking the grid."
        )
    return TapButtonBehavior(
        name="enter_sewer",
        description="tap the enter sewer button on the main screen",
        x=target.x,
        y=target.y,
    )


def create_go_to_stage_249_and_enter_behavior() -> Behavior:
    left_arrow = _load_confirmed_target("stage_left_arrow")
    enter_button = _load_confirmed_target("stage_enter_button")
    result = read_current_stage(GameEnvironment())
    if result.stage is None:
        raise ValueError(
            f"Cannot read current stage from image. roi={result.roi_path} reason={result.reason}. "
            "Save stage templates first or use MCP vision to set the current stage."
        )
    return StageNavigationBehavior(
        name="go_to_stage_249_and_enter",
        description=f"navigate from detected stage {result.stage} to stage 249, then tap enter",
        left_arrow=left_arrow,
        enter_button=enter_button,
        current_stage=result.stage,
        target_stage=249,
    )


def get_behavior(name: str) -> Behavior:
    if name == "enter_sewer":
        return create_enter_sewer_behavior()
    if name == "go_to_stage_249_and_enter":
        return create_go_to_stage_249_and_enter_behavior()
    raise ValueError(f"Unknown behavior: {name}")


def execute_behavior(env: GameEnvironment, behavior: Behavior, *, dry_run: bool = True) -> None:
    print(f"Behavior: {behavior.name} - {behavior.description}")
    execute_actions(env, behavior.plan(), dry_run=dry_run)


def _load_confirmed_target(name: str) -> TargetPoint:
    target = load_target(name)
    if not target.description.startswith("confirmed:"):
        raise ValueError(f"Target is not confirmed: {name}")
    return target
