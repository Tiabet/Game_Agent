from __future__ import annotations

import argparse
import json
import sys

from agent.actions import execute_action, parse_action
from agent.env import GameEnvironment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute an assistant-produced action JSON via ADB")
    parser.add_argument("--adb-path", default=r"C:\LDPlayer\LDPlayer9\adb.exe")
    parser.add_argument("--device", default="emulator-5554")
    parser.add_argument("--json", help="Action JSON. If omitted, stdin is used.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually sends the action to LDPlayer. Without this, dry-run is used.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw = args.json if args.json is not None else sys.stdin.read()
    payload = json.loads(raw)

    action = parse_action(payload)
    env = GameEnvironment(adb_path=args.adb_path, device=args.device)
    execute_action(env, action, dry_run=not args.execute)


if __name__ == "__main__":
    main()
