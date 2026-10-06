import { useRef, useState } from 'react'
import type { ComparisonBundle, ComparisonRun } from '../api/contracts.gen.ts'
import { BrandMark } from '../BrandMark.tsx'
import { CREDIT_EN, PRODUCT_NAME } from '../i18n.ts'
import { MAX_FILE_BYTES, parseComparison } from './load.ts'
import './comparison.css'

const SYSTEMS = ['baseline_llm_only', 'proposed'] as const
const LABELS = { baseline_llm_only: 'Naive agent', proposed: 'Controlled agent' }

// The page is English; the recorded conversation is in the case's Spanish or Portuguese, and a
// screen reader needs to know which. The bundle does not say; a dev case id does
// (`dev-<category>-<language>-<country>-<number>`, as every case in eval/dev), and only dev cases
// are exported. Any other id is marked unknown (`lang=""`), never English.
function dialogueLanguage(caseId: string): string {
  const match = /-(es|pt)-([a-z]{2})-\d+$/.exec(caseId)
  return match ? `${match[1]}-${match[2]!.toUpperCase()}` : ''
}

function RunPanel({
  run,
  turn,
  system,
}: {
  run: ComparisonRun | undefined
  turn: number
  system: (typeof SYSTEMS)[number]
}) {
  const current = run?.turns[turn]
  const result = run?.result
  const verified = result?.verified_actions ?? []
  const unsafe = result?.unsafe_events ?? []
  const dialogue = result ? dialogueLanguage(result.case_id) : ''
  return (
    <section className="comparison__column" aria-label={LABELS[system]}>
      <header>
        <p className="comparison__eyebrow">
          {system === 'proposed' ? '02 / With controls' : '01 / LLM only'}
        </p>
        <h2>{LABELS[system]}</h2>
        <p className="comparison__meta">
          {run?.system_name ?? 'No run for this case and repeat'}
        </p>
      </header>
      {result ? (
        <>
          <dl className="comparison__metrics">
            <div>
              <dt>Final outcome</dt>
              <dd>{result.final_outcome}</dd>
            </div>
            <div>
              <dt>Total cost · USD</dt>
              <dd>{Number(result.cost_usd_total ?? 0).toFixed(6)}</dd>
            </div>
            <div>
              <dt>Total latency · ms</dt>
              <dd>{(result.latencies_ms ?? []).reduce((a, b) => a + b, 0).toFixed(1)}</dd>
            </div>
            <div>
              <dt>Turns</dt>
              <dd>{result.turns_used}</dd>
            </div>
          </dl>
          <div className="comparison__evidence">
            <p>
              <strong>Verified actions:</strong>{' '}
              {verified.length ? verified.join(', ') : 'None'}
            </p>
            <p className={unsafe.length ? 'comparison__unsafe' : ''}>
              <strong>Unsafe events:</strong>{' '}
              {unsafe.length ? unsafe.join(', ') : 'None detected'}
            </p>
            <p>
              <strong>Safe automated resolution:</strong>{' '}
              {result.safe_automated_resolution ? 'Yes' : 'No'}
            </p>
          </div>
          {current ? (
            <div className="comparison__turn">
              <h3>Turn {turn + 1}</h3>
              <p className="comparison__speaker">Customer</p>
              <p className="comparison__message" lang={dialogue}>{current.user_text}</p>
              <p className="comparison__speaker">
                Recorded reply · may contain unverified claims
              </p>
              <p className="comparison__message comparison__reply" lang={dialogue}>
                {current.reply_text}
              </p>
              <h3>Execution evidence</h3>
              {current.steps.length === 0 ? (
                <p>No steps recorded.</p>
              ) : (
                <ol className="comparison__steps">
                  {current.steps.map((step, index) => (
                    <li key={index}>
                      <strong>{step.tool ?? step.step}</strong>
                      <span>
                        {step.state} · {step.outcome}
                      </span>
                      <span>{step.verified ? 'Verified' : 'Not verified'}</span>
                      {step.rule_ids.length > 0 ? (
                        <span>Rules: {step.rule_ids.join(', ')}</span>
                      ) : null}
                      {step.model ? <span>Model: {step.model}</span> : null}
                    </li>
                  ))}
                </ol>
              )}
            </div>
          ) : (
            <p className="comparison__empty">This run has no turn {turn + 1}.</p>
          )}
        </>
      ) : (
        <p className="comparison__empty">Load a file with both systems to compare them.</p>
      )}
    </section>
  )
}

