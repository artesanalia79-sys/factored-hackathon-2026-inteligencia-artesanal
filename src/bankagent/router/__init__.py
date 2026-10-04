"""Learned intent router with split-conformal abstention (Task 18). Owner: Juan José + Santiago.

``model`` (TF-IDF character n-grams and logistic regression), ``conformal`` and ``corpus`` (the
synthetic training messages in ``eval/router/``). Offline today: the agent's router gate is still
the keyword attack gate. The evaluation against the keyword router lives in the harness,
``bankagent.eval.router`` (``uv run poe router``); model card: ``docs/models/router.md``.
"""
