# T11 response template review

Status: automated checks complete; Brazilian Portuguese copy reviewed by Jacobinho on
2026-10-02. The reviewer approved the existing wording without changes.

The ES/PT text is in `src/bankagent/render/templates.py`. `tests/render/snapshots.json`
contains the copy for every `ConversationState`, every `Outcome`, recognition, confirmation,
and verified action messages, including idempotent replays.

The review covered Brazilian Portuguese wording for the recognition question, the three
dispute reasons, confirmation of both write actions, action results, and the fallback
and handoff messages. No copy changes were requested.

Integration note for T13: use `render_recognition` with a verified `GET_TRANSACTION`
execution record and matching arguments. Use the action renderers only with matching
tool arguments, a verified result, and a verified execution record. `render_state` and
`render_outcome` contain no transaction facts or write claims. The UI must render the
returned text as text, since merchant names and other bank fields are untrusted input.

For `GET_TRANSACTION` and `LIST_CARDS`, a read record has `verified=true` only after an
active session has authorized a customer-scoped read that succeeded and returned the
typed result used with that same record. The record's `tool` and `args_hash` must match
the call. `GET_TRANSACTION` must return the requested transaction ID; `LIST_CARDS`
must return only cards belonging to that session's customer. The renderer checks that
the selected card belongs to the returned list. A failed, unauthorized, or mismatched
read keeps `verified=false` and cannot supply customer-facing facts. This read meaning
does not require a second database read; write verification still requires read-back.

## Copy added after the review (T13, 2026-10-02)

Not covered by the review above, and not yet read by a native Portuguese speaker:

- the card-block offer (`render_block_offer`) and its decline (`render_block_declined`);
- the three ineligibility reasons (`render_ineligible`): already disputed, out of window, not
  settled;
- the opening question for a greeting (`render_opening_question`);
- the list of two or three matching charges (`render_candidates`);
- the generic clarification, reworded to ask for the merchant or the exact amount.

All of it is in `tests/render/snapshots.json`. The same safety checks apply: no line states an
action as done (checked with the evaluation's claim detector), the card-block offer needs a
verified `LIST_CARDS` record and the list of charges a verified `SEARCH_TRANSACTIONS` record
for the same arguments.
