# Video script

Skeleton from Task 15; Task 28 records it. The organizers ask for a short video pitch that
shows the **working solution** and explains the **core architecture decisions**
(`docs/challenge/kickoff.md`). Target length: 3 minutes.

## Before recording

1. Reset the demo so every persona is unused (`docs/operations.md`, "Reset the demo").
2. Open the public URL once and wait for it to answer: a free instance that slept takes
   about a minute to wake.
3. Have the access code at hand; do not show it being typed if the video is public.
4. Record against the deployed service, not a local one: the video must show what the judges
   will open.
5. Each persona can be used once per reset for a dispute. A second dispute by the same
   persona is escalated to a person (rule `DSP-ESC-02`), which is correct but not the scene
   you want. The table below uses each persona once.

## Scenes

| Time | Scene | What is on screen | What is said |
|---|---|---|---|
| 0:00-0:20 | The problem | Slide 1 | What a customer with an unrecognized charge goes through, and what it costs the bank. |
| 0:20-1:05 | A dispute in Spanish | The chat, persona Mariana | The agent finds the charge, asks whether she recognizes it, asks for confirmation, creates the dispute and offers to block the card. Point at the confirmation step. |
| 1:05-1:35 | Portuguese, with the card block | The chat, persona Rafael | The same flow in Portuguese; this time the block is accepted. Two writes, each with its own confirmation. |
| 1:35-1:55 | Knowing when not to act | The chat, persona Carlos | A high-risk charge is not disputed by the agent: it is handed to a person with a reference. |
| 1:55-2:35 | Why it can be trusted | Slide 3 | The model only interprets; code decides. Identity from the session, confirmation token, read-back. |
| 2:35-2:55 | The numbers | Slide 4 | Proposed system against the LLM-only baseline on the held-out set, failures included. |
| 2:55-3:00 | Close | Slide 6 | What is left before production, in one sentence. |

## Utterances that work on the fixture bank

They are the ones of `tests/orchestrator/test_api_acceptance.py`, so the outcome is known.

| Persona | Type this | Then | Outcome |
|---|---|---|---|
| Mariana | `Hola, tengo un cargo de 2,450 pesos en ELECTROMUNDO que no reconozco` | `No fui yo`, `Sí, confirmo`, `No, no la bloquees.` | Dispute created; block declined |
| Rafael | `Oi, apareceu uma compra de 32.500 pesos na GAMESTORE DIGITAL que eu não fiz. Não reconheço essa compra.` | `Não, não reconheço.`, `Sim, confirmo.`, `Sim, pode bloquear o cartão.` | Dispute created; card blocked |
| Carlos | `No reconozco el cargo de 9.800.000 COP en LUXURY WATCHES INTL` | `No fui yo` | Handoff to a person with a reference |
| Sofía | `Hola, me aparece un cargo de PAYPAL SPOTIFYMX de 129 pesos y no sé qué es` | `Sí, fui yo` | No dispute: she recognizes it |

With the real model on, other phrasings work too. Rehearse the exact ones you will record:
the acceptance tests cover the lines above only with the keyword interpreter.

## After recording

- Reset the demo again before the email goes out.
- Check the video shows no access code, token or anything from `.env`.
