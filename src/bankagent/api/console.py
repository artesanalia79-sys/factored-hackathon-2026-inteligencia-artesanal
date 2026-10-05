"""Read-only HTTP surface for the human-agent console (T21): across-customer views a bank
employee needs, never reachable with a customer session token.

Gated by the same shared demo access code as login (``AuthService.check_console_access``),
presented as an ``X-Console-Access-Code`` header instead of a login field. No code configured
(local, no ``DEMO_ACCESS_CODE``) means no gate, exactly like the login's own meaning of it.

``ConsoleDeps`` carries narrow callables, not whole store objects, so this module never imports
``bankagent.store`` directly and the wiring (``bankagent.api.wiring``) stays the one place that
decides what backs them.

No ``from __future__ import annotations`` here: FastAPI must resolve the dependency closed over
inside ``build_console_router`` when it reads the endpoint signatures (``bankagent.auth.http``
hits the same thing).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from bankagent.auth.http import SafeValidationRoute
from bankagent.auth.service import AccessCodeRequired, AuthService
from bankagent.contracts.console import (
    ConsoleCardBlockEntry,
    ConsoleDisputeEntry,
    ConsoleHandoffDetail,
    ConsoleHandoffEntry,
    RuleExplanation,
)
from bankagent.contracts.domain import CardBlockEvent, DisputeCase
from bankagent.contracts.handoff import HandoffPacket
from bankagent.contracts.records import ExecutionRecord
from bankagent.policy.schema import ACTION_KINDS, PolicyConfig

DEFAULT_LIMIT = 50


@dataclass(frozen=True, slots=True)
class ConsoleDeps:
    """Everything the console router reads. Built once in ``bankagent.api.wiring``."""

    list_handoffs: Callable[[int], Sequence[HandoffPacket]]
    get_handoff: Callable[[str], HandoffPacket | None]
    list_disputes: Callable[[int], Sequence[tuple[str, DisputeCase]]]
    list_card_blocks: Callable[[int], Sequence[tuple[str, CardBlockEvent]]]
    list_records: Callable[[str], Sequence[ExecutionRecord]]
    # A customer's first name for display, or None (an id outside the serving DB, or a test
    # double that does not track names). Never the source of truth for identity.
    customer_name: Callable[[str], str | None]
    policy: PolicyConfig


def build_console_router(*, auth: AuthService, deps: ConsoleDeps) -> APIRouter:
    router = APIRouter(prefix="/api/console", tags=["console"], route_class=SafeValidationRoute)
    descriptions = {rule.rule_id: rule.description for rule in deps.policy.rules}
    kinds = {rule.rule_id: rule.kind for rule in deps.policy.rules}

    def explanations(
        rule_ids: Sequence[str], *, decisive_kinds: Sequence[str] | None = None
    ) -> tuple[RuleExplanation, ...]:
        """Every rule by id, in full, decisive ones first. ``decisive_kinds=None`` means every
        rule given is already curated as decisive (``HandoffPacket.trigger_rule_ids``);
        otherwise only a rule of one of these kinds is (disputes: ``ACTION_KINDS``, the one a
        `proceed` decision can still vary on)."""
        rows = [
            RuleExplanation(
                rule_id=rule_id,
                description=descriptions.get(rule_id, rule_id),
                decisive=True if decisive_kinds is None else kinds.get(rule_id) in decisive_kinds,
            )
            for rule_id in rule_ids
        ]
        rows.sort(key=lambda row: not row.decisive)
        return tuple(rows)

    def require_access(
        x_console_access_code: Annotated[str | None, Header()] = None,
    ) -> None:
        try:
            auth.check_console_access(x_console_access_code)
        except AccessCodeRequired:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": AccessCodeRequired.code},
            ) from None

    def handoff_entry(handoff: HandoffPacket) -> ConsoleHandoffEntry:
        return ConsoleHandoffEntry(
            handoff=handoff,
            customer_name=deps.customer_name(handoff.customer_id),
            rule_explanations=explanations(handoff.trigger_rule_ids),
        )

    @router.get("/handoffs", dependencies=[Depends(require_access)])
    def handoffs(limit: int = DEFAULT_LIMIT) -> list[ConsoleHandoffEntry]:
        return [handoff_entry(handoff) for handoff in deps.list_handoffs(limit)]

    @router.get("/handoffs/{handoff_id}", dependencies=[Depends(require_access)])
    def handoff_detail(handoff_id: str) -> ConsoleHandoffDetail:
        handoff = deps.get_handoff(handoff_id)
        if handoff is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail={"error": "not_found"}
            )
        return ConsoleHandoffDetail(
            entry=handoff_entry(handoff), records=tuple(deps.list_records(handoff.trace_id))
        )

    @router.get("/disputes", dependencies=[Depends(require_access)])
    def disputes(limit: int = DEFAULT_LIMIT) -> list[ConsoleDisputeEntry]:
        return [
            ConsoleDisputeEntry(
                customer_id=customer_id,
                customer_name=deps.customer_name(customer_id),
                case=case,
                rule_explanations=explanations(case.rule_ids, decisive_kinds=ACTION_KINDS),
            )
            for customer_id, case in deps.list_disputes(limit)
        ]

    @router.get("/card-blocks", dependencies=[Depends(require_access)])
    def card_blocks(limit: int = DEFAULT_LIMIT) -> list[ConsoleCardBlockEntry]:
        return [
            ConsoleCardBlockEntry(
                customer_id=customer_id,
                customer_name=deps.customer_name(customer_id),
                event=event,
            )
            for customer_id, event in deps.list_card_blocks(limit)
        ]

    return router
