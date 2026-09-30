"""Evaluation harness (Task 12, owner: Santiago).

Scores the proposed agent and the LLM-only baseline on identical cases, tools and budgets, from
``ExecutionRecord``s and harness-side observations, never from the assistant's text alone.

Modules:

- ``bank``: ground truth about the synthetic bank (owners, segments, PII) used by the scorer.
- ``cases``: load ``EvalCase`` YAML files (refuses the sealed held-out set).
- ``system``: the ``System`` interface every runner implements, and ``ToolObservation``.
- ``tools``: wraps real tools so the harness observes every call and injects tool faults.
- ``simulator``: the scripted user that answers the agent from the case ``FactSheet``.
- ``fake``: a scripted fake system (stands in for Task 13 and exercises every detector).
- ``adapters``: adapters from the Task 13 turn function to ``System``.
- ``runner``: runs cases through a system and collects ``CaseTrace``s.
- ``detectors``: deterministic ES/PT detectors for action claims and PII in replies.
- ``scorer``: ``CaseTrace`` -> ``EvalResult`` with every ``UnsafeEvent``.
- ``metrics``, ``gates``, ``report``: case-level metrics with Wilson CIs, gates and the report.
- ``cli``: ``python -m bankagent.eval.cli smoke`` (``uv run poe eval-smoke``).

Production code must never import this package. Rules: ``docs/rules/eval.md``.
"""
