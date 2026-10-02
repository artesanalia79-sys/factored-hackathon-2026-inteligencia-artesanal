"""Serving DB (DuckDB, read-only) and operational store (SQLite) access. Owner: Victor (T7, T19).

`ops.OpsStore` holds sessions, login challenges, confirmation tokens, disputes, card blocks,
handoffs and execution records; `serving.ServingDB` reads the minimal customer profile.
"""
