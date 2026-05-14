from __future__ import annotations

from pathlib import Path


def main() -> None:
    path = Path("runtime/planner_response.json")
    path.unlink(missing_ok=True)
    print(f"Cleared {path}")


if __name__ == "__main__":
    main()
