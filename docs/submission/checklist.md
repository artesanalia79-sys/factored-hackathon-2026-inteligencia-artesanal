# Submission checklist

Skeleton from Task 15; Task 28 fills it in. Deadline: **Mon Oct 5 2026, 11:59 PM Colombia time
(UTC-5)**, by email to `hackathon.admin@factored.ai`. The organizers require all four items
below (`docs/challenge/kickoff.md`, "Submission"; the Participant Hub,
<https://www.factored.ai/careers/ai-data-hackathon>).

## What the organizers answered

Read on 2026-10-03 in the hackathon's Slack (answers by Factored staff) and on the Participant
Hub. Read them again before sending: an answer in a chat can be corrected later.

| Topic | Answer | Where |
|---|---|---|
| Deadline | October 5, 11:59 PM Colombia time (UTC-5) | `#challenge-help` 2026-09-28, `#general` 2026-10-02 |
| Language | All deliverables in English, the video included. The prototype itself must show customer interactions in Spanish and Portuguese. | `#general` 2026-10-01; Hub FAQ |
| Video | No longer than 3 minutes | `#challenge-help` 2026-09-28; Hub |
| Where | Email to `hackathon.admin@factored.ai`. It bounced for another team on 2026-10-01 ("the account does not exist") and was fixed the next day. | Hub; `#general` 2026-10-01 and 10-02 |
| Who is on the team | The people who contributed to the GitHub repository when it is submitted. At most 4, each registered individually. A shared or machine account that commits is fine as long as every member appears in the contributor list. | `#general` 2026-09-25 and 09-28; `#announcements` 2026-09-15 |
| Emails | A registration email different from the GitHub one is no problem: say so in the submission. | `#general` 2026-09-28 |
| Tools | External LLM APIs are allowed; paid cloud tiers too. Cloud deployment is not required: a credible path to production is. | `#challenge-help` 2026-09-25; `#technical-help` 2026-09-25; `#general` 2026-09-28 |
| Learned component | A prompted or fine-tuned LLM counts if it is defined and evaluated rigorously. The baseline can be rules, TF-IDF with logistic regression or a zero-shot LLM, on the same held-out data, with valid labels and no leakage. | `#technical-help` 2026-09-25; `#general` 2026-09-29 |
| Data | Not all the supplied data has to be used, with a reason. Mock data: "you can use mock data you generate if it's not used for testing" (a mentor, who said he lacked context). | `#general` 2026-09-29 |
| Judged on | Technical Judgment, AI Engineering, Data Engineering, Machine Learning, Data Analytics. "Quality over quantity." | Hub |

Mentors' tips, in their words: quantify why the business problem matters ("juries want to
understand why this is actually a problem worth solving"); branches, small pull requests,
clear commits and tagged versions "show up clearly in the best-practices evaluation"; "depth
beats breadth".

Owners marked "T28" are not assigned yet: Santiago assigns them when T28 starts.

## The four required items

| # | Item | Where it comes from | Owner | Done |
|---|---|---|---|---|
| 1 | Link to the **public** GitHub repository `factored-hackathon-2026-inteligencia-artesanal` | The repository, made public after the checks in "Before the repository goes public" | Santiago (T28) | [ ] |
| 2 | Link to where the tool is **deployed** | The Render service of `docs/operations.md` | Santiago (T15, T26) | [ ] |
| 3 | A **4-6 slide** presentation | `docs/submission/slides_outline.md` | T28 | [ ] |
| 4 | A **video pitch of at most 3 minutes, in English**, showing the working solution and the core architecture decisions | `docs/submission/video_script.md` | T28 | [ ] |

## Before the repository goes public

Making it public publishes the whole git history, not only the current files.

- [x] `gitleaks` on the full history is green on `main` (CI job `secrets`). Checked on
      2026-10-05 at `f32243c`; check again on the freeze commit.
- [x] `uv run poe secrets-scan` passes on a fresh clone (2026-10-05, clone of `f32243c`).
- [x] No file under `private/`, `data/` or `eval/heldout/` was ever committed (2026-10-05):
      `git log --all --diff-filter=A --name-only -- private data eval/heldout` prints nothing.
- [x] The held-out set is represented only by its sha256 manifest (and the reports of its runs,
      which hold case ids, outcomes and reasons, no conversation).
- [ ] The access code of the public service is in no committed file (the pre-commit hook
      compares staged files with the local `.env` value of `DEMO_ACCESS_CODE`).

## The team in the repository

The organizers take the team from the repository's contributors, so the list must show it.

- [ ] Every member named in `README.md` has at least one commit of their own on `main`. A
      `Co-authored-by` line is not enough: GitHub's contributor list counts authored commits.
      Check with `gh api repos/<owner>/<repo>/contributors --jq '.[].login'`.
- [ ] A member who cannot push yet is added as a collaborator by the owner account first.
- [ ] The list also shows two accounts that are not people: the team's shared account, which
      owns the repository and made its initial commit, and the AI coding assistant, which
      authored three commits (2026-10-05). The email names them so nobody counts five members.
- [ ] The freeze commit is tagged (Task 26); the tag is what the email and the slides cite.

## Open decision: the data behind the evaluation

- [ ] Owner Santiago (T27). The held-out evaluation runs on the synthetic fixture bank, and a
      mentor said generated data is fine "if it's not used for testing". Either ask a mentor
      with our context (public repository, no restricted data in it, a labeled test fixture
      for write correctness as the problem statement allows) or run part of the evaluation on
      the curated data locally (T19, now in T29). Write the choice and its reason on the
      evaluation slide and in `docs/limitations.md`.

## The deployed service (Task 15, Task 26)

- [ ] The freeze tag is deployed: the commit Render lists as live is the tagged one
      (`docs/operations.md`, "Deploys"; the service itself does not report its commit).
- [ ] Auto-deploy is Off in a way a later push cannot undo, so a later merge cannot restart
      or change the demo: in `render.yaml` (set by the freeze commit), or in the dashboard
      with the Blueprint's Auto Sync off too (`docs/operations.md`, "At the freeze").
- [ ] `uv run poe smoke <public URL>` ends with `OK: verified dispute`, then the demo is reset
      (`docs/operations.md`, "Reset the demo") so the judges start from a clean state.
- [ ] `DEMO_ACCESS_CODE` is set, and the code is in the email and on the slide with the URL,
      not in the repository.
- [ ] If the real model is on: the OpenAI project has a monthly budget set by the key owner,
      and `LLM_SPEND_LIMIT_USD` is set on purpose.
- [ ] The email says the first request can take about a minute (the free instance sleeps after
      15 minutes without traffic) and that the demo state resets when it sleeps.

## Evidence the judges can check

- [x] `README.md` quick start works on a fresh clone (`uv sync --group data`, `uv run poe fixtures`,
      `uv run poe check`).
- [x] Final evaluation report with both systems and the deviations from the pre-registration
      (Task 27), linked from the README: `docs/evidence/final_evaluation.md`.
- [x] `docs/limitations.md` is current (the final evaluation and what it found, 2026-10-05).
- [ ] `docs/operations.md` has the measured image size and memory and the staging record.

## The email

- [ ] To `hackathon.admin@factored.ai`, in English, sent in the morning with margin before
      11:59 PM Colombia time. Watch the inbox for a bounce: the address has bounced before.
- [ ] Contains: repository link, deployed URL, access code, slides, video link, team name.
- [ ] Lists each member with the email they registered with and their GitHub username, and
      says so where the two emails differ.
- [ ] Says that the shared account and the AI assistant in the contributor list are not
      members.
- [ ] Someone other than the sender opened every link from a browser with no session.
