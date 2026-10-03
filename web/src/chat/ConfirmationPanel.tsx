import { Check, ShieldCheck } from '@phosphor-icons/react'
import { type Ref, useEffect, useId, useState } from 'react'
import type { ConfirmationView } from '../api/contracts.gen.ts'
import type { Copy } from '../i18n.ts'

/**
 * How long a new question ignores its buttons. The card block offer replaces the dispute
 * question in the same place, so the second click of a double click on "Confirmar" would land
 * on the new "Confirmar": longer than a double click (500 ms by default on Windows), shorter
 * than reading the question.
 */
const ARMING_MS = 600

interface Props {
  view: ConfirmationView
  copy: Copy
  /** Receives focus when the question appears: its name and facts are read out together. */
  panelRef: Ref<HTMLElement>
  onAnswer: (text: string) => void
}

/**
 * The write the agent asks about, with its exact arguments, before the customer answers. The
 * values are the question's own strings from the backend; this only labels them. The buttons
 * answer in the chat like a typed "Sí" or "No": the server issues the confirmation token at the
 * yes, for the arguments shown here. Mount one per question (a `key`), so each one waits
 * `ARMING_MS` before it takes an answer.
 */
export function ConfirmationPanel({ view, copy, panelRef, onAnswer }: Props) {
  const titleId = useId()
  const factsId = useId()
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    const timer = window.setTimeout(() => setArmed(true), ARMING_MS)
    return () => window.clearTimeout(timer)
  }, [])
  if (view.action !== 'create_dispute' && view.action !== 'block_card') return null
  const facts: [string, string | null | undefined][] =
    view.action === 'create_dispute'
      ? [
          [copy.fields.merchant, view.merchant],
          [copy.fields.amount, view.amount],
          [copy.fields.date, view.date],
          [copy.fields.card, view.card_last4],
          [copy.fields.channel, view.channel],
          [copy.fields.reason, view.reason],
        ]
      : [[copy.fields.card, view.card_last4]]

  function answer(text: string) {
    if (armed) onAnswer(text)
  }

  return (
    <section
      ref={panelRef}
      tabIndex={-1}
      className="confirm"
      aria-labelledby={titleId}
      aria-describedby={factsId}
    >
      <div className="confirm__head">
        <span className="confirm__icon" aria-hidden="true">
          <ShieldCheck weight="fill" size={22} />
        </span>
        <div>
          <h2 id={titleId} className="confirm__title">
            {copy.confirmTitle}
          </h2>
          <p className="confirm__action">{copy.confirmActions[view.action]}</p>
        </div>
      </div>
      <dl id={factsId} className="confirm__facts">
        {facts
          .filter(([, value]) => value !== null && value !== undefined)
          .map(([label, value]) => (
            <div key={label} className="fact">
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
      </dl>
      <div className="confirm__foot">
        <p className="confirm__hint">{copy.confirmHint}</p>
        <div className="confirm__buttons">
          {/* aria-disabled, not disabled: the buttons stay in the tab order while arming. */}
          <button
            type="button"
            className="button button--secondary"
            aria-disabled={!armed}
            onClick={() => answer(copy.cancelWords)}
          >
            {copy.cancel}
          </button>
          <button
            type="button"
            className="button button--primary"
            aria-disabled={!armed}
            onClick={() => answer(copy.confirmWords)}
          >
            <Check weight="bold" aria-hidden="true" />
            {copy.confirm}
          </button>
        </div>
      </div>
    </section>
  )
}
