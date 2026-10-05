"""Console contracts (T21) against the real policy file, not just hand-picked examples."""

from __future__ import annotations

from bankagent.contracts.console import RuleExplanation
from bankagent.policy.schema import load_policy


def test_every_real_rule_description_fits_a_rule_explanation() -> None:
    """Regression: a 500-char cap once rejected DSP-ESC-02's real description (741 chars) and
    crashed the whole disputes list for any case it was attached to."""
    for rule in load_policy().rules:
        RuleExplanation(rule_id=rule.rule_id, description=rule.description)
