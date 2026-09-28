---
name: debug-ci
description: Diagnose and fix a failing GitHub Actions run for this repo using the gh CLI, reproducing the failing step locally with the same uv/poe commands. Use when CI is red.
---

# Debugging a failing CI run

## 1. Find the failure

```powershell
gh run list --limit 5
gh run view <run-id> --log-failed
```

## 2. Map the step to a local command

| CI step | Local command |
|---|---|
| `uv sync --locked` | `uv sync --locked` (lock out of date → `uv lock`, commit `uv.lock`) |
| `poe check` | `uv run poe check` (or `lint`, `format-check`, `typecheck`, `test` separately) |
| skills-check | `uv run poe sync-skills` then commit the mirrors |
| contracts-check | `uv run poe contracts` then commit `docs/contracts/` |
| fixtures-check | `uv run poe fixtures --update-hash` (only if the YAML change was intended) |
| pre-commit | `uv run pre-commit run --all-files` |
| gitleaks | find the leaked value, remove it, **rotate the secret**, never just delete the line |
| eval-smoke | `uv run poe eval-smoke` |

## 3. Common causes

- CRLF vs LF: `.gitattributes` forces LF; run `uv run pre-commit run mixed-line-ending --all-files`.
- Windows-only paths (`\`) in code; use `pathlib`.
- A test depends on the wall clock; inject `now` / use `as_of_date`.
- Missing pinned dependency: add it with `uv add --group <lane> "pkg==X.Y.Z"`.

## 4. Rules

- Never disable a check, mark a test skip, or push `--no-verify` to get green.
- If a secret leaked, stop and tell a human: the secret must be rotated.
