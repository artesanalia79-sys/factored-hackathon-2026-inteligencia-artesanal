# T10 interpreter dev comparison

Date: 2026-09-28. Script: `scripts/compare_interpreter.py`.

Sixteen team-authored synthetic ES/PT utterances were labeled before the run. They are a small
development sample, not the sealed held-out evaluation. The real `gpt-6-luna` provider and the
deterministic keyword baseline received the same utterances. Intent accuracy:

| System | Correct | Accuracy |
|---|---:|---:|
| Keyword baseline | 12 / 16 | 75.0% |
| OpenAI interpreter | 16 / 16 | 100.0% |

The OpenAI run cost 0.0015963 USD according to API token usage and `config/pricing.yaml`. Four
baseline misses were paraphrases of duplicate charges, unauthorized payments and dispute status.
The sample is too small and authored too close to implementation to estimate production accuracy.
The 95% Wilson lower bound for 16/16 is about 81%, so the apparent advantage is preliminary.

OpenAI MLflow autologging is left disabled: its default traces include raw customer prompts and
responses, which conflicts with the repository's no-PII logging rule. The provider exposes safe
token/cost metadata through `ExecutionRecord` for completed calls. Failed calls do not yet emit
records; T20 owns that follow-up. Offline synthetic transport fixtures make CI independent of
the API and cost-free; they are not recorded API responses.
