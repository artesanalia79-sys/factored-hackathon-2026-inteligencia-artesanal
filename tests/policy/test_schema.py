"""`policy/dispute_policy_v1.yaml` loads, validates and matches its own invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bankagent.policy.schema import (
    ELIGIBILITY_KINDS,
    ESCALATION_KINDS,
    POLICY_FILE,
    FraudScoreRule,
    PolicyConfig,
    RepeatDisputerRule,
    WindowRule,
    load_policy,
)


def test_the_committed_file_loads() -> None:
    config = load_policy()
    assert config.policy_version == "dispute-v1"
    assert len(config.rules) >= 5


def test_every_rule_id_is_unique_and_every_kind_is_eligibility_or_escalation() -> None:
    config = load_policy()
    ids = [rule.rule_id for rule in config.rules]
    assert len(set(ids)) == len(ids)
    assert {rule.kind for rule in config.rules} <= set(ELIGIBILITY_KINDS) | set(ESCALATION_KINDS)


def test_eligibility_and_escalation_partition_every_rule() -> None:
    config = load_policy()
    eligibility = {rule.rule_id for rule in config.eligibility_rules()}
    escalation = {rule.rule_id for rule in config.escalation_rules()}
    assert eligibility.isdisjoint(escalation)
    assert eligibility | escalation == {rule.rule_id for rule in config.rules}


def test_the_window_rule_has_the_three_fixture_countries() -> None:
    config = load_policy()
    window_rules = [rule for rule in config.rules if isinstance(rule, WindowRule)]
    assert len(window_rules) == 1
    assert set(window_rules[0].window_days) == {"AR", "MX", "CO"}


def test_the_fraud_score_rule_matches_adr_0003() -> None:
    config = load_policy()
    (rule,) = [rule for rule in config.rules if isinstance(rule, FraudScoreRule)]
    assert rule.threshold == 30.0
    assert rule.provenance.synthetic is False


def test_the_repeat_disputer_rule_is_labelled_synthetic_with_a_todo() -> None:
    config = load_policy()
    (rule,) = [rule for rule in config.rules if isinstance(rule, RepeatDisputerRule)]
    assert rule.provenance.synthetic is True
    assert rule.todo is not None


def test_the_window_rule_is_labelled_synthetic_pending_legal_verification() -> None:
    # AR, MX and CO are all synthetic until a human checks a primary source; see
    # docs/limitations.md and the TODO on this rule.
    config = load_policy()
    (rule,) = [rule for rule in config.rules if isinstance(rule, WindowRule)]
    assert rule.provenance.synthetic is True
    assert rule.todo is not None


def test_a_duplicate_rule_id_is_rejected(tmp_path) -> None:
    bad = tmp_path / "dup.yaml"
    bad.write_text(
        "policy_version: v\nsla_days: 1\nrules:\n"
        "  - {rule_id: X, kind: approved_status, description: a, explanation_key: k, "
        "provenance: {synthetic: true, source: s}}\n"
        "  - {rule_id: X, kind: no_open_case, description: a, explanation_key: k, "
        "provenance: {synthetic: true, source: s}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate rule_id"):
        load_policy(bad)


def test_an_unknown_field_is_rejected(tmp_path) -> None:
    bad = tmp_path / "extra.yaml"
    bad.write_text(
        "policy_version: v\nsla_days: 1\nrules:\n"
        "  - {rule_id: X, kind: approved_status, description: a, explanation_key: k, "
        "surprise: 1, provenance: {synthetic: true, source: s}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_policy(bad)


def test_the_file_path_points_at_the_committed_policy() -> None:
    assert POLICY_FILE.name == "dispute_policy_v1.yaml"
    assert POLICY_FILE.exists()


def test_a_policy_config_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate(
            {"policy_version": "v", "sla_days": 1, "rules": [], "surprise": True}
        )
