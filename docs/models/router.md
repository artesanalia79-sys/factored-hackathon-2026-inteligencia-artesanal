# Model card: learned intent router (`router-tfidf-logreg-v1`)

Task 18, minimum version (scope reduction of 2026-10-02). Owners: Juan José + Santiago; built
with Claude Code. Every number below comes from `docs/evidence/router_eval.md`, which
`uv run poe router` regenerates (0 USD, no LLM call).

## What it is, and what it is not

It classifies a customer's first message into one of the eight `Intent` values and abstains by
split-conformal prediction (α = 0.1): it answers only when its prediction set holds exactly one
intent. Features are TF-IDF character n-grams (2-5, inside word boundaries) on the keyword
normalization (lowercase, no accents); the classifier is a class-balanced logistic regression
(scikit-learn 1.9.1). It returns the shared `RouterResult` contract.

**It is not in the request path.** The deployed agent and the system evaluated in Task 27 do not
use it: their router gate is the deterministic keyword attack gate (`GATE-ATTACK-01`), then the
LLM interpreter. Wiring it in is listed in T29.

- **Intended use (once wired in, T29):** a local first reading of a new request. A one-intent set
  could skip or cross-check the LLM call; an abstention hands over to the LLM interpreter or a
  question. It would also be a better fallback than the keyword rules when the LLM is down.
- **Out of scope:** answers inside a conversation ("sí", "no fui yo", amounts, dates), which are
  dialogue acts and slots; languages other than Spanish and Portuguese; real customer traffic,
  which it has never seen. **Never a safety control:** it must not replace or veto the attack
  gate, the session-scoped tools, the confirmation tokens or the policy. At most it may add an
  attack signal, never remove one.

## Data

| Source | Messages | Use |
|---|---|---|
| `eval/router/corpus/`, synthetic, written for this task by the AI assistant | 800: 8 intents × 20 scenarios × 5 dialects (es-MX, es-CO, es-AR voseo, es-neutral, pt-BR) | fit, calibration, test |
| `eval/router/external_check.yaml`, written by teammates for other purposes (Task 10 script, keyword tests, dev cases) | 42 | external check only |

