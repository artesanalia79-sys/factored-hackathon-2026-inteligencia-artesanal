"""Pick the cases of the curated end-to-end run and the outcome each one must reach (T19).

Every case is real: an active customer of the curated serving DB and one of their card
transactions. What the agent must do with it is worked out here, from the row and the
parameters of ``policy/dispute_policy_v1.yaml`` (windows, threshold, flags, statuses), with
this module's own SQL and a few lines of Python: ``bankagent.policy.engine`` and
``build_inputs`` are not used. ``run.py`` then checks that the agent reached it over HTTP, so a
mistake could only go unnoticed if it were made twice, in two different ways.

Picks are deterministic for one serving DB and seed (ordered by an md5 of the seed and the
transaction id), and each customer is used once, so no case sees another case's writes. The
values of a case (ids, amounts, merchants) are organizer data: they stay in memory and in the
git-ignored transcripts, never in the committed report.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

import duckdb

from bankagent.contracts.enums import DecisionType, Language, Specialty, TransactionStatus
from bankagent.policy.schema import (
    ApprovedStatusRule,
    CardBlockableRule,
    DqFlagRule,
    FraudScoreRule,
    NoOpenCaseRule,
    PolicyConfig,
    RepeatDisputerRule,
    Rule,
    WindowRule,
)
from bankagent.render.templates import MAX_CANDIDATES


class Scenario(StrEnum):
    """What the scripted customer does. Picked in this order: the rarest rows first."""

    ESCALATE_FRAUD = "escalate_fraud"
    ESCALATE_REPEAT = "escalate_repeat"
    ESCALATE_DATA = "escalate_data"
    CHOOSE_AMONG_MATCHES = "choose_among_matches"
    DISPUTE_CARD_NOT_ACTIVE = "dispute_card_not_active"
    DISPUTE_BLOCK_DECLINED = "dispute_block_declined"
    DISPUTE_BLOCK_ACCEPTED = "dispute_block_accepted"
    ALREADY_DISPUTED = "already_disputed"
    RECOGNIZED = "recognized"
    INELIGIBLE_WINDOW = "ineligible_window"
    INELIGIBLE_STATUS = "ineligible_status"
    OTHER_CUSTOMERS_CHARGE = "other_customers_charge"


# The pool of rows each scenario picks from (`family` in _POOL_SQL). Every pool holds only
# charges whose amount no other transaction of the customer has, except the "twins", so the
# amount alone finds the charge the customer means.
_FAMILY: dict[Scenario, str] = {
    Scenario.ESCALATE_FRAUD: "fraud",
    Scenario.ESCALATE_REPEAT: "repeat",
    Scenario.ESCALATE_DATA: "flagged",
    Scenario.CHOOSE_AMONG_MATCHES: "twins",
    Scenario.DISPUTE_CARD_NOT_ACTIVE: "unblockable",
    Scenario.DISPUTE_BLOCK_DECLINED: "recent",
    Scenario.DISPUTE_BLOCK_ACCEPTED: "recent",
    Scenario.ALREADY_DISPUTED: "recent",
    Scenario.RECOGNIZED: "recent",
    Scenario.INELIGIBLE_WINDOW: "old",
    Scenario.INELIGIBLE_STATUS: "unsettled",
    Scenario.OTHER_CUSTOMERS_CHARGE: "recent",
}

FAMILIES = tuple(sorted(set(_FAMILY.values())))

# One constant statement; the family and every threshold are bound parameters. The repeat-
# disputer history and the open prior complaints are restated here from their definitions
# (`dispute_history`: category Transactions, case type Complaint or Claim; statuses Open,
# In Process, Escalated), not imported from the store.
_POOL_SQL = """
WITH history AS (
    SELECT customer_id, max(created_at) AS last_dispute_at
    FROM dispute_history
    WHERE category = 'Transactions' AND case_type IN ('Complaint', 'Claim')
    GROUP BY customer_id
),
open_cases AS (
    SELECT DISTINCT customer_id, related_transaction_id AS transaction_id
    FROM dispute_history
    WHERE related_transaction_id IS NOT NULL AND status IN ('Open', 'In Process', 'Escalated')
),
amounts AS (
    SELECT customer_id, amount, count(*) AS same_amount, count(DISTINCT currency) AS currencies
    FROM transactions_enriched
    GROUP BY customer_id, amount
),
candidates AS (
    SELECT t.transaction_id, t.customer_id, t.product_id, p.country, p.preferred_language,
           t.transaction_status, t.transaction_ts, t.transaction_type, t.amount, t.currency,
           t.merchant_name, k.card_last4, k.product_status, t.fraud_score, t.dq_flags,
           h.last_dispute_at, o.transaction_id IS NOT NULL AS open_case, a.same_amount,
           a.currencies,
           row_number() OVER (
               PARTITION BY t.customer_id, t.amount
               ORDER BY t.transaction_ts DESC, t.transaction_id
           ) AS position,
           date_diff('day', CAST(t.transaction_ts AS DATE), $as_of::DATE) AS age_days
    FROM transactions_enriched AS t
    JOIN customer_profile_min AS p ON p.customer_id = t.customer_id
    JOIN customer_cards AS k ON k.product_id = t.product_id AND k.customer_id = t.customer_id
    JOIN amounts AS a ON a.customer_id = t.customer_id AND a.amount = t.amount
    LEFT JOIN history AS h ON h.customer_id = t.customer_id
    LEFT JOIN open_cases AS o
           ON o.customer_id = t.customer_id AND o.transaction_id = t.transaction_id
    WHERE p.customer_status = 'Active'
)
SELECT transaction_id, customer_id, product_id, country, preferred_language, transaction_status,
       transaction_ts, transaction_type, amount, currency, merchant_name, card_last4,
       product_status, fraud_score, dq_flags, last_dispute_at, open_case, same_amount, position
