"""Placeholder for poe tasks whose implementation belongs to a later plan task.

It fails loudly (exit code 2) so nobody mistakes a placeholder for a passing step.
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    task, title, owner = [*argv, "?", "?", "?"][:3]
    print(
        f"NOT IMPLEMENTED YET: Task {task} ({title}), owner: {owner}.\n"
        "See docs/plan/implementation_plan.md and replace this placeholder in pyproject.toml.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
