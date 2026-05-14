from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.env import GameEnvironment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("x", type=int)
    parser.add_argument("y", type=int)
    parser.add_argument("count", type=int)
    parser.add_argument("--interval", type=float, default=0.08)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    env = GameEnvironment()
    for index in range(args.count):
        env.tap(args.x, args.y)
        env.wait(args.interval)
        print(f"tap={index + 1}/{args.count} x={args.x} y={args.y}")


if __name__ == "__main__":
    main()
