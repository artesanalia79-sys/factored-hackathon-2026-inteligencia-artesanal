# T11 response template review

Status: automated checks complete; Portuguese speaker review pending (reviewer not assigned).

The ES/PT text is in `src/bankagent/render/templates.py`. `tests/render/snapshots.json`
contains the copy for every `ConversationState`, every `Outcome`, recognition, confirmation,
and verified action messages, including idempotent replays.

The reviewer should check Brazilian Portuguese wording for the recognition question,
the three dispute reasons, confirmation of both write actions, action results, and the
fallback and handoff messages. Record the reviewer's name and any wording changes here
before marking the PT acceptance criterion complete.

Integration note for T13: use `render_recognition` with a verified `GET_TRANSACTION`
execution record and matching arguments. Use the action renderers only with matching
tool arguments, a verified result, and a verified execution record. `render_state` and
`render_outcome` contain no transaction facts or write claims. The UI must render the
returned text as text, since merchant names and other bank fields are untrusted input.
