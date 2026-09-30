"""Bronze conversion: everything becomes a string column, shards concatenate, nulls survive."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from bankagent.ingest.bronze import (
    UnsupportedSourceFormat,
    concat_tables,
    read_source_table,
    write_bronze_table,
)


def test_csv_is_read_as_all_strings(tmp_path: Path) -> None:
    path = tmp_path / "customers.csv"
    path.write_text(
        "customer_id,credit_score,accepts_marketing\nCUST-1,720,true\nCUST-2,,false\n",
        encoding="utf-8",
    )

    table = read_source_table(path)
    assert table.schema.field("credit_score").type == "string"
    assert table.column("credit_score").to_pylist() == ["720", None]
    assert table.column("customer_id").to_pylist() == ["CUST-1", "CUST-2"]


def test_csv_values_are_kept_byte_for_byte(tmp_path: Path) -> None:
    """Regression: types used to be inferred and then cast to string, which turned
    response_code '00' into '0', 'True' into 'true' and '450.0' into '450'."""
    path = tmp_path / "transactions.csv"
    path.write_text(
        "response_code,is_fraud,amount,fraud_score,product_number,note\n"
        '00,True,450.0,11.0,0042,"a, b"\n'
        "05,False,1027.50,,0001,NA\n",
        encoding="utf-8",
    )

    table = read_source_table(path)
    assert {field.type for field in table.schema} == {"string"}
    assert table.to_pydict() == {
        "response_code": ["00", "05"],
        "is_fraud": ["True", "False"],
        "amount": ["450.0", "1027.50"],
        "fraud_score": ["11.0", None],
        "product_number": ["0042", "0001"],
        "note": ["a, b", None],
    }


def test_csv_header_with_bom_keeps_string_types(tmp_path: Path) -> None:
    path = tmp_path / "codes.csv"
    path.write_bytes(b"\xef\xbb\xbfresponse_code,amount\n00,1.0\n")

    table = read_source_table(path)
    assert table.to_pydict() == {"response_code": ["00"], "amount": ["1.0"]}


def test_ndjson_is_read_as_all_strings(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        '{"event_id": "E1", "duration_seconds": 12}\n'
        '{"event_id": "E2", "duration_seconds": null}\n',
        encoding="utf-8",
    )
    table = read_source_table(path)
    assert table.column("duration_seconds").to_pylist() == ["12", None]


def test_single_json_object_is_wrapped_in_a_list(tmp_path: Path) -> None:
    path = tmp_path / "one.json"
    path.write_text(json.dumps({"a": 1, "b": "x"}), encoding="utf-8")
    table = read_source_table(path)
    assert table.num_rows == 1
    assert table.column("a").to_pylist() == ["1"]


def test_parquet_columns_are_cast_to_strings(tmp_path: Path) -> None:
    import pyarrow as pa

    source = pa.table({"amount": pa.array([1, 2], type=pa.int64())})
    path = tmp_path / "source.parquet"
    pq.write_table(source, path)

    table = read_source_table(path)
    assert table.column("amount").to_pylist() == ["1", "2"]


def test_unsupported_extension_raises() -> None:
    with pytest.raises(UnsupportedSourceFormat):
        read_source_table(Path("data.xlsx"))


def test_concat_tables_single_is_returned_as_is(tmp_path: Path) -> None:
    path = tmp_path / "a.csv"
    path.write_text("x\n1\n", encoding="utf-8")
    table = read_source_table(path)
    assert concat_tables([table]) is table


def test_concat_tables_aligns_differing_schemas(tmp_path: Path) -> None:
    a = tmp_path / "a.csv"
    a.write_text("x,y\n1,2\n", encoding="utf-8")
    b = tmp_path / "b.csv"
    b.write_text("x,z\n3,4\n", encoding="utf-8")

    combined = concat_tables([read_source_table(a), read_source_table(b)])
    assert sorted(combined.schema.names) == ["x", "y", "z"]
    assert combined.num_rows == 2
    as_dict = combined.to_pydict()
    assert as_dict["x"] == ["1", "3"]
    assert as_dict["y"] == ["2", None]
    assert as_dict["z"] == [None, "4"]


def test_write_bronze_table_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "a.csv"
    path.write_text("x\n1\n", encoding="utf-8")
    table = read_source_table(path)

    dest = tmp_path / "bronze" / "nested" / "customers.parquet"
    write_bronze_table(table, dest)
    assert dest.exists()
    assert pq.read_table(dest).num_rows == 1
