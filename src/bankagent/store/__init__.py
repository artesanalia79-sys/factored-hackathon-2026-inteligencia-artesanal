"""Serving DB (DuckDB, read-only) and operational store (SQLite) access. Owner: Juan José (T7, T19).

`sqlite.Database` owns the SQLite connection and schema; `ops.OpsStore` holds sessions, login
challenges, confirmation tokens, disputes, card blocks, handoffs and execution records, every
read scoped to a customer or session; `console.HandoffConsole` has the unscoped reads for human
agents (never imported by the tools); `serving.ServingDB` reads the minimal customer profile.
"""
