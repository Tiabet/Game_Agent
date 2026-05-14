from __future__ import annotations

import argparse

from agent.env import GameEnvironment
from agent.stage import clicks_to_target, read_current_stage, save_stage_template


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read and manage current stage number")
    subparsers = parser.add_subparsers(dest="command", required=True)

    read = subparsers.add_parser("read")
    read.add_argument("--adb-path", default=r"C:\LDPlayer\LDPlayer9\adb.exe")
    read.add_argument("--device", default="emulator-5554")
    read.add_argument("--target", type=int, default=249)
    read.add_argument("--min-confidence", type=float, default=0.85)

    template = subparsers.add_parser("save-template")
    template.add_argument("stage", type=int)
    template.add_argument("--roi", default="runtime/stage_number_roi.png")
    template.add_argument("--confirmed", action="store_true")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.command == "read":
        env = GameEnvironment(adb_path=args.adb_path, device=args.device)
        result = read_current_stage(env, min_confidence=args.min_confidence)
        print(f"roi={result.roi_path}")
        print(f"stage={result.stage}")
        print(f"confidence={result.confidence:.3f}")
        print(f"reason={result.reason}")
        if result.stage is not None:
            print(f"clicks_to_{args.target}={clicks_to_target(result.stage, args.target)}")
        return

    if args.command == "save-template":
        if not args.confirmed:
            raise ValueError("Refusing to save a stage template without --confirmed.")
        path = save_stage_template(args.stage, args.roi)
        print(f"saved_template={path}")
        return

    raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
