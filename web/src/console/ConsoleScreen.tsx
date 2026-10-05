import { useEffect, useState } from 'react'
import { ApiError, api } from '../api/client.ts'
import type {
  ConsoleCardBlockEntry,
  ConsoleDisputeEntry,
  ConsoleHandoffDetail,
  ConsoleHandoffEntry,
} from '../api/contracts.gen.ts'
import { BrandMark } from '../BrandMark.tsx'
import './console.css'

type Tab = 'handoffs' | 'disputes' | 'blocks'

// `dateStyle`/`timeStyle` cannot mix with explicit field options (the spec throws), and a plain
// `timeStyle: 'short'` renders an unpadded, AM/PM-less hour in some locales ("2:48", ambiguous).
// Every field named explicitly instead, with a fixed 24-hour clock.
const DATE_TIME = new Intl.DateTimeFormat('en-GB', {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
  timeZone: 'UTC',
})

function when(iso: string): string {
  return `${DATE_TIME.format(new Date(iso))} UTC`
}

function who(customerId: string, customerName: string | null | undefined): string {
  return customerName ? `${customerName} · ${customerId}` : customerId
}

interface RuleRow {
  rule_id: string
  description: string
  decisive?: boolean
}

// Every rule the policy engine checked is shown, in full: it is meant to read as "this is how
// thoroughly we checked", not just "here is the one reason". The decisive one (or ones) is
// highlighted, so "why did this happen" is still a glance, not a read of the whole rulebook.
function RuleList({ rules }: { rules: RuleRow[] | undefined }) {
  if (!rules || rules.length === 0) return <p className="console__empty">No rules.</p>
  return (
    <>
      {rules.map((rule) => (
        <p
          key={rule.rule_id}
          className={rule.decisive ? 'console__rule console__rule--decisive' : 'console__rule'}
        >
          <code>{rule.rule_id}</code>
          {rule.decisive ? <span className="console__rule-tag">Applied in this case</span> : null}{' '}
          {rule.description}
        </p>
      ))}
    </>
  )
}

