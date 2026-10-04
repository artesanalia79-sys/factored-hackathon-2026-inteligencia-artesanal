"""Create a local `.env` from `.env.example` without ever printing secret values.

Usage:
    uv run poe init-env                       # create .env, generate APP_SECRET_KEY
    uv run poe init-env --from-dictionary PATH # also fill AWS_DEFAULT_REGION and S3_BUCKET
                                                # from the organizer data dictionary (.txt)

AWS access keys are intentionally NOT written to .env. Store them with
`aws configure --profile factored` so they only live in your AWS CLI profile.
Existing non-empty values in .env are preserved, also those written as `export KEY=value` or
`KEY = value`, and keys that are not in .env.example are kept at the end of the file. The file
is readable by its owner only (mode 600).
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"

_LINE = re.compile(r"^(?:export\s+)?(?P<key>[A-Z][A-Z0-9_]*)\s*=\s*(?P<value>.*)$")


def _parse(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """(value by key, original line by key) of an existing dotenv file."""
    values: dict[str, str] = {}
    lines: dict[str, str] = {}
    if not path.exists():
        return values, lines
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _LINE.match(line.strip())
        if match:
            values[match["key"]] = match["value"]
            lines[match["key"]] = line
    return values, lines


def _write_private(path: Path, text: str) -> None:
    """Write a file only its owner can read: it holds secrets."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    path.chmod(0o600)  # os.open keeps the mode of a file that already existed


def _from_dictionary(path: Path) -> dict[str, str]:
    """Extract only the bucket name and region from the organizer dictionary text."""
    found: dict[str, str] = {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    bucket = re.search(r"Bucket Name\s+(\S+)", text)
    region = re.search(r"^Region\s+(\S+)", text, flags=re.MULTILINE)
    if bucket:
        found["S3_BUCKET"] = bucket.group(1)
    if region:
        found["AWS_DEFAULT_REGION"] = region.group(1)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--from-dictionary", type=Path, default=None)
    args = parser.parse_args(argv)

    if not EXAMPLE.exists():
        print("ERROR: .env.example not found", file=sys.stderr)
        return 1

    existing, existing_lines = _parse(TARGET)
    overrides: dict[str, str] = {}
    if args.from_dictionary is not None:
        if not args.from_dictionary.exists():
            print(f"ERROR: dictionary file not found: {args.from_dictionary}", file=sys.stderr)
            return 1
        overrides = _from_dictionary(args.from_dictionary)

    filled: list[str] = []
    out_lines: list[str] = []
    template_keys: set[str] = set()
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = _LINE.match(line.strip())
        if not match:
            out_lines.append(line)
            continue
        key, default = match["key"], match["value"]
        template_keys.add(key)
        value = existing.get(key) or overrides.get(key) or default
        if key == "APP_SECRET_KEY" and not value:
            value = secrets.token_urlsafe(48)
        if value and value != default:
            filled.append(key)
        out_lines.append(f"{key}={value}")

    kept = [key for key in existing_lines if key not in template_keys]
    if kept:
        out_lines += ["", "# Kept from your .env (not in .env.example)."]
        out_lines += [existing_lines[key] for key in kept]
        filled += [key for key in kept if existing[key]]

    _write_private(TARGET, "\n".join(out_lines) + "\n")
    # Only key NAMES are printed, never values.
    print(f"Wrote {TARGET.name}. Keys with local values: {', '.join(sorted(filled)) or 'none'}")
    print("Reminder: AWS keys belong in `aws configure --profile factored`, not in .env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
