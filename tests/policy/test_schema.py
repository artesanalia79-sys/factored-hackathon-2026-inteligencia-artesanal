"""`policy/dispute_policy_v1.yaml` loads, validates and matches its own invariants."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from bankagent.policy.schema import (
    ACTION_KINDS,
    ELIGIBILITY_KINDS,
    ESCALATION_KINDS,
    POLICY_FILE,
    CardBlockableRule,
    DqFlagRule,
    FraudScoreRule,
    PolicyConfig,
    Provenance,
    RepeatDisputerRule,
    WindowRule,
    load_policy,
)


def _committed() -> dict[str, Any]:
    with POLICY_FILE.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _rule(data: dict[str, Any], kind: str) -> dict[str, Any]:
    (rule,) = [rule for rule in data["rules"] if rule["kind"] == kind]
    return rule


def test_the_committed_file_loads_with_one_rule_of_every_kind() -> None:
    config = load_policy()
    assert config.policy_version == "dispute-v1.1"
    kinds = sorted(rule.kind for rule in config.rules)
    assert kinds == sorted(ELIGIBILITY_KINDS + ESCALATION_KINDS + ACTION_KINDS)


def test_eligibility_escalation_and_action_rules_partition_every_rule() -> None:
    config = load_policy()
    groups = [
        {rule.rule_id for rule in group}
        for group in (config.eligibility_rules(), config.escalation_rules(), config.action_rules())
    ]
    assert sum(len(group) for group in groups) == len(config.rules)
    assert set().union(*groups) == {rule.rule_id for rule in config.rules}


def test_the_window_rule_has_the_three_fixture_countries() -> None:
    (rule,) = [rule for rule in load_policy().rules if isinstance(rule, WindowRule)]
    assert set(rule.window_days) == {"AR", "MX", "CO"}


def test_the_fraud_score_rule_matches_adr_0003() -> None:
    (rule,) = [rule for rule in load_policy().rules if isinstance(rule, FraudScoreRule)]
    assert rule.threshold == 30.0
    assert rule.provenance.label == "verified"


def test_the_dq_rule_never_escalates_a_possible_duplicate() -> None:
    (rule,) = [rule for rule in load_policy().rules if isinstance(rule, DqFlagRule)]
    assert "dq_possible_duplicate" not in rule.flags
    assert "dq_txn_after_card_expiry" not in rule.flags


def test_only_an_active_card_is_blockable() -> None:
    (rule,) = [rule for rule in load_policy().rules if isinstance(rule, CardBlockableRule)]
    assert [status.value for status in rule.statuses] == ["Active"]


def test_every_placeholder_is_labelled_synthetic_with_a_todo() -> None:
    config = load_policy()
    labels = {rule.rule_id: rule.provenance.label for rule in config.rules}
    assert labels == {
        "DSP-ELIG-01": "internal",
        "DSP-ELIG-02": "internal",
        "DSP-WIN-01": "synthetic",
        "DSP-ESC-01": "verified",
        "DSP-ESC-02": "synthetic",
        "DSP-ESC-03": "synthetic",
        "DSP-ACT-01": "internal",
    }
    for rule in config.rules:
        if rule.provenance.synthetic:
            assert rule.todo is not None, rule.rule_id
            assert rule.todo.startswith("TODO(T9, "), rule.rule_id
    repeat = [rule for rule in config.rules if isinstance(rule, RepeatDisputerRule)]
    assert repeat[0].window_days == 90


def test_the_file_path_points_at_the_committed_policy() -> None:
    assert POLICY_FILE.name == "dispute_policy_v1.yaml"
    assert POLICY_FILE.exists()


# -- what the loader refuses ----------------------------------------------------------------


def test_a_duplicate_rule_id_is_rejected(tmp_path: Path) -> None:
    data = _committed()
    data["rules"][1]["rule_id"] = data["rules"][0]["rule_id"]
    with pytest.raises(ValidationError, match="duplicate rule_id"):
        load_policy(_write(tmp_path, data))


def test_a_missing_kind_is_rejected(tmp_path: Path) -> None:
    data = _committed()
    data["rules"] = [rule for rule in data["rules"] if rule["kind"] != "window"]
    with pytest.raises(ValidationError, match="exactly one rule of each kind"):
        load_policy(_write(tmp_path, data))


def test_a_kind_given_twice_is_rejected(tmp_path: Path) -> None:
    data = _committed()
    extra = dict(_rule(data, "approved_status"), rule_id="DSP-ELIG-09")
    data["rules"].append(extra)
    with pytest.raises(ValidationError, match="exactly one rule of each kind"):
        load_policy(_write(tmp_path, data))


@pytest.mark.parametrize("rule_id", ["X", "DSP-ELIG-1", "dsp-elig-01", "DSP-ELIG-001"])
def test_a_rule_id_off_the_pattern_is_rejected(tmp_path: Path, rule_id: str) -> None:
    data = _committed()
    data["rules"][0]["rule_id"] = rule_id
    with pytest.raises(ValidationError):
        load_policy(_write(tmp_path, data))


@pytest.mark.parametrize(
    ("kind", "field", "value"),
    [
        ("window", "window_days", {"MX": -5}),
        ("window", "window_days", {"MX": 0}),
        ("window", "window_days", {}),
        ("repeat_disputer", "window_days", 0),
        ("fraud_score", "threshold", 101.0),
        ("fraud_score", "threshold", -1.0),
        ("dq_flag", "flags", []),
        ("card_blockable", "statuses", []),
        ("card_blockable", "statuses", ["Lost"]),
    ],
)
def test_an_out_of_range_parameter_is_rejected(
    tmp_path: Path, kind: str, field: str, value: object
) -> None:
    data = _committed()
    _rule(data, kind)[field] = value
    with pytest.raises(ValidationError):
        load_policy(_write(tmp_path, data))


def test_an_unknown_field_is_rejected(tmp_path: Path) -> None:
    data = _committed()
    data["rules"][0]["surprise"] = 1
    with pytest.raises(ValidationError):
        load_policy(_write(tmp_path, data))


def test_a_policy_config_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate(
            {"policy_version": "v", "sla_days": 1, "rules": [], "surprise": True}
        )


# -- provenance labels ----------------------------------------------------------------------


def test_provenance_defaults_to_synthetic() -> None:
    assert Provenance(source="s").label == "synthetic"


def test_a_verified_label_needs_who_and_when() -> None:
    with pytest.raises(ValidationError, match="verified_by and verified_on"):
        Provenance(synthetic=False, source="s")
    with pytest.raises(ValidationError, match="verified_by and verified_on"):
        Provenance(synthetic=False, source="s", verified_by="someone")
    verified = Provenance(
        synthetic=False, source="s", verified_by="someone", verified_on=date(2026, 9, 29)
    )
    assert verified.label == "verified"


def test_an_internal_rule_is_neither_synthetic_nor_verified() -> None:
    assert Provenance(synthetic=False, internal=True, source="s").label == "internal"
    with pytest.raises(ValidationError, match="internal rule is not synthetic"):
        Provenance(synthetic=True, internal=True, source="s")
