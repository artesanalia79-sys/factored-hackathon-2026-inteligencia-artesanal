# Submission checklist

Skeleton from Task 15; Task 28 fills it in. Deadline: **Mon Oct 5 2026**, by email to
`hackathon.admin@factored.ai`. The organizers require all four items below
(`docs/challenge/kickoff.md`, "Submission").

Owners marked "T28" are not assigned yet: Santiago assigns them when T28 starts.

## The four required items

| # | Item | Where it comes from | Owner | Done |
|---|---|---|---|---|
| 1 | Link to the **public** GitHub repository `factored-hackathon-2026-inteligencia-artesanal` | The repository, made public after the checks in "Before the repository goes public" | Santiago (T28) | [ ] |
| 2 | Link to where the tool is **deployed** | The Render service of `docs/operations.md` | Santiago (T15, T26) | [ ] |
| 3 | A **4-6 slide** presentation | `docs/submission/slides_outline.md` | T28 | [ ] |
| 4 | A short **video pitch** showing the working solution and the core architecture decisions | `docs/submission/video_script.md` | T28 | [ ] |

## Before the repository goes public

Making it public publishes the whole git history, not only the current files.

- [ ] `gitleaks` on the full history is green on `main` (CI job `secrets`).
- [ ] `uv run poe secrets-scan` passes on a fresh clone.
- [ ] No file under `private/`, `data/` or `eval/heldout/` was ever committed:
      `git log --all --diff-filter=A --name-only -- private data eval/heldout` prints nothing.
- [ ] The held-out set is represented only by its sha256 manifest.
- [ ] The access code of the public service is in no committed file (the pre-commit hook
      compares staged files with the local `.env` value of `DEMO_ACCESS_CODE`).

## The deployed service (Task 15, Task 26)

- [ ] The freeze tag is deployed and it is the commit the service reports
      (`docs/operations.md`, "Which commit is live").
- [ ] Auto-deploy is Off, so a later merge cannot restart or change the demo.
- [ ] `uv run poe smoke <public URL>` ends with `OK: verified dispute`, then the demo is reset
      (`docs/operations.md`, "Reset the demo") so the judges start from a clean state.
- [ ] `DEMO_ACCESS_CODE` is set, and the code is in the email and on the slide with the URL,
      not in the repository.
- [ ] If the real model is on: the OpenAI project has a monthly budget set by the key owner,
      and `LLM_SPEND_LIMIT_USD` is set on purpose.
- [ ] The email says the first request can take about a minute (the free instance sleeps after
      15 minutes without traffic) and that the demo state resets when it sleeps.

## Evidence the judges can check

- [ ] `README.md` quick start works on a fresh clone (`uv sync`, `uv run poe fixtures`,
      `uv run poe check`).
- [ ] Final evaluation report with both systems and the deviations from the pre-registration
      (Task 27), linked from the README.
- [ ] `docs/limitations.md` is current.
- [ ] `docs/operations.md` has the measured image size and memory and the staging record.

## The email

- [ ] To `hackathon.admin@factored.ai`, sent in the morning with margin before the deadline.
- [ ] Contains: repository link, deployed URL, access code, slides, video link, team name and
      members.
- [ ] Someone other than the sender opened every link from a browser with no session.
