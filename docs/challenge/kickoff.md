# Kickoff Briefing (Sep 25, 2026): Team Notes

> Source: kickoff slides and the official site (datathon.factored.ai), summarized by the team.

## Timeline

| Date | Milestone |
|---|---|
| Sep 25 | Challenge launch, 10-day build begins |
| **Oct 5** | **Submissions close** |
| Oct 15 | Finalists announced |
| Oct 16 | Award ceremony |

## The challenge in one line

"Don't build a chatbot, build a customer-service system." One focused banking workflow
(account/payment inquiries, card support, transaction disputes, or credit-product info and
eligibility), an end-to-end working prototype, and multilingual support in **Spanish and Portuguese**.

The system should: **Understand → Decide → Act → Verify → Escalate**.

Minimum requirements: maintain conversational context, clarify ambiguous requests, retrieve trusted
information, use tools securely, execute appropriate workflows, verify that actions actually
happened, know when NOT to act, hand off to a human when needed.

Key idea: *AI should not be autonomous just because it can be.*

| Case | Expected behavior |
|---|---|
| Normal case | Automated resolution: policy-compliant resolution, verified account queries, authorized self-service |
| Ambiguous / unsupported | Clarification or abstention for missing parameters or unsupported requests |
| Human-required | Safe escalation: structured handoff with verified facts and open questions, without dumping raw transcripts |

## Evaluation and rigor ("Prove it works")

Baseline → Proposed system → Held-out evaluation.

- **Technical rigor:** data quality and contracts (strict input schema enforcement), reproducible
  preparation (deterministic pipeline execution), valid labels (grounded relevance judgments and
  ground truth), leakage prevention (strict train/eval isolation), appropriate split (realistic
  held-out distributions), learned component benchmarked against a baseline model.
- **Key metrics:** safe automated resolution, unsafe outcomes, cost efficiency.
- **Technical deliverables:** data-backed baseline (reproducible logs), grounded AI core (verified
  records), controlled automation (permissions enforced besides prompts), data and ML discipline
  (repeatable pipelines, strict schema contracts), measured failures (held-out stress tests incl.
  injection), route to operation (deterministic setup with audit execution logs).

## Multi-disciplinary evaluation (no single skill is mandatory)

| Discipline | Suggested tasks |
|---|---|
| Artificial Intelligence | Production backend and structured JSON handoffs |
| Machine Learning | LLM/RAG orchestration and prompt injection defense |
| Data Engineering | Strong ETL/ELT pipeline and customer record isolation |
| Data Analysis | Demand patterns and cost-per-resolution ROI |

## Make the result a real service

| Observability | Reliability | Security | Reproducibility |
|---|---|---|---|
| Tracing | Bounded retries | Authentication | Setup instructions |
| Execution records | Safe fallback | Access controls | Versioning |
| Monitoring | Tool failure handling | Data retention | Repeatable evaluation |

Be honest about what's missing: capacity limits, data limitations, language coverage, deployment
work, remaining risks.

**Final takeaway:** build something that works, prove that it works, and know when it should not
act. And show what it would take to make it real.

## Evaluation criteria (kickoff slide)

"First and foremost the solution should work."

- Overall project rationale and documentation
- AI Engineering: backend, frontend and deployment
- Data Analytics: data quality and relevant insights from the solution
- Data Engineering: extraction and transformation of the data
- Machine Learning: model selection, optimization, implementation and tracking

Official site criteria: architecture, reliability, reproducibility, data quality, business
reasoning, privacy and fairness, production thinking.

## Submission (all required)

1. Link to the **public** GitHub repository named `factored-hackathon-2026-[team name]`
   (ours: `factored-hackathon-2026-inteligencia-artesanal`).
2. Link to where the tool is **deployed**.
3. A **4–6 slide** presentation with details on the tool.
4. A short, **mandatory video pitch** demonstrating the working solution and explaining core
   architectural decisions.

Submit everything to **hackathon.admin@factored.ai**. "Submit your tool no matter what."

Suggested resources (optional): Microsoft Azure, Snowflake, AWS, Databricks. Any language and tools
are allowed. Support channel: Slack `#technical-help`.
