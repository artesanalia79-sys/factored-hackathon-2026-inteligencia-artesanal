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
from bankagent.policy.schema import PolicyConfig

DEFAULT_LIMIT = 50


@dataclass(frozen=True, slots=True)
class ConsoleDeps:
    """Everything the console router reads. Built once in ``bankagent.api.wiring``."""

    list_handoffs: Callable[[int], Sequence[HandoffPacket]]
    get_handoff: Callable[[str], HandoffPacket | None]
    list_disputes: Callable[[int], Sequence[tuple[str, DisputeCase]]]
    list_card_blocks: Callable[[int], Sequence[tuple[str, CardBlockEvent]]]
    list_records: Callable[[str], Sequence[ExecutionRecord]]
    policy: PolicyConfig


def build_console_router(*, auth: AuthService, deps: ConsoleDeps) -> APIRouter:
    router = APIRouter(prefix="/api/console", tags=["console"], route_class=SafeValidationRoute)
    descriptions = {rule.rule_id: rule.description for rule in deps.policy.rules}

    def explanations(rule_ids: Sequence[str]) -> tuple[RuleExplanation, ...]:
        return tuple(
            RuleExplanation(rule_id=rule_id, description=descriptions.get(rule_id, rule_id))
            for rule_id in rule_ids
        )

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

    @router.get("/handoffs", dependencies=[Depends(require_access)])
    def handoffs(limit: int = DEFAULT_LIMIT) -> list[ConsoleHandoffEntry]:
        return [
            ConsoleHandoffEntry(
                handoff=handoff, rule_explanations=explanations(handoff.trigger_rule_ids)
            )
            for handoff in deps.list_handoffs(limit)
        ]

    @router.get("/handoffs/{handoff_id}", dependencies=[Depends(require_access)])
    def handoff_detail(handoff_id: str) -> ConsoleHandoffDetail:
        handoff = deps.get_handoff(handoff_id)
        if handoff is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail={"error": "not_found"}
            )
        return ConsoleHandoffDetail(
            entry=ConsoleHandoffEntry(
                handoff=handoff, rule_explanations=explanations(handoff.trigger_rule_ids)
            ),
            records=tuple(deps.list_records(handoff.trace_id)),
        )

    @router.get("/disputes", dependencies=[Depends(require_access)])
    def disputes(limit: int = DEFAULT_LIMIT) -> list[ConsoleDisputeEntry]:
        return [
            ConsoleDisputeEntry(
                customer_id=customer_id,
                case=case,
                rule_explanations=explanations(case.rule_ids),
            )
            for customer_id, case in deps.list_disputes(limit)
        ]

    @router.get("/card-blocks", dependencies=[Depends(require_access)])
    def card_blocks(limit: int = DEFAULT_LIMIT) -> list[ConsoleCardBlockEntry]:
        return [
            ConsoleCardBlockEntry(customer_id=customer_id, event=event)
            for customer_id, event in deps.list_card_blocks(limit)
        ]

    return router
