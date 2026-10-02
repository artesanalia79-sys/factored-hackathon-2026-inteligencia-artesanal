"""`poe policy-explain`: prints a decision and the rules it names, on the real fixture bank."""

from __future__ import annotations

from pathlib import Path

import pytest

from bankagent.policy.cli import main


def test_it_explains_a_proceed_decision(
    fixture_bank: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["CUST-FX-001", "TXN-FX-0101", "--bank", str(fixture_bank)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "decision: proceed" in out
    assert "DSP-ELIG-01" in out
    assert "sla_due_date:" in out


def test_it_explains_an_escalate_decision_with_its_provenance(
    fixture_bank: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["CUST-FX-006", "TXN-FX-0601", "--bank", str(fixture_bank)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "decision: escalate" in out
    assert "DSP-ESC-01" in out
    assert "verified" in out  # the fraud rule is provenance.synthetic: false


def test_it_exits_for_an_unknown_transaction(fixture_bank: Path) -> None:
    with pytest.raises(SystemExit):
        main(["CUST-FX-001", "TXN-DOES-NOT-EXIST", "--bank", str(fixture_bank)])
