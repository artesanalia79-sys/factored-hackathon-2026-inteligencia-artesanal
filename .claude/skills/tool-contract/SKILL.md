---
name: tool-contract
description: Add or change an agent tool (read or write) with typed args/results, session-scoped authorization, confirmation tokens, idempotency, read-back verification and BOLA tests. Use when editing src/bankagent/tools.
---

# Adding or changing a tool

Read `docs/rules/backend.md` first. Tools are the only way the agent touches bank data.

## Contract

1. Define `<Name>Args` and `<Name>Result` in `src/bankagent/contracts/tools.py`. Args must not
   include `customer_id`; identity comes from `ToolContext.session`.
2. Add a `ToolName` enum value and a `ToolSpec` entry in `TOOL_SPECS` (`is_write`,
   `requires_confirmation`, description in English).
3. Implement the `Tool` protocol: `run(ctx: ToolContext, args: <Name>Args) -> <Name>Result`.

## Implementation rules

- Check `ctx.session.is_active(ctx.now)` first → `SessionExpired`.
- Scope every query by `ctx.session.customer_id` with **parameterized SQL**. The SQL lives in
  `src/bankagent/store/` as constant strings (the tools hold none; a test checks it):

```python
row = con.execute(
    "SELECT * FROM transactions_enriched WHERE transaction_id = ? AND customer_id = ?",
    [args.transaction_id, ctx.session.customer_id],
).fetchone()
if row is None:
    raise NotFound("transaction")  # same error for "not yours" and "does not exist"
```

- Write tools: require `ctx.confirmation_token_id`, validate the token matches
  `(session_id, action, args_hash)`, is unused and unexpired, then mark it used in the same
  transaction as the write. Missing/mismatched → `ConfirmationRequired`.
- Idempotency: the same `idempotency_key` returns the existing record, never a second write.
  On a confirmed write the token is checked first, so a replay needs a **fresh** token: repeated
  with its used token the call is `ConfirmationRequired`, not the stored record. After a lost
  response, read the record (`get_dispute` by transaction, `list_cards`) or issue a new token.
- After a write, read it back and return `verified=True` only if it matches.
- Transient infrastructure failures raise `ToolUnavailable` (retryable); never swallow errors.
- Emit an `ExecutionRecord` for every call (args hash, outcome, latency). Never log raw PII.

## Where the pieces live

- `src/bankagent/tools/base.py`: `BaseTool`. Subclass it, set `name` and write `_run`; `run`
  already checks the session and the argument type and turns database failures into
  `ToolUnavailable` with a cause that carries no values. Never raise or chain an exception that
  quotes a row, an argument or a customer text.
- Confirmed writes (`requires_confirmation=True`) follow `tools/writes.py`:
  `_require_allowed_by_policy(ctx)` (the `PolicyDecision` in `ToolContext.policy` must allow the
  action), look the target up scoped by customer, then inside `store.transaction()` call
  `_consume_token(...)` and the insert, then `_read_back(...)`. Decide explicitly what each store
  refusal becomes (`IdempotencyConflict`, `DisputeAlreadyExists`, `CardAlreadyBlocked`).
- `tools/__init__.py`: register the tool in `build_tools`; it refuses to start if the tools and
  `TOOL_SPECS` differ.
- `tools/confirmation.py`: `issue_confirmation_token` issues the token for one exact call.
- `tools/records.py`: `call_tool` runs a tool and returns its `ExecutionRecord`; the orchestrator
  owns the turn and step numbering and stores the records.
- Tests go in `tests/tools/` on the fixture bank; `conftest.py` has the `desk` fixture.

## Required tests

- Happy path with the fixture bank.
- BOLA: another customer's id → `NotFound`, indistinguishable from a missing id.
- Expired session → `SessionExpired`.
- Writes: no token, wrong token, reused token, expired token → `ConfirmationRequired`.
- Idempotency: two calls (the second with a fresh token), one row.
- Read-back mismatch → `verified=False`.
- Writes: a policy decision that does not allow the action (or none) → `InvalidArguments`.
