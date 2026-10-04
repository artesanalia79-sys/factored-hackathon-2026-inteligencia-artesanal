"""Offline evaluation of the learned intent router (Task 18).

``evaluate`` scores the router of ``bankagent.router`` against the keyword router on grouped
splits of the synthetic corpus; ``report`` writes ``docs/evidence/router_eval.md``; ``tracking``
logs an MLflow run; ``cli`` is ``uv run poe router`` / ``uv run poe router-predict``. It lives in
the harness because production code never imports ``bankagent.eval``, while ``bankagent.router``
stays importable by the agent.
"""
