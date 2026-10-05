import { useRef, useState } from 'react'
import type { ComparisonBundle, ComparisonRun } from '../api/contracts.gen.ts'
import { BrandMark } from '../BrandMark.tsx'
import { MAX_FILE_BYTES, parseComparison } from './load.ts'
import './comparison.css'

const SYSTEMS = ['baseline_llm_only', 'proposed'] as const
const LABELS = { baseline_llm_only: 'Agente ingenuo', proposed: 'Agente controlado' }

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
  return (
    <section className="comparison__column" aria-label={LABELS[system]}>
      <header>
        <p className="comparison__eyebrow">
          {system === 'proposed' ? '02 / Con controles' : '01 / LLM solo'}
        </p>
        <h2>{LABELS[system]}</h2>
        <p className="comparison__meta">
          {run?.system_name ?? 'Sin ejecución para este caso y repetición'}
        </p>
      </header>
      {result ? (
        <>
          <dl className="comparison__metrics">
            <div>
              <dt>Resultado final</dt>
              <dd>{result.final_outcome}</dd>
            </div>
            <div>
              <dt>Costo total · USD</dt>
              <dd>{Number(result.cost_usd_total ?? 0).toFixed(6)}</dd>
            </div>
            <div>
              <dt>Latencia total · ms</dt>
              <dd>{(result.latencies_ms ?? []).reduce((a, b) => a + b, 0).toFixed(1)}</dd>
            </div>
            <div>
              <dt>Turnos</dt>
              <dd>{result.turns_used}</dd>
            </div>
          </dl>
          <div className="comparison__evidence">
            <p>
              <strong>Acciones verificadas:</strong>{' '}
              {verified.length ? verified.join(', ') : 'Ninguna'}
            </p>
            <p className={unsafe.length ? 'comparison__unsafe' : ''}>
              <strong>Eventos inseguros:</strong>{' '}
              {unsafe.length ? unsafe.join(', ') : 'Ninguno detectado'}
            </p>
            <p>
              <strong>Resolución automática segura:</strong>{' '}
              {result.safe_automated_resolution ? 'Sí' : 'No'}
            </p>
          </div>
          {current ? (
            <div className="comparison__turn">
              <h3>Turno {turn + 1}</h3>
              <p className="comparison__speaker">Cliente</p>
              <p className="comparison__message">{current.user_text}</p>
              <p className="comparison__speaker">
                Respuesta registrada · puede contener afirmaciones sin verificar
              </p>
              <p className="comparison__message comparison__reply">{current.reply_text}</p>
              <h3>Evidencia de ejecución</h3>
              {current.steps.length === 0 ? (
                <p>Sin pasos registrados.</p>
              ) : (
                <ol className="comparison__steps">
                  {current.steps.map((step, index) => (
                    <li key={index}>
                      <strong>{step.tool ?? step.step}</strong>
                      <span>
                        {step.state} · {step.outcome}
                      </span>
                      <span>{step.verified ? 'Verificado' : 'Sin verificación'}</span>
                      {step.rule_ids.length > 0 ? (
                        <span>Reglas: {step.rule_ids.join(', ')}</span>
                      ) : null}
                      {step.model ? <span>Modelo: {step.model}</span> : null}
                    </li>
                  ))}
                </ol>
              )}
            </div>
          ) : (
            <p className="comparison__empty">Esta ejecución no tiene un turno {turn + 1}.</p>
          )}
        </>
      ) : (
        <p className="comparison__empty">Carga un archivo con ambos sistemas para compararlos.</p>
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

  async function openFile(file: File | undefined) {
    if (!file) return
    const request = ++generation.current
    setBusy(true)
    setError(false)
    setBundle(null)
    try {
      if (file.size > MAX_FILE_BYTES) throw new Error('Too large')
      const next = parseComparison(await file.text())
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

  return (
    <main className="comparison" lang="es">
      <header className="comparison__top">
        <a className="brand" href="/">
          <BrandMark />
          <span>Volver al inicio</span>
        </a>
        <span className="comparison__eyebrow">Laboratorio / T22</span>
      </header>
      <h1>
        Una conversación.
        <br />
        Dos formas de actuar.
      </h1>
      <p className="lead">Compara las respuestas con la evidencia de lo que cada agente hizo.</p>
      <p>
        Replay de casos dev. El archivo se abre en este navegador; no se envía al servidor ni
        ejecuta acciones.
      </p>
      <div className="comparison__loader">
        <label htmlFor="comparison-file">Abrir comparison.json · máximo 8 MB</label>
        <input
          id="comparison-file"
          type="file"
          accept=".json,application/json"
          onChange={(event) => {
            void openFile(event.target.files?.[0])
            event.target.value = ''
          }}
        />
        {busy ? <output>Leyendo archivo…</output> : null}
        {error ? (
          <p role="alert">
            No se pudo abrir el archivo. Usa un comparison.json válido del harness (hasta 500
            ejecuciones).
          </p>
        ) : null}
      </div>
      {bundle ? (
        <>
          <aside className="comparison__provenance" aria-label="Procedencia">
            <strong>
              {bundle.simulated
                ? 'SIMULACIÓN · agentes con guion, no resultados reales'
                : 'EJECUCIÓN REGISTRADA · consulta el proveedor y modelo'}
            </strong>
            <p>
              {bundle.suite_id} · {bundle.cost_assumptions}
            </p>
            <p className="comparison__hash">SHA-256 de casos: {bundle.case_set_sha256}</p>
            <p>
              Datos del archivo local. Las métricas corresponden a toda la ejecución, no solo al
              turno visible.
            </p>
          </aside>
          <div className="comparison__controls">
            <div className="comparison__field">
              <label htmlFor="comparison-case">Caso</label>
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
              <label htmlFor="comparison-repeat">Repetición</label>
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
                Anterior
              </button>
              <output aria-live="polite">
                {turns ? `Turno ${turn + 1} de ${turns}` : 'Sin turnos'}
              </output>
              <button
                className="button button--secondary"
                disabled={turn + 1 >= turns}
                onClick={() => setTurn((value) => value + 1)}
              >
                Siguiente
              </button>
            </div>
          </div>
          <p>
            Los agentes pueden hacer preguntas distintas. Se alinea el número de turno; cada columna
            conserva la respuesta de su cliente simulado.
          </p>
          <div className="comparison__grid">
            {SYSTEMS.map((system, index) => (
              <RunPanel key={system} system={system} run={runs[index]} turn={turn} />
            ))}
          </div>
        </>
      ) : (
        <section className="comparison__empty" aria-label="Cómo empezar">
          <h2>Abre una ejecución para empezar</h2>
          <p>
            Genera una simulación gratuita con <code>uv run poe eval-smoke</code> y abre{' '}
            <code>eval/runs/smoke/comparison.json</code>.
          </p>
          <p>
            También puedes abrir el archivo dev exportado por el harness con los agentes reales.
          </p>
        </section>
      )}
    </main>
  )
}