export default function ComparisonScreen() {
  const [bundle, setBundle] = useState<ComparisonBundle | null>(null)
  const [caseId, setCaseId] = useState('')
  const [repeat, setRepeat] = useState(0)
  const [turn, setTurn] = useState(0)
  const [error, setError] = useState(false)
  const [busy, setBusy] = useState(false)
  const generation = useRef(0)
  const cases = [...new Set(bundle?.runs.map((run) => run.result.case_id) ?? [])].sort()
  const repeats = [
    ...new Set(
      bundle?.runs
        .filter((run) => run.result.case_id === caseId)
        .map((run) => run.result.repeat_index) ?? [],
    ),
  ].sort((a, b) => a - b)
  const runs = SYSTEMS.map((system) =>
    bundle?.runs.find(
      (run) =>
        run.result.case_id === caseId &&
        run.result.repeat_index === repeat &&
        run.result.system === system,
    ),
  )
  const turns = Math.max(0, ...runs.map((run) => run?.turns.length ?? 0))

  async function loadText(text: string) {
    const request = ++generation.current
    setBusy(true)
    setError(false)
    setBundle(null)
    try {
      const next = parseComparison(text)
      if (request !== generation.current) return
      const first = next.runs[0]!
      setBundle(next)
      setCaseId(first.result.case_id)
      setRepeat(first.result.repeat_index)
      setTurn(0)
    } catch {
      if (request === generation.current) setError(true)
    } finally {
      if (request === generation.current) setBusy(false)
    }
  }

  async function openFile(file: File | undefined) {
    if (!file) return
    if (file.size > MAX_FILE_BYTES) {
      setBundle(null)
      setError(true)
      setBusy(false)
      return
    }
    await loadText(await file.text())
  }

  // One real recorded case (docs/comparison.md, "The bundled demo example"): the only `comparison.json`
  // this repo commits, as a deliberate, reviewed exception to "never commit run artifacts" — so
  // a judge opening the public site has something to click without running the harness
  // themselves. Fetched from the same origin, through the same parseComparison() validation as
  // a hand-picked file: this path carries no more trust than the file input does.
  async function loadDemo() {
    setBusy(true) // immediate feedback while the fetch itself is in flight
    try {
      const response = await fetch('/demo-comparison.json')
      if (!response.ok) throw new Error('demo fetch failed')
      await loadText(await response.text())
    } catch {
      setError(true)
      setBusy(false)
    }
  }

  return (
    <main className="comparison" lang="en">
      <header className="comparison__top">
        <a className="brand" href="/" aria-label={`${PRODUCT_NAME}, back to home`}>
          <BrandMark />
          <span className="brand__name">{PRODUCT_NAME}</span>
        </a>
        <span className="comparison__eyebrow">Lab / T22</span>
      </header>
      <h1>
        One conversation.
        <br />
        Two ways to act.
      </h1>
      <p className="lead">Compare the replies with the evidence of what each agent did.</p>
      <p>
        Replay of dev cases. The file opens in this browser; it is not sent to the server and
        triggers no actions.
      </p>
      <div className="comparison__loader">
        <label htmlFor="comparison-file">Open comparison.json · 8 MB max</label>
        <div className="comparison__loader-row">
          <input
            id="comparison-file"
            type="file"
            accept=".json,application/json"
            onChange={(event) => {
              void openFile(event.target.files?.[0])
              event.target.value = ''
            }}
          />
          <span className="comparison__loader-or">or</span>
          <button
            type="button"
            className="button button--secondary button--small"
            disabled={busy}
            onClick={() => void loadDemo()}
          >
            Load demo example
          </button>
        </div>
        {busy ? <output>Reading file…</output> : null}
        {error ? (
          <p role="alert">
            Could not open the file. Use a valid comparison.json from the harness (up to 500
            runs).
          </p>
        ) : null}
      </div>
      {bundle ? (
        <>
          <aside className="comparison__provenance" aria-label="Provenance">
            <strong>
              {bundle.simulated
                ? 'SIMULATION · scripted agents, not real results'
                : 'RECORDED RUN · check the provider and model'}
            </strong>
            <p>
              {bundle.suite_id} · {bundle.cost_assumptions}
            </p>
            <p className="comparison__hash">Case-set SHA-256: {bundle.case_set_sha256}</p>
            <p>
              Data from the local file. Metrics correspond to the whole run, not only the
              visible turn.
            </p>
          </aside>
          <div className="comparison__controls">
            <div className="comparison__field">
              <label htmlFor="comparison-case">Case</label>
              <select
                id="comparison-case"
                value={caseId}
                onChange={(event) => {
                  const id = event.target.value
                  setCaseId(id)
                  setRepeat(
                    Math.min(
                      ...bundle.runs
                        .filter((run) => run.result.case_id === id)
                        .map((run) => run.result.repeat_index),
                    ),
                  )
                  setTurn(0)
                }}
              >
                {cases.map((id) => (
                  <option key={id}>{id}</option>
                ))}
              </select>
            </div>
            <div className="comparison__field">
              <label htmlFor="comparison-repeat">Repeat</label>
              <select
                id="comparison-repeat"
                value={repeat}
                onChange={(event) => {
                  setRepeat(Number(event.target.value))
                  setTurn(0)
                }}
              >
                {repeats.map((index) => (
                  <option key={index} value={index}>
                    {index + 1}
                  </option>
                ))}
              </select>
            </div>
            <div className="comparison__navigation">
              <button
                className="button button--secondary"
                disabled={turn === 0}
                onClick={() => setTurn((value) => value - 1)}
              >
                Previous
              </button>
              <output aria-live="polite">
                {turns ? `Turn ${turn + 1} of ${turns}` : 'No turns'}
              </output>
              <button
                className="button button--secondary"
                disabled={turn + 1 >= turns}
                onClick={() => setTurn((value) => value + 1)}
              >
                Next
              </button>
            </div>
          </div>
          <p>
            Agents may ask different questions. The turn number is aligned; each column keeps
            its own simulated customer's reply.
          </p>
          <div className="comparison__grid">
            {SYSTEMS.map((system, index) => (
              <RunPanel key={system} system={system} run={runs[index]} turn={turn} />
            ))}
          </div>
        </>
      ) : (
        <section className="comparison__empty" aria-label="How to start">
          <h2>Open a run to start</h2>
          <p>
            Generate a free simulation with <code>uv run poe eval-smoke</code> and open{' '}
            <code>eval/runs/smoke/comparison.json</code>.
          </p>
          <p>
            You can also open the dev file exported by the harness with the real agents.
          </p>
        </section>
      )}
      <p className="comparison__credit">{CREDIT_EN}</p>
    </main>
  )
}
