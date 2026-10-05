"""Export dev-only replays from scored runs; never expose the sealed evaluation in the UI."""

from collections.abc import Sequence

from bankagent.contracts.comparison import (
    ComparisonBundle,
    ComparisonRun,
    ComparisonStep,
    ComparisonTurn,
)
from bankagent.contracts.enums import EvalSplit
from bankagent.eval.scorer import ScoredRun
from bankagent.obs.redaction import redact


def build_comparison(
    runs: Sequence[ScoredRun],
    *,
    suite_id: str,
    case_set_sha256: str,
    simulated: bool,
    cost_assumptions: str,
) -> ComparisonBundle:
    """Allowlist fields, redact dialogue, and reject non-dev material before serialization."""
    if any(run.trace.case.split != EvalSplit.DEV for run in runs):
        raise ValueError("comparison export accepts dev cases only")
    return ComparisonBundle(
        schema_version=1,
        suite_id=suite_id,
        case_set_sha256=case_set_sha256,
        simulated=simulated,
        cost_assumptions=cost_assumptions,
        runs=tuple(
            ComparisonRun(
                system_name=run.trace.system_name,
                result=run.result,
                turns=tuple(
                    ComparisonTurn(
                        user_text=redact(turn.user_text),
                        reply_text=redact(turn.reply_text),
                        steps=tuple(
                            ComparisonStep(
                                step=r.step,
                                state=r.state,
                                tool=r.tool,
                                outcome=r.outcome,
                                verified=r.verified,
                                rule_ids=r.rule_ids,
                                model=r.model,
                            )
                            for r in turn.records
                        ),
                    )
                    for turn in run.trace.turns
                ),
            )
            for run in runs
        ),
    )