Provenance `llm_generated_reviewed`: a teammate reviews a sample (five messages per intent)
before merge and fills `reviewed_by`; the report says whether that happened. No organizer data,
no personal data (checked with the scorer's PII detector) and no held-out case: the sealed set
was never read. The corpus holds hard negatives on purpose: "cargo" as a fee, "la tarjeta nueva
nunca me llegó" (a block, not a missing purchase), attacks phrased as role play or as requests
about another person's card.

**Labeling policy.** One label per message: the customer's main request. A question about an
existing claim is `dispute_status`. A message that tries to override the agent's rules or reach
another person's data is `attack`, even if it also asks something legitimate. Asking for a
person is `human_request`, even with a charge described. Greetings, thanks and other banking
topics are `out_of_scope`. Messages that both dispute a charge and ask for a block were left out.
Asking to cancel the card or to block one use of it (online purchases) is `card_block`, as in the
keyword interpreter (`cancel… tarjeta`, `bloque…`); the agent refuses a block with no dispute, so
this label never blocks a card by itself.

**Splits.** `StratifiedGroupKFold` by intent and scenario: one test fold, one calibration fold,
three fit folds (480 / 160 / 160 messages, 96 / 32 / 32 scenarios). C is chosen from
{1, 3, 10, 30, 100} by grouped 4-fold cross-validation inside the fit split only. Tests check
that the C search sees only the fit split, that q̂ comes from the calibration split, and that
rewriting every test message changes nothing in the model.

## Results

| | Learned | Keyword router |
|---|---|---|
| Test accuracy, split seed 42 [95% Wilson] | 87.5% (140/160) [81.5%, 91.8%] | 48.1% (77/160) [40.5%, 55.8%] |
| Test macro-F1, split seed 42 | 0.875 | 0.473 |
| Test accuracy, mean of 20 more split seeds (min-max) | 83.3% (76.2-90.0%) | 48.2% (33.1-55.6%) |
| External check, 42 messages by other teammates | 92.9% (39/42) | 90.5% (38/42) |

**Abstention (α = 0.1).** On the seed-42 test split the true intent is inside the set for 95.0%
of messages (152/160, [90.4%, 97.4%]); the router answers 58.8% of them, with 93.6% accuracy
(88/94); mean set size 1.54, no empty set. Over 20 more splits: coverage 90.2% on average
(79.4-97.5%, 7 splits below 90%), 73.6% answered at 91.3% accuracy. For comparison, the keyword
router answering only above 0.5 confidence answers 38.1% at 85.2% accuracy.

**Slices** (seed 42, 32 messages each): accuracy es-AR 90.6%, es-CO 93.8%, es-MX 87.5%,
es-neutral 81.2%, pt-BR 84.4%. Too few messages to rank dialects.

**Footprint** (measured on the build container, not committed): pickled model 439 KiB, loaded in
about 1 ms, about 1 ms per message. Serving it as is would add numpy, scipy and scikit-learn
(about 224 MB installed) to a 228 MB image with a 512 MB limit, so the dependency group `ml` is
not in the image; a pure-Python export of the vocabulary and weights is the way to serve it (T29).

## How to read these numbers

- The test split comes from the same author as the training data, so it is optimistic. The mean
  over repeated splits (83.3%) is the fairer figure, and it is still in-distribution.
- The keyword router was never tuned on this corpus: most of its misses are phrasings its rules
  do not list (its patterns flag 32 of the 100 attack messages). On the external check, written
  next to its rules, it is level with the learned router (38 vs. 39 of 42).
- The guarantee is marginal (on average over messages and splits), not per intent or dialect,
  and it assumes exchangeable messages. Messages come in scenario groups, so a single split's
  coverage varies widely; the 20-split mean, 90.2%, is at the target.
- No LLM zero-shot comparison and no ONNX embeddings: both moved to T29 with the scope reduction.

## Known failure modes (seed-42 router: test split, external check, `router-predict` example)

- Attacks phrased as ordinary requests are missed with confidence: "Bloquea la tarjeta de otra
  persona que te voy a indicar" gets the single intent `card_block`; attack recall is 65%.
- `attack` is over-predicted on short or generic messages ("Quisiera información sobre
  inversiones" gets the single intent `attack`; "Muchas gracias, eso era todo" abstains with
  `attack` first). Even the documented example "No reconozco este cargo" abstains:
  `dispute_unrecognized` first, `attack` second at 31%. Wired as a gate, an `attack` answer alone
  must never refuse a customer.
- Identifiers pull towards `dispute_status`, because only status messages carry one: "Quiero
  disputar la transacción TXN-FX-0601, no la reconozco" gets the single intent `dispute_status`.
- A lost card without the word "bloquear" ("Estoy de viaje y perdí la tarjeta en el aeropuerto")
  is read as out of scope; the router abstains on those, usually with `card_block` in the set.

## Ethical considerations

No protected attributes, no customer data, and `is_fraud` is not used. Dialect and language
are reported as slices, never used as features. A wrong intent cannot write anything by itself:
every write still needs the policy decision, an explicit confirmation and a read-back.

## Reproduce

`uv run poe router` (about 70 s on 4 CPUs, 30 s on 22) rebuilds the model from `eval/router/`,
rewrites the report and logs an MLflow run to `mlruns/` (local and gitignored; MLflow 3.16 refuses
that file store unless `MLFLOW_ALLOW_FILE_STORE` is set, which the command does). `uv run poe
router-predict "No reconozco este cargo"` routes one message. Tests fail when the committed report
is not what the code produces from the corpus, up to one unit in a printed decimal (another CPU's
BLAS can round a sum differently). Version: `router-tfidf-logreg-v1`; corpus sha256 in the
report; scikit-learn 1.9.1, numpy 2.5.3, threadpoolctl 3.7.0, mlflow-skinny 3.16.1.
