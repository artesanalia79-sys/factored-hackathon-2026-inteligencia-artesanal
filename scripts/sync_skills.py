"""Mirror the canonical agent skills into every tool-specific skills folder.

`.agents/skills/` is the single source of truth (read natively by Codex and other
AGENTS.md-compatible tools). Claude Code reads `.claude/skills/` and Kiro reads
`.kiro/skills/`, so this script keeps byte-identical copies there.

Usage:
    python scripts/sync_skills.py          # write the mirrors (strict: removes stale files)
    python scripts/sync_skills.py --check  # exit 1 if any mirror drifts (CI + pre-commit)

Only the standard library is used so the pre-commit hook works without the venv.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".agents" / "skills"
MIRRORS: tuple[Path, ...] = (ROOT / ".claude" / "skills", ROOT / ".kiro" / "skills")


def _files(base: Path) -> dict[str, bytes]:
    """Map relative POSIX path -> bytes for every file under base."""
    if not base.exists():
        return {}
    return {
        path.relative_to(base).as_posix(): path.read_bytes()
        for path in sorted(base.rglob("*"))
        if path.is_file()
    }


def _label(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def diff(source: Path, mirror: Path) -> list[str]:
    """Return human-readable drift descriptions between source and mirror."""
    src, dst = _files(source), _files(mirror)
    label = _label(mirror)
    problems: list[str] = []
    for rel in sorted(src.keys() - dst.keys()):
        problems.append(f"missing in {label}: {rel}")
    for rel in sorted(dst.keys() - src.keys()):
        problems.append(f"stale in {label}: {rel}")
    for rel in sorted(src.keys() & dst.keys()):
        if src[rel] != dst[rel]:
            problems.append(f"differs in {label}: {rel}")
    return problems


def sync(source: Path, mirror: Path) -> None:
    if mirror.exists():
        shutil.rmtree(mirror)
    shutil.copytree(source, mirror)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync agent skills mirrors.")
    parser.add_argument("--check", action="store_true", help="only report drift")
    args = parser.parse_args(argv)

    if not SOURCE.is_dir():
        print(f"ERROR: canonical skills folder not found: {SOURCE}", file=sys.stderr)
        return 1

    if args.check:
        problems = [p for mirror in MIRRORS for p in diff(SOURCE, mirror)]
        if problems:
            print("Skill mirrors are out of sync with .agents/skills:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            print("Fix: uv run poe sync-skills (never edit the mirrors directly).", file=sys.stderr)
            return 1
        print(f"Skills in sync ({len(_files(SOURCE))} files x {len(MIRRORS)} mirrors).")
        return 0

    for mirror in MIRRORS:
        sync(SOURCE, mirror)
        print(f"Synced {_label(SOURCE)} -> {_label(mirror)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
