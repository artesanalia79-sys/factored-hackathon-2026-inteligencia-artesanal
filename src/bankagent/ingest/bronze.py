"""Convert raw downloaded files into one bronze parquet file per table.

Bronze is a faithful copy: every column is read and written as a string (`pyarrow.string()`).
No business logic, no type coercion, no dropped rows — that is the silver layer's job (Task 5).
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq


class UnsupportedSourceFormat(ValueError):
    def __init__(self, path: Path) -> None:
        super().__init__(f"don't know how to read {path.name}: unsupported extension")


def _read_csv_as_strings(path: Path) -> pa.Table:
    table = pa_csv.read_csv(
        path,
        convert_options=pa_csv.ConvertOptions(
            column_types={}, strings_can_be_null=True, null_values=["", "NULL", "null", "NA"]
        ),
    )
    return table.cast(pa.schema([pa.field(name, pa.string()) for name in table.schema.names]))


def _read_json_as_strings(path: Path) -> pa.Table:
    text = path.read_text(encoding="utf-8")
    records = (
        [json.loads(line) for line in text.splitlines() if line.strip()]
        if "\n" in text.strip()
        else json.loads(text)
    )
    if isinstance(records, dict):
        records = [records]
    columns = sorted({key for record in records for key in record})
    arrays = [
        pa.array(
            [None if record.get(col) is None else str(record.get(col)) for record in records],
            type=pa.string(),
        )
        for col in columns
    ]
    return pa.table(arrays, names=columns)


def read_source_table(path: Path) -> pa.Table:
    """Read one raw file (CSV, JSON/NDJSON or parquet) as an all-string Arrow table."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _read_csv_as_strings(path)
    if suffix in {".json", ".jsonl", ".ndjson"}:
        return _read_json_as_strings(path)
    if suffix == ".parquet":
        table = pq.read_table(path)
        return table.cast(pa.schema([pa.field(name, pa.string()) for name in table.schema.names]))
    raise UnsupportedSourceFormat(path)


def concat_tables(tables: list[pa.Table]) -> pa.Table:
    """Concatenate same-table shards (e.g. daily partitions), promoting a shared string schema."""
    if len(tables) == 1:
        return tables[0]
    columns = sorted({name for table in tables for name in table.schema.names})
    aligned = []
    for table in tables:
        missing = [name for name in columns if name not in table.schema.names]
        for name in missing:
            table = table.append_column(name, pa.array([None] * table.num_rows, type=pa.string()))
        aligned.append(table.select(columns))
    return pa.concat_tables(aligned)


def write_bronze_table(table: pa.Table, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, dest)