FROM candidates
WHERE CASE $family
    WHEN 'recent' THEN transaction_status = 'Approved' AND age_days <= $recent_days
                       AND same_amount = 1
    WHEN 'fraud' THEN transaction_status = 'Approved' AND age_days <= $recent_days
                      AND same_amount = 1 AND fraud_score >= $threshold
    WHEN 'repeat' THEN transaction_status = 'Approved' AND age_days <= $recent_days
                       AND same_amount = 1
                       AND date_diff('day', CAST(last_dispute_at AS DATE), $as_of::DATE)
                           <= $repeat_days
    WHEN 'flagged' THEN transaction_status = 'Approved' AND age_days <= $recent_days
                        AND same_amount = 1 AND list_has_any(dq_flags, $flags)
    WHEN 'unblockable' THEN transaction_status = 'Approved' AND age_days <= $recent_days
                            AND same_amount = 1
                            AND NOT list_contains($blockable, product_status)
    WHEN 'old' THEN transaction_status = 'Approved' AND age_days > $old_days AND same_amount = 1
    WHEN 'unsettled' THEN transaction_status <> 'Approved' AND same_amount = 1
    WHEN 'twins' THEN same_amount BETWEEN 2 AND $max_choices AND currencies = 1
    ELSE false
END
ORDER BY md5($seed || ':' || transaction_id), transaction_id
LIMIT $pool
"""

# How many of a customer's transactions have exactly this amount (in any currency).
_SAME_AMOUNT_SQL = """
SELECT count(*) FROM transactions_enriched WHERE customer_id = $customer_id AND amount = $amount
"""


@dataclass(frozen=True, slots=True)
class Charge:
    """One card transaction as the cases see it. Organizer data: never written to the report.

    ``transaction_ts`` and ``last_dispute_at`` are naive UTC, as stored. ``position`` is the
    charge's place among the customer's charges with the same amount, newest first: the order
    the agent lists them in.
    """

    transaction_id: str
    customer_id: str
    product_id: str
    country: str
    language: Language
    status: str
    transaction_ts: datetime
    transaction_type: str
    amount: Decimal
    currency: str
    merchant_name: str | None
    card_last4: str
    card_status: str
    fraud_score: float | None
    dq_flags: tuple[str, ...]
    last_dispute_at: datetime | None
    open_case: bool
    same_amount: int
    position: int


@dataclass(frozen=True, slots=True)
class Expected:
    """What the agent must do once the customer says the charge is not theirs.

    ``decision`` is ``None`` where no policy step runs. ``rule_ids`` is exactly what the
    policy record must carry: the failing rule (ineligible), the rules that fired (escalate),
    or every rule checked, in the file's order (proceed).
    """

    decision: DecisionType | None
    rule_ids: tuple[str, ...] = ()
    explanation_key: str | None = None
    block_offered: bool = False
    specialty: Specialty | None = None


@dataclass(frozen=True, slots=True)
class CuratedCase:
    """One case: who is logged in, the charge they talk about, and what must happen.

    ``customer_id`` is the session customer. It owns ``charge`` except in
    ``other_customers_charge``, where the charge is another customer's and must stay out of
    reach.
    """

    case_id: str
    scenario: Scenario
    customer_id: str
    language: Language
    charge: Charge
    expected: Expected


def expect(
    charge: Charge, policy: PolicyConfig, as_of: date, *, already_disputed: bool = False
) -> Expected:
    """The decision ``policy`` gives ``charge``, from the row and the file's parameters only.

    ``already_disputed``: the agent filed a dispute on this charge earlier in the run.
    """
    for rule in policy.eligibility_rules():
        if _ineligible(rule, charge, as_of, already_disputed=already_disputed):
            return Expected(
                DecisionType.INELIGIBLE, (rule.rule_id,), explanation_key=rule.explanation_key
            )
    fired = tuple(rule for rule in policy.escalation_rules() if _escalates(rule, charge, as_of))
    if fired:
        fraud = any(isinstance(rule, FraudScoreRule) for rule in fired)
        return Expected(
            DecisionType.ESCALATE,
            tuple(rule.rule_id for rule in fired),
            specialty=Specialty.FRAUD if fraud else Specialty.DISPUTES,
        )
    actions = policy.action_rules()
    checked = (*policy.eligibility_rules(), *policy.escalation_rules(), *actions)
    return Expected(
        DecisionType.PROCEED,
        tuple(rule.rule_id for rule in checked),
        block_offered=all(_blockable(rule, charge) for rule in actions),
    )


def _ineligible(rule: Rule, charge: Charge, as_of: date, *, already_disputed: bool) -> bool:
    if isinstance(rule, ApprovedStatusRule):
        return charge.status != TransactionStatus.APPROVED
    if isinstance(rule, NoOpenCaseRule):
        return charge.open_case or already_disputed
    if isinstance(rule, WindowRule):
        limit = rule.window_days.get(charge.country)
        return limit is None or (as_of - charge.transaction_ts.date()).days > limit
    raise TypeError(f"{rule.kind} is not an eligibility rule")


def _escalates(rule: Rule, charge: Charge, as_of: date) -> bool:
    if isinstance(rule, FraudScoreRule):
        return charge.fraud_score is not None and charge.fraud_score >= rule.threshold
    if isinstance(rule, RepeatDisputerRule):
        last = charge.last_dispute_at
        return last is not None and (as_of - last.date()).days <= rule.window_days
    if isinstance(rule, DqFlagRule):
        return any(flag in rule.flags for flag in charge.dq_flags)
    raise TypeError(f"{rule.kind} is not an escalation rule")


def _blockable(rule: Rule, charge: Charge) -> bool:
    if isinstance(rule, CardBlockableRule):
        return charge.card_status in {status.value for status in rule.statuses}
    raise TypeError(f"{rule.kind} is not an action rule")


def _fits(scenario: Scenario, expected: Expected, policy: PolicyConfig) -> bool:
    """Whether a charge with this expected outcome shows what ``scenario`` is about."""
    decision, ids = expected.decision, expected.rule_ids
    fraud = _rule(policy, FraudScoreRule).rule_id
    repeat = _rule(policy, RepeatDisputerRule).rule_id
    flag = _rule(policy, DqFlagRule).rule_id
    if scenario == Scenario.ESCALATE_FRAUD:
        return decision == DecisionType.ESCALATE and fraud in ids
    if scenario == Scenario.ESCALATE_REPEAT:
        return decision == DecisionType.ESCALATE and repeat in ids and fraud not in ids
    if scenario == Scenario.ESCALATE_DATA:
        return decision == DecisionType.ESCALATE and ids == (flag,)
    if scenario == Scenario.INELIGIBLE_WINDOW:
        return ids == (_rule(policy, WindowRule).rule_id,)
    if scenario == Scenario.INELIGIBLE_STATUS:
        return ids == (_rule(policy, ApprovedStatusRule).rule_id,)
    if scenario == Scenario.DISPUTE_CARD_NOT_ACTIVE:
        return decision == DecisionType.PROCEED and not expected.block_offered
    if scenario in {
        Scenario.DISPUTE_BLOCK_DECLINED,
        Scenario.DISPUTE_BLOCK_ACCEPTED,
        Scenario.ALREADY_DISPUTED,
    }:
        return decision == DecisionType.PROCEED and expected.block_offered
    # Choosing among matches is checked against the other charges of the amount (`tells_apart`);
    # a recognized charge and another customer's charge never reach the policy.
    return True


def tells_apart(charge: Charge, twins: Sequence[Charge], policy: PolicyConfig, as_of: date) -> bool:
    """Whether a ``choose_among_matches`` case on ``charge`` shows which charge the agent took.

    ``twins`` are the customer's other charges of the same amount. At least one charge of the
    amount must not be refused, so the scenario reaches a step that names a charge: a dispute
    or a handoff, whose charge the run checks. Taking the wrong one must then end differently:
    a dispute or a handoff names the charge it was made for, and a refusal must differ from
    the outcome of every twin. ``False`` when ``twins`` is not exactly the rest of the group
    (``same_amount``): a twin that is not looked at cannot be compared.
    """
    if len(twins) != charge.same_amount - 1:
        return False
    mine = expect(charge, policy, as_of)
    theirs = [expect(twin, policy, as_of) for twin in twins]
    if all(outcome.decision == DecisionType.INELIGIBLE for outcome in (mine, *theirs)):
        return False
    if mine.decision in {DecisionType.PROCEED, DecisionType.ESCALATE}:
        return True
    return mine not in theirs


def _charge(row: tuple[Any, ...]) -> Charge:
    (
        transaction_id,
        customer_id,
        product_id,
        country,
        language,
        status,
        transaction_ts,
        transaction_type,
        amount,
        currency,
        merchant_name,
        card_last4,
        card_status,
        fraud_score,
        dq_flags,
        last_dispute_at,
        open_case,
        same_amount,
        position,
    ) = row
    return Charge(
        transaction_id=transaction_id,
        customer_id=customer_id,
        product_id=product_id,
        country=country,
        language=Language(language) if language in set(Language) else Language.ES,
        status=status,
        transaction_ts=transaction_ts,
        transaction_type=transaction_type,
        amount=amount,
        currency=currency,
        merchant_name=merchant_name,
        card_last4=card_last4,
        card_status=card_status,
        fraud_score=fraud_score,
        dq_flags=tuple(dq_flags or ()),
        last_dispute_at=last_dispute_at,
        open_case=bool(open_case),
        same_amount=int(same_amount),
        position=int(position),
    )


def pool(
    con: duckdb.DuckDBPyConnection,
    policy: PolicyConfig,
    as_of: date,
    family: str,
    *,
    seed: str,
    limit: int,
) -> list[Charge]:
    """Up to ``limit`` charges of one family (`FAMILIES`), in the seed's order."""
    windows = _rule(policy, WindowRule).window_days.values()
    rows = con.execute(
        _POOL_SQL,
        {
            "family": family,
            "as_of": as_of,
            "recent_days": min(windows),
            "old_days": max(windows),
            "threshold": _rule(policy, FraudScoreRule).threshold,
            "repeat_days": _rule(policy, RepeatDisputerRule).window_days,
            "flags": list(_rule(policy, DqFlagRule).flags),
            "blockable": [status.value for status in _rule(policy, CardBlockableRule).statuses],
            "max_choices": MAX_CANDIDATES,
            "seed": seed,
            "pool": limit,
        },
    ).fetchall()
    return [_charge(row) for row in rows]


