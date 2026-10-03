"""`poe policy-explain`: prints a decision and the rules it names, on the real fixture bank."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from bankagent.contracts.domain import DisputeCase
from bankagent.contracts.enums import DisputeReason, DisputeStatus
from bankagent.policy.cli import main
from bankagent.store.ops import OpsStore


def _explain(capsys: pytest.CaptureFixture[str], *argv: str) -> str:
    assert main(list(argv)) == 0
    return capsys.readouterr().out


def _line(out: str, rule_id: str) -> str:
    (line,) = [line for line in out.splitlines() if line.strip().startswith(rule_id)]
    return line


def test_it_explains_a_proceed_decision_rule_by_rule(
    fixture_bank: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _explain(
        capsys,
        "CUST-FX-001",
        "TXN-FX-0101",
        "--bank",
        str(fixture_bank),
        "--filed-on",
        "2026-10-03",
    )
    assert "decision: proceed" in out
    assert "allowed_actions: create_dispute, block_card" in out
    assert "sla_due_date: 2026-11-02" in out
    # Every rule is listed with its own provenance label, and on a proceed every rule passed.
    assert "[internal]" in _line(out, "DSP-ELIG-01")
    assert "[synthetic]" in _line(out, "DSP-WIN-01")
    assert "[verified]" in _line(out, "DSP-ESC-01")
    assert "[synthetic]" in _line(out, "DSP-ESC-02")
    assert "[internal]" in _line(out, "DSP-ACT-01")
    assert "fired:" not in out
    assert out.count("    passed") == 7
    assert "TODO(T9, Juan José)" in out  # the window placeholder carries its TODO


def test_it_explains_an_escalation_with_the_key_that_fired(
    fixture_bank: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _explain(capsys, "CUST-FX-006", "TXN-FX-0601", "--bank", str(fixture_bank))
    assert "decision: escalate" in out
    assert "allowed_actions: none" in out
    assert "[verified]" in _line(out, "DSP-ESC-01")
    assert "fired: dispute.fraud_risk" in out
    assert "sla_due_date" not in out


def test_it_reads_the_agents_own_disputes_from_the_ops_store(
    fixture_bank: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ops = tmp_path / "ops.sqlite"
    with OpsStore(ops) as store:
        store.insert_dispute(
            "CUST-FX-002",
            DisputeCase(
                dispute_id="DSP-TEST-0001",
                transaction_id="TXN-FX-0201",
                reason=DisputeReason.UNRECOGNIZED,
                status=DisputeStatus.SUBMITTED,
                created_at=datetime(2026, 10, 3, 9, tzinfo=UTC),
                amount=Decimal("859.00"),
                currency="COP",
                idempotency_key="idem-cli-0001",
                policy_version="test-v1",
            ),
        )
    args = ("CUST-FX-002", "TXN-FX-0202", "--bank", str(fixture_bank), "--filed-on", "2026-10-03")
    assert "decision: proceed" in _explain(capsys, *args)
    out = _explain(capsys, *args, "--ops-store", str(ops))
    assert "decision: escalate" in out
    assert "fired: dispute.repeat_disputer" in out
    # A year later the same agent dispute no longer makes a repeat disputer.
    later = _explain(capsys, *args[:-1], "2027-10-04", "--ops-store", str(ops))
    assert "decision: proceed" in later


def test_it_exits_for_an_unknown_or_foreign_transaction(fixture_bank: Path) -> None:
    for transaction_id in ("TXN-DOES-NOT-EXIST", "TXN-FX-0601"):
        with pytest.raises(SystemExit, match="no transaction"):
            main(["CUST-FX-001", transaction_id, "--bank", str(fixture_bank)])
