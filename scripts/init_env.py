"""Create a local `.env` from `.env.example` without ever printing secret values.

Usage:
    uv run poe init-env                       # create .env, generate APP_SECRET_KEY
    uv run poe init-env --from-dictionary PATH # also fill AWS_DEFAULT_REGION and S3_BUCKET
                                                # from the organizer data dictionary (.txt)

AWS access keys are intentionally NOT written to .env. Store them with
`aws configure --profile factored` so they only live in your AWS CLI profile.
Existing non-empty values in .env are preserved.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"

_LINE = re.compile(r"^(?P<key>[A-Z][A-Z0-9_]*)=(?P<value>.*)$")


def _parse(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _LINE.match(line.strip())
        if match:
            values[match["key"]] = match["value"]
    return values


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

    existing = _parse(TARGET)
    overrides: dict[str, str] = {}
    if args.from_dictionary is not None:
        if not args.from_dictionary.exists():
            print(f"ERROR: dictionary file not found: {args.from_dictionary}", file=sys.stderr)
            return 1
        overrides = _from_dictionary(args.from_dictionary)

    filled: list[str] = []
    out_lines: list[str] = []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = _LINE.match(line.strip())
        if not match:
            out_lines.append(line)
            continue
        key, default = match["key"], match["value"]
        value = existing.get(key) or overrides.get(key) or default
        if key == "APP_SECRET_KEY" and not value:
            value = secrets.token_urlsafe(48)
        if value and value != default:
            filled.append(key)
        out_lines.append(f"{key}={value}")

    TARGET.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    # Only key NAMES are printed, never values.
    print(f"Wrote {TARGET.name}. Keys with local values: {', '.join(sorted(filled)) or 'none'}")
    print("Reminder: AWS keys belong in `aws configure --profile factored`, not in .env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
