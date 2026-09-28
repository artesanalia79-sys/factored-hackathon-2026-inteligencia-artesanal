---
name: write-adr
description: Record an architecture decision record (ADR) in docs/adr using the repo template, with context, options considered, decision and consequences. Use when a choice has trade-offs other teammates or judges should understand.
---

# Writing an ADR

1. Copy `docs/adr/0000-template.md` to `docs/adr/NNNN-short-title.md` with the next free number.
2. Fill in: status (`proposed` / `accepted` / `superseded by NNNN`), date, deciders, context,
   options considered (at least two, with pros and cons), decision, consequences (good and bad),
   and how we would know the decision was wrong.
3. Keep it under one page. Link evidence (queries, eval runs, docs) instead of pasting it.
4. If it supersedes an older ADR, update the old one's status line.
5. Small decisions that do not merit an ADR go to `docs/decision_ledger.md` as one table row.

ADRs are in English and never contain secrets or organizer data rows.
