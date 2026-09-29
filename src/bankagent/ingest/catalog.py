"""The 13 organizer tables we ingest, and where to find them in the bucket.

Source: `docs/challenge/data_dictionary.md`. The exact prefix layout is not documented, so
`discover_key` lists the bucket and matches by table name; this is more robust than guessing a
fixed path and fails loudly (`SourceTableNotFound`) instead of silently ingesting nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

TABLES: tuple[str, ...] = (
    "customers",
    "products",
    "branches",
    "service_agents",
    "marketing_campaigns",
    "transactions",
    "call_center_interactions",
    "call_transcripts",
    "satisfaction_surveys",
    "digital_events",
    "complaints",
    "campaign_sends",
    "daily_exchange_rates",
)

# Approximate row counts from the dataset summary. Used only to warn on wildly unexpected sizes,
# never to fail the build (partitioned/date-limited pulls are expected to be smaller).
DOCUMENTED_ROW_COUNTS: dict[str, int] = {
    "customers": 150_000,
    "products": 400_000,
    "branches": 350,
    "service_agents": 1_200,
    "marketing_campaigns": 200,
    "transactions": 5_000_000,
    "call_center_interactions": 800_000,
    "call_transcripts": 200_000,
    "satisfaction_surveys": 250_000,
    "digital_events": 10_000_000,
    "complaints": 80_000,
    "campaign_sends": 2_000_000,
    "daily_exchange_rates": 3_000,
}


class SourceTableNotFound(RuntimeError):
    def __init__(self, table: str) -> None:
        super().__init__(
            f"no S3 object matching table '{table}' was found under the bucket root. "
            "Check S3_BUCKET/AWS_PROFILE in .env and that the organizer data is published."
        )
        self.table = table


@dataclass(frozen=True, slots=True)
class SourceObject:
    """One S3 object (a table may be split across several, e.g. daily partitions)."""

    key: str
    size: int


def is_data_file(key: str) -> bool:
    suffix = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    return suffix in {"csv", "parquet", "json", "jsonl", "ndjson"}


def matches_table(key: str, table: str) -> bool:
    """A key belongs to `table` if the table name appears as a path or file-name segment.

    Matches both flat layouts (`customers.csv`) and partitioned ones
    (`transactions/process_date=2026-06-01/part-0.parquet`), while `service_agents` does not
    accidentally match a hypothetical `agents` prefix (comparison is on whole segments).
    """
    if not is_data_file(key):
        return False
    stem = key.rsplit("/", 1)[-1].split(".", 1)[0]
    segments = {segment.split("=", 1)[0] for segment in key.split("/")[:-1]}
    return table in segments or stem == table or stem.startswith(f"{table}_")
