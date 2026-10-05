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

function RuleList({ rules }: { rules: { rule_id: string; description: string }[] | undefined }) {
  if (!rules || rules.length === 0) return <p className="console__empty">Ninguna regla.</p>
  return (
    <>
      {rules.map((rule) => (
        <p key={rule.rule_id} className="console__rule">
          <code>{rule.rule_id}</code> {rule.description}
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
      setError(deniedAccess ? 'Código de acceso incorrecto o faltante.' : null)
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
    <main className="console" lang="es">
      <header className="console__top">
        <a className="brand" href="/">
          <BrandMark />
          <span>Volver al inicio</span>
        </a>
        <span className="console__eyebrow">Lado del banco / T21</span>
      </header>
      <h1>
        Lo que ve el banco,
        <br />
        no solo el cliente.
      </h1>
      <p className="lead">
        Casos escalados a una persona, disputas y bloqueos que el agente resolvió solo — cada uno
        con las reglas que lo dispararon y, para lo escalado, el rastro de ejecución verificado.
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
        <label htmlFor="console-code">Código de acceso (solo si el servidor lo pide)</label>
        <input
          id="console-code"
          type="password"
          autoComplete="off"
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        <button type="submit" disabled={busy}>
          {busy ? 'Cargando…' : 'Actualizar'}
        </button>
      </form>
      {error ? <p role="alert">{error}</p> : null}
      <nav className="console__tabs" aria-label="Secciones">
        {(
          [
            ['handoffs', `Escalados (${handoffs?.length ?? 0})`],
            ['disputes', `Disputas (${disputes?.length ?? 0})`],
            ['blocks', `Tarjetas bloqueadas (${blocks?.length ?? 0})`],
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
        <section className="console__list" aria-label="Lista">
          {tab === 'handoffs' &&
            (handoffs ?? []).map((entry) => (
              <button
                key={entry.handoff.handoff_id}
                type="button"
                className="console__row console__row--pick"
                onClick={() => openHandoff(entry.handoff.handoff_id)}
              >
                <strong>{entry.handoff.customer_id}</strong>
                <span>
                  {entry.handoff.routing.specialty} · {entry.handoff.routing.priority}
                </span>
                <span className="console__meta">{entry.handoff.request}</span>
              </button>
            ))}
          {tab === 'handoffs' && sectionErrors.handoffs ? (
            <p role="alert">No se pudo cargar lo escalado.</p>
          ) : tab === 'handoffs' && handoffs?.length === 0 ? (
            <p className="console__empty">Ningún caso escalado todavía.</p>
          ) : null}
          {tab === 'disputes' &&
            (disputes ?? []).map((entry) => (
              <article key={entry.case.dispute_id} className="console__row">
                <strong>{entry.customer_id}</strong>
                <span>
                  {entry.case.reason} · {entry.case.status} · {entry.case.amount}{' '}
                  {entry.case.currency}
                </span>
                {entry.case.sla_due_date ? (
                  <span className="console__meta">Plazo SLA: {entry.case.sla_due_date}</span>
                ) : null}
                <RuleList rules={entry.rule_explanations} />
              </article>
            ))}
          {tab === 'disputes' && sectionErrors.disputes ? (
            <p role="alert">No se pudo cargar las disputas.</p>
          ) : tab === 'disputes' && disputes?.length === 0 ? (
            <p className="console__empty">Ninguna disputa todavía.</p>
          ) : null}
          {tab === 'blocks' &&
            (blocks ?? []).map((entry) => (
              <article key={entry.event.block_id} className="console__row">
                <strong>{entry.customer_id}</strong>
                <span>
                  Tarjeta •••• {entry.event.card_last4} · {entry.event.reason}
                </span>
              </article>
            ))}
          {tab === 'blocks' && sectionErrors.blocks ? (
            <p role="alert">No se pudieron cargar las tarjetas bloqueadas.</p>
          ) : tab === 'blocks' && blocks?.length === 0 ? (
            <p className="console__empty">Ninguna tarjeta bloqueada todavía.</p>
          ) : null}
        </section>
        <section className="console__detail" aria-label="Detalle">
          {tab !== 'handoffs' ? (
            <p className="console__empty">
              El rastro de ejecución solo existe para lo escalado a una persona.
            </p>
          ) : detailError ? (
            <p role="alert">No se pudo abrir ese caso.</p>
          ) : detail ? (
            <>
              <h2>Caso {detail.entry.handoff.handoff_id}</h2>
              <p>{detail.entry.handoff.request}</p>
              <h3>Reglas que dispararon el caso</h3>
              <RuleList rules={detail.entry.rule_explanations} />
              <h3>Rastro de ejecución</h3>
              {detail.records && detail.records.length > 0 ? (
                <ol className="console__steps">
                  {detail.records.map((record) => (
                    <li key={record.record_id}>
                      <strong>{record.tool ?? record.step}</strong>
                      <span>
                        {record.state} · {record.outcome}
                      </span>
                      <span>{record.verified ? 'Verificado' : 'Sin verificación'}</span>
                    </li>
                  ))}
                </ol>
              ) : (
                <p>Sin pasos registrados.</p>
              )}
            </>
          ) : (
            <p className="console__empty">Elige un caso escalado para ver su rastro.</p>
          )}
        </section>
      </div>
    </main>
  )
}
