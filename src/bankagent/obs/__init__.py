"""Execution records, tracing, cost accounting and PII redaction.

Owner: Santiago (T20 minimum: redaction of personal data and secrets in logs, ``redaction``).
Tracing, cost accounting and the full fallback matrix are T29.
"""

from bankagent.obs.redaction import install_log_redaction, redact

__all__ = ["install_log_redaction", "redact"]
