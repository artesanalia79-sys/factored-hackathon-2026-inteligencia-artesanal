"""Pre-commit guard: block organizer data, secrets and sealed evaluation material.

This hook is a second line of defense next to gitleaks. It needs only the Python
standard library so it works on every teammate's machine and in CI.

It fails when a staged file:
  * lives under a forbidden path (private/, data/, eval/heldout/, ...);
  * has a forbidden extension (*.duckdb, *.parquet, *.sqlite, organizer PDFs/DOCX);
  * is a dotenv file other than `.env.example`;
  * contains something that looks like a credential (AWS key id, AWS secret,
    OpenAI key, private key block);
  * contains any non-trivial value from the local `.env` (e.g. APP_SECRET_KEY,
    OPENAI_API_KEY, S3_BUCKET). Values are never printed, only key names.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path, PurePosixPath

FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "private/",
    "data/",
    "eval/heldout/",
    "mlruns/",
    "mlartifacts/",
)
FORBIDDEN_SUFFIXES: tuple[str, ...] = (
    ".duckdb",
    ".duckdb.wal",
    ".parquet",
    ".sqlite",
    ".sqlite3",
    ".pdf",
    ".docx",
)
ALLOWED_DOTENV: frozenset[str] = frozenset({".env.example"})

# Patterns are assembled from pieces so this file never matches itself.
_AKIA = "AK" + "IA"
_ASIA = "AS" + "IA"
SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "aws_access_key_id": re.compile(rf"\b(?:{_AKIA}|{_ASIA})[0-9A-Z]{{16}}\b"),
    "aws_secret_access_key": re.compile(
        r"(?i)aws_?secret_?access_?key\s*[:=]\s*['\"]?[A-Za-z0-9/+]{40}\b"
    ),
    "openai_api_key": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b"),
    "private_key_block": re.compile("-----BEGIN " + r"(?:[A-Z]+ )?PRIVATE KEY-----"),
}

# .env keys whose local values must never appear in versioned files.
SENSITIVE_ENV_KEYS: frozenset[str] = frozenset(
    {"APP_SECRET_KEY", "OPENAI_API_KEY", "S3_BUCKET", "HELDOUT_DIR"}
)
MIN_SENSITIVE_VALUE_LEN = 8
MAX_SCAN_BYTES = 2_000_000


def _normalize(path: str) -> str:
    # Backslashes are normalized explicitly so Windows-style paths behave the same on Linux CI.
    norm = path.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm


def check_path(path: str) -> str | None:
    """Return a reason string if the path itself is forbidden, else None."""
    norm = _normalize(path)
    lower = norm.lower()
    name = PurePosixPath(norm).name
    for prefix in FORBIDDEN_PREFIXES:
        if lower.startswith(prefix) or (prefix == "private/" and "/private/" in f"/{lower}"):
            return f"forbidden location '{prefix}'"
    for suffix in FORBIDDEN_SUFFIXES:
        if lower.endswith(suffix):
            return f"forbidden file type '{suffix}'"
    if (name == ".env" or name.startswith(".env.")) and name not in ALLOWED_DOTENV:
        return "dotenv files must never be committed (only .env.example)"
    return None


def scan_text(text: str) -> list[str]:
    """Return the names of secret patterns found in the text."""
    return [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(text)]


def load_sensitive_env_values(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key in SENSITIVE_ENV_KEYS and len(value) >= MIN_SENSITIVE_VALUE_LEN:
            values[key] = value
    return values


def scan_env_leaks(text: str, env_values: dict[str, str]) -> list[str]:
    return [key for key, value in env_values.items() if value in text]


def main(argv: list[str] | None = None) -> int:
    files = argv if argv is not None else sys.argv[1:]
    env_values = load_sensitive_env_values(Path(".env"))
    problems: list[str] = []
    for file in files:
        reason = check_path(file)
        if reason:
            problems.append(f"{file}: {reason}")
            continue
        path = Path(file)
        if not path.is_file() or path.stat().st_size > MAX_SCAN_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary files are covered by the size/extension rules
        for hit in scan_text(text):
            problems.append(f"{file}: looks like a secret ({hit})")
        for key in scan_env_leaks(text, env_values):
            problems.append(f"{file}: contains the local value of {key} from .env")
    if problems:
        print("Blocked by check-forbidden-paths:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "Move organizer material to private/, keep secrets in .env or your AWS profile, "
            "and never commit data or held-out files.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
