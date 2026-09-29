"""CLI entry point for `uv run poe ingest` / `uv run poe ingest-check`.

Usage:
    python -m bankagent.ingest.cli               # ingest, fail loudly on drift
    python -m bankagent.ingest.cli --allow-drift  # ingest, accept and record drift
    python -m bankagent.ingest.cli --check        # verify the last manifest is still current,
                                                    # without downloading anything

Requires `AWS_PROFILE` and `S3_BUCKET` from `.env` (see `.env.example`), and AWS credentials in
the local profile: `aws configure --profile factored`. Never pass AWS keys as arguments or env
values other than through the AWS CLI profile.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load_dotenv(root: Path) -> None:
    """Load `.env` into the process environment (no external dependency, keys only)."""
    import os

    env_path = root / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--allow-drift",
        action="store_true",
        help="accept and record drift vs. the previous manifest",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="only report drift against the current bronze files; no download",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s"
    )
    _load_dotenv(ROOT)

    # Imported after .env is loaded and lazily so `--help` works without boto3 installed.
    from bankagent.ingest.catalog import SourceTableNotFound
    from bankagent.ingest.pipeline import IngestDriftError, resolve_config, run_ingest
    from bankagent.ingest.s3_source import S3Unavailable, download_objects, list_bucket_objects

    try:
        config = resolve_config(root=ROOT, allow_drift=args.allow_drift)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.check:
        from bankagent.ingest.manifest import load_manifest

        manifest = load_manifest(config.manifest_path)
        if manifest is None:
            print("No manifest yet; run `uv run poe ingest` first.", file=sys.stderr)
            return 1
        for entry in manifest.files:
            path = config.bronze_dir / entry.relative_path
            if not path.exists():
                print(f"ERROR: {entry.relative_path} is missing", file=sys.stderr)
                return 1
        print(f"Manifest OK: {len(manifest.files)} tables recorded, all files present.")
        return 0

    def list_objects_fn():
        return list_bucket_objects(
            bucket=config.s3_bucket, aws_profile=config.aws_profile, region=config.region
        )

    def download_fn(objects, dest_dir):
        return download_objects(
            objects,
            bucket=config.s3_bucket,
            aws_profile=config.aws_profile,
            region=config.region,
            dest_dir=dest_dir,
        )

    try:
        manifest, problems = run_ingest(
            config, list_objects_fn=list_objects_fn, download_fn=download_fn
        )
    except IngestDriftError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Re-run with --allow-drift if this change is expected.", file=sys.stderr)
        return 1
    except (S3Unavailable, SourceTableNotFound) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    total_rows = sum(entry.row_count for entry in manifest.files)
    print(f"Ingested {len(manifest.files)} tables, {total_rows:,} rows total.")
    if problems:
        print("Drift accepted (--allow-drift):")
        for problem in problems:
            print(f"  - {problem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
