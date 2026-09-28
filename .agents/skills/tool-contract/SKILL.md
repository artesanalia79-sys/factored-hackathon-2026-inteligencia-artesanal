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
- Scope every query by `ctx.session.customer_id` with **parameterized SQL**:

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
- After a write, read it back and return `verified=True` only if it matches.
- Transient infrastructure failures raise `ToolUnavailable` (retryable); never swallow errors.
- Emit an `ExecutionRecord` for every call (args hash, outcome, latency). Never log raw PII.

## Required tests

- Happy path with the fixture bank.
- BOLA: another customer's id → `NotFound`, indistinguishable from a missing id.
- Expired session → `SessionExpired`.
- Writes: no token, wrong token, reused token, expired token → `ConfirmationRequired`.
- Idempotency: two calls, one row.
- Read-back mismatch → `verified=False`.
