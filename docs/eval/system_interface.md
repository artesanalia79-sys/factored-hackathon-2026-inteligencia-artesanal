# What the agent must expose for evaluation (Task 13)

Audience: Jacobo (Task 13). Owner of the harness side: Santiago (Task 12).

The evaluation harness (`src/bankagent/eval/`) scores the agent from its `ExecutionRecord`s and from
what the tools did. **Production code never imports `bankagent.eval`**: the harness wraps the agent
through `bankagent.eval.adapters.TurnFunctionSystem`. Task 13 only has to expose the shape below.
Nothing here requires a contract change.

## 1. An agent factory with injected dependencies

```python
def create_agent(
    *,
    llm: LLMProvider,  # bankagent.contracts.llm
    tools: Mapping[ToolName, Tool[Any, Any]],  # bankagent.contracts.tools, keyed by ToolName
    clock: Callable[[], datetime],  # returns an aware UTC datetime
) -> Agent: ...
```

- One agent per conversation. The harness creates a new one for every case run.
- The agent must use **these** objects: the `llm` it receives (the `StubProvider` in CI, 0 USD) and
  the `tools` it receives. The harness wraps every tool to see which records it reads or writes and
  to inject `tool_unavailable` faults. An agent that opens its own store or provider bypasses the
  evaluation.
- It must read the time only from `clock` (session expiry and confirmation-token TTLs are tested
  with it).
- Any other dependency (policy, templates, router, ops store) may be built inside the factory.

## 2. A turn method

```python
class Agent(Protocol):
    def handle_turn(self, session: Session, text: str, /) -> AgentTurnOutput: ...
```

- `session` is the server-side `bankagent.contracts.domain.Session` the harness opened for the
  case customer. Its `customer_id` never reaches the model; the agent passes the session to the
  tools through `ToolContext`. For `expired_session` cases the harness hands over a session that is
  already expired: the agent must detect it with `session.is_active(clock())`.
- `text` is one customer message (Spanish or Portuguese).

`AgentTurnOutput` is any object with these attributes (a dataclass or a Pydantic model is fine):

| Attribute | Type | Meaning |
|---|---|---|
| `reply_text` | `str` | the customer-facing reply of this turn |
| `records` | `Sequence[ExecutionRecord]` | every record emitted **during this turn** |
| `ended` | `bool` | true when the conversation is over (resolved, denied, escalated, reauth) |
| `claimed_actions` | `Sequence[ActionType]` | actions the reply states as done (from the template keys) |

The HTTP endpoint (`POST /api/chat/turn`) can call the same method, but the harness uses the
method directly: through HTTP it could not inject the tools and LLM provider.

## 3. What the records must show

The scorer reads these fields. Everything else in the backend rules still applies.

| Situation | Record the scorer expects |
|---|---|
| Session expired at any point | a record with `error_code = session_expired` (e.g. `step = authenticate`, `outcome = failure`) |
| Tool call | `step = tool_call`, `tool`, `args_hash = args_hash(args)`, the real `outcome` and `error_code` |
| Write read back successfully | `verified = true` on the write's `tool_call` record, or a `step = verify` record with the same `tool` and `args_hash` |
| Explicit confirmation | a `step = confirmation`, `outcome = success` record with the **same `args_hash`** as the write it authorizes, emitted in the turn where the customer said yes, before the write |
| Policy refusal or attack | `step = policy`, `outcome = blocked`, with `rule_ids` |
| Abstention | a record with `state = abstain` |
| Handoff | a `create_handoff` tool call (the harness reads the `HandoffDraft` from the tool arguments) |
| Every record | `turn_index` = the 0-based index of the user message, `step_index` increasing within the turn |

`args_hash` must be computed with `bankagent.contracts.base.args_hash` on the exact arguments passed
to the tool, so the confirmation, the tool call and the harness observation line up.

## 4. How the harness plugs it in

Real runs use the real Task 8 tools through `RunConfig.backend_factory`
(`bankagent.eval.backend.FixtureBackendFactory`): the fixture bank is shared and every case run
gets a fresh, empty ops store, so one run's disputes never reach another run or repeat. The
run's stores are in `EvalEnvironment.backend`; the T13 agent's policy evaluator and
confirmation issuer must be bound to them, not to stores of their own.

When T13 is on `main`, `bankagent.eval.adapters.proposed_system` becomes (`TODO(T13, Santiago)`):

```python
class _ProposedSystem:
    name = "proposed"
    variant = SystemVariant.PROPOSED

    def open_session(self, env: EvalEnvironment) -> _AgentSession:
        backend = env.backend  # required: real tools
        agent = create_agent(
            llm=env.llm,
            tools=env.tools,
            clock=env.clock,
            policy=build_policy_evaluator(backend.serving, backend.store, clock=env.clock),
            issue_confirmation=build_confirmation_issuer(backend.store),
        )
        return _AgentSession(agent, env.session)
```

The LLM-only baseline (`bankagent.eval.baseline`, decision D1) already runs this way:

```bash
uv run poe eval-run --system baseline                      # dev cases, StubProvider, 0 USD
uv run poe eval-run --system baseline --system proposed \
    --provider openai --repeats 3 --budget-usd 5          # real cost: owner approval first
```

With the `StubProvider` the baseline cannot act (the stub only produces interpretations), so a
stub run checks the plumbing, not the baseline. `uv run poe eval-smoke` still runs the scripted
fakes in `bankagent.eval.fake`, which follow this interface and serve as an executable example.

## 5. Quick self-check for Task 13

```bash
uv run poe eval-smoke          # harness self-check with the fakes (CI)
uv run pytest tests/eval -q    # scorer, detectors, simulator, pipeline
```

Once the adapter is wired, a dev run of the real agent on `eval/dev/` must show zero unsafe events
and zero unclassified simulator questions before the held-out run.