@dataclass(slots=True)
class _Picker:
    con: duckdb.DuckDBPyConnection
    policy: PolicyConfig
    as_of: date
    seed: str
    limit: int
    used: set[str]
    pools: dict[str, list[Charge]]

    def charges(self, family: str) -> list[Charge]:
        if family not in self.pools:
            self.pools[family] = pool(
                self.con, self.policy, self.as_of, family, seed=self.seed, limit=self.limit
            )
        return self.pools[family]

    def has_amount(self, customer_id: str, amount: Decimal) -> bool:
        row = self.con.execute(
            _SAME_AMOUNT_SQL, {"customer_id": customer_id, "amount": amount}
        ).fetchone()
        return bool(row and row[0])

    def twins_of(self, charge: Charge) -> list[Charge]:
        """The customer's other charges of the amount, as far as the twins pool holds them."""
        return [
            other
            for other in self.charges(_FAMILY[Scenario.CHOOSE_AMONG_MATCHES])
            if other.customer_id == charge.customer_id
            and other.amount == charge.amount
            and other.transaction_id != charge.transaction_id
        ]

    def pick(self, scenario: Scenario, count: int) -> list[CuratedCase]:
        picked: list[CuratedCase] = []
        candidates = self.charges(_FAMILY[scenario])
        for charge in candidates:
            if len(picked) == count:
                break
            if charge.customer_id in self.used:
                continue
            expected = expect(charge, self.policy, self.as_of)
            if not _fits(scenario, expected, self.policy):
                continue
            if scenario == Scenario.CHOOSE_AMONG_MATCHES and not tells_apart(
                charge, self.twins_of(charge), self.policy, self.as_of
            ):
                continue
            customer, language = charge.customer_id, charge.language
            if scenario == Scenario.OTHER_CUSTOMERS_CHARGE:
                # The session belongs to a customer with no charge of that amount, which rules
                # out the owner too.
                other = next(
                    (
                        candidate
                        for candidate in candidates
                        if candidate.customer_id not in self.used
                        and not self.has_amount(candidate.customer_id, charge.amount)
                    ),
                    None,
                )
                if other is None:
                    continue
                customer, language = other.customer_id, other.language
                expected = Expected(None)
            elif scenario == Scenario.RECOGNIZED:
                expected = Expected(None)
            self.used.update({customer, charge.customer_id})
            picked.append(
                CuratedCase(
                    case_id=f"{scenario.value}-{len(picked) + 1:02d}",
                    scenario=scenario,
                    customer_id=customer,
                    language=language,
                    charge=charge,
                    expected=expected,
                )
            )
        return picked


def _rule[RuleT](policy: PolicyConfig, kind: type[RuleT]) -> RuleT:
    return next(rule for rule in policy.rules if isinstance(rule, kind))


def pick_cases(
    serving_db: Path,
    policy: PolicyConfig,
    as_of: date,
    *,
    per_scenario: int,
    seed: str,
    scenarios: Sequence[Scenario] = tuple(Scenario),
) -> list[CuratedCase]:
    """Up to ``per_scenario`` cases of each scenario, deterministic for this file and seed.

    A scenario may get fewer cases (or none) when the file has too few fitting rows; the
    caller reports that instead of hiding it.
    """
    if per_scenario < 1:
        raise ValueError("per_scenario must be at least 1")
    with duckdb.connect(str(serving_db), read_only=True) as con:
        picker = _Picker(
            con=con,
            policy=policy,
            as_of=as_of,
            seed=seed,
            limit=max(500, per_scenario * 60),
            used=set(),
            pools={},
        )
        return [
            case
            for scenario in Scenario
            if scenario in scenarios
            for case in picker.pick(scenario, per_scenario)
        ]
