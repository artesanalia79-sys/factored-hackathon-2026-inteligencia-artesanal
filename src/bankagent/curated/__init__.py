"""The agent end to end on the curated serving DB, locally (Task 19).

``uv run poe curated-e2e`` runs the production app (``create_default_app``) with
``DATA_MODE=curated`` and drives it over HTTP for real customers of the serving DB that
``uv run poe serving-build`` builds from organizer data. `cases.py` picks the cases and works
out what each one must end in, without the policy engine; `run.py` opens each session in code
(no login can return a code on organizer data), talks to the agent and checks every reply,
record and write; `report.py` writes the committed evidence, which holds counts and step names
only, never a value from a row. Owner: Juan José (T19).
"""