export default function ConsoleScreen() {
  const [code, setCode] = useState('')
  const [appliedCode, setAppliedCode] = useState<string | null>(null)
  const [request, setRequest] = useState(0)
  const [tab, setTab] = useState<Tab>('handoffs')
  const [handoffs, setHandoffs] = useState<ConsoleHandoffEntry[] | null>(null)
  const [disputes, setDisputes] = useState<ConsoleDisputeEntry[] | null>(null)
  const [blocks, setBlocks] = useState<ConsoleCardBlockEntry[] | null>(null)
  const [sectionErrors, setSectionErrors] = useState({
    handoffs: false,
    disputes: false,
    blocks: false,
  })
  const [detail, setDetail] = useState<ConsoleHandoffDetail | null>(null)
  const [detailError, setDetailError] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // True until the mount fetch settles; a later refresh sets it from the submit handler, never
  // synchronously inside the effect below (that would trigger a second, avoidable render).
  const [busy, setBusy] = useState(true)

  useEffect(() => {
    let active = true
    // Settled, not all-or-nothing: one broken section (a bad rule description, say) must not
    // blank out the other two, which have nothing to do with it.
    Promise.allSettled([
      api.consoleHandoffs(appliedCode),
      api.consoleDisputes(appliedCode),
      api.consoleCardBlocks(appliedCode),
    ]).then(([handoffResult, disputeResult, blockResult]) => {
      if (!active) return
      const deniedAccess = [handoffResult, disputeResult, blockResult].some(
        (result) =>
          result.status === 'rejected' &&
          result.reason instanceof ApiError &&
          result.reason.code === 'access_code_required',
      )
      setError(deniedAccess ? 'Incorrect or missing access code.' : null)
      setHandoffs(handoffResult.status === 'fulfilled' ? handoffResult.value : null)
      setDisputes(disputeResult.status === 'fulfilled' ? disputeResult.value : null)
      setBlocks(blockResult.status === 'fulfilled' ? blockResult.value : null)
      setSectionErrors({
        handoffs: handoffResult.status === 'rejected' && !deniedAccess,
        disputes: disputeResult.status === 'rejected' && !deniedAccess,
        blocks: blockResult.status === 'rejected' && !deniedAccess,
      })
      setBusy(false)
    })
    return () => {
      active = false
    }
  }, [appliedCode, request])

  function openHandoff(handoffId: string) {
    setDetail(null)
    setDetailError(false)
    api.consoleHandoff(appliedCode, handoffId).then(setDetail, () => setDetailError(true))
  }

  return (
    <main className="console" lang="en">
      <header className="console__top">
        <a className="brand" href="/">
          <BrandMark />
          <span>Back to home</span>
        </a>
        <span className="console__eyebrow">Bank side / T21</span>
      </header>
      <h1>
        What the bank sees,
        <br />
        not only the customer.
      </h1>
      <p className="lead">
        Cases escalated to a person, disputes and blocks the agent resolved on its own — each
        with the rules that triggered it and, for escalated cases, the verified execution trace.
      </p>
      <form
        className="console__loader"
        onSubmit={(event) => {
          event.preventDefault()
          setBusy(true)
          setAppliedCode(code || null)
          setRequest((current) => current + 1)
        }}
      >
        <label htmlFor="console-code">Access code (only if the server asks for it)</label>
        <input
          id="console-code"
          type="password"
          autoComplete="off"
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        {/* The browser's dark-mode button is 4.45:1, under WCAG AA; the shared style is not. */}
        <button type="submit" className="button button--secondary" disabled={busy}>
          {busy ? 'Loading…' : 'Refresh'}
        </button>
      </form>
      {error ? <p role="alert">{error}</p> : null}
      <nav className="console__tabs" aria-label="Sections">
        {(
          [
            ['handoffs', `Escalated (${handoffs?.length ?? 0})`],
            ['disputes', `Disputes (${disputes?.length ?? 0})`],
            ['blocks', `Blocked cards (${blocks?.length ?? 0})`],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-pressed={tab === key}
            onClick={() => {
              setTab(key)
              setDetail(null)
              setDetailError(false)
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="console__grid">
        <section className="console__list" aria-label="List">
          {tab === 'handoffs' &&
            (handoffs ?? []).map((entry) => (
              <button
                key={entry.handoff.handoff_id}
                type="button"
                className="console__row console__row--pick"
                onClick={() => openHandoff(entry.handoff.handoff_id)}
              >
                <strong>{who(entry.handoff.customer_id, entry.customer_name)}</strong>
                <span>
                  {entry.handoff.routing.specialty} · {entry.handoff.routing.priority} ·{' '}
                  {when(entry.handoff.created_at)}
                </span>
                <span className="console__meta">{entry.handoff.request}</span>
              </button>
            ))}
          {tab === 'handoffs' && sectionErrors.handoffs ? (
            <p role="alert">Could not load escalated cases.</p>
          ) : tab === 'handoffs' && handoffs?.length === 0 ? (
            <p className="console__empty">No escalated cases yet.</p>
          ) : null}
          {tab === 'disputes' &&
            (disputes ?? []).map((entry) => (
              <article key={entry.case.dispute_id} className="console__row">
                <strong>{who(entry.customer_id, entry.customer_name)}</strong>
                <span>
                  {entry.case.reason} · {entry.case.status} · {entry.case.amount}{' '}
                  {entry.case.currency} · {when(entry.case.created_at)}
                </span>
                {entry.case.sla_due_date ? (
                  <span className="console__meta">SLA due date: {entry.case.sla_due_date}</span>
                ) : null}
                <RuleList rules={entry.rule_explanations} />
              </article>
            ))}
          {tab === 'disputes' && sectionErrors.disputes ? (
            <p role="alert">Could not load disputes.</p>
          ) : tab === 'disputes' && disputes?.length === 0 ? (
            <p className="console__empty">No disputes yet.</p>
          ) : null}
          {tab === 'blocks' &&
            (blocks ?? []).map((entry) => (
              <article key={entry.event.block_id} className="console__row">
                <strong>{who(entry.customer_id, entry.customer_name)}</strong>
                <span>
                  Card •••• {entry.event.card_last4} · {entry.event.reason} ·{' '}
                  {when(entry.event.blocked_at)}
                </span>
              </article>
            ))}
          {tab === 'blocks' && sectionErrors.blocks ? (
            <p role="alert">Could not load blocked cards.</p>
          ) : tab === 'blocks' && blocks?.length === 0 ? (
            <p className="console__empty">No blocked cards yet.</p>
          ) : null}
        </section>
        <section className="console__detail" aria-label="Detail">
          {tab !== 'handoffs' ? (
            <p className="console__empty">
              The execution trace only exists for cases escalated to a person.
            </p>
          ) : detailError ? (
            <p role="alert">Could not open that case.</p>
          ) : detail ? (
            <>
              <h2>Case {detail.entry.handoff.handoff_id}</h2>
              <p className="console__meta">
                {who(detail.entry.handoff.customer_id, detail.entry.customer_name)} ·{' '}
                {when(detail.entry.handoff.created_at)}
              </p>
              <p>{detail.entry.handoff.request}</p>
              <h3>Rules that triggered the case</h3>
              <RuleList rules={detail.entry.rule_explanations} />
              <h3>Execution trace</h3>
              {detail.records && detail.records.length > 0 ? (
                <ol className="console__steps">
                  {detail.records.map((record) => (
                    <li key={record.record_id}>
                      <strong>{record.tool ?? record.step}</strong>
                      <span>
                        {record.state} · {record.outcome}
                      </span>
                      <span>{record.verified ? 'Verified' : 'Not verified'}</span>
                    </li>
                  ))}
                </ol>
              ) : (
                <p>No steps recorded.</p>
              )}
            </>
          ) : (
            <p className="console__empty">Pick an escalated case to see its trace.</p>
          )}
        </section>
      </div>
    </main>
  )
}
