"""Serving DB (DuckDB, read-only) and operational store (SQLite) access.

`sqlite.Database` owns the SQLite connection and schema; `ops.OpsStore` holds sessions, login
challenges, confirmation tokens, disputes, card blocks, handoffs and execution records, every
read scoped to a customer or session; `console.HandoffConsole` has the unscoped reads for human
agents (never imported by the tools); `serving.ServingDB` reads the minimal customer profile
and, for the tools, the cards, transactions and prior complaints of one customer plus the agent
directory. The tools hold no SQL: every statement they run is here. Owner: Juan José (T7, T8, T19).
"""
