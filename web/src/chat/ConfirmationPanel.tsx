import { Check, ShieldCheck } from '@phosphor-icons/react'
import { type ReactNode, type Ref, useId } from 'react'
import type { ConfirmationView } from '../api/contracts.gen.ts'
import type { Copy } from '../i18n.ts'

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
 * yes, for the arguments shown here.
 */
export function ConfirmationPanel({ view, copy, panelRef, onAnswer }: Props) {
  const titleId = useId()
  const factsId = useId()
  if (view.action !== 'create_dispute' && view.action !== 'block_card') return null
  const card: ReactNode = (
    <>
      <span aria-hidden="true">•••• {view.card_last4}</span>
      <span className="sr-only">{copy.cardEnding(view.card_last4)}</span>
    </>
  )
  const facts: [string, ReactNode][] =
    view.action === 'create_dispute'
      ? [
          [copy.fields.merchant, view.merchant],
          [copy.fields.amount, view.amount],
          [copy.fields.date, view.date],
          [copy.fields.card, card],
          [copy.fields.channel, view.channel],
          [copy.fields.reason, view.reason],
        ]
      : [[copy.fields.card, card]]

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
          <button
            type="button"
            className="button button--secondary"
            onClick={() => onAnswer(copy.cancelWords)}
          >
            {copy.cancel}
          </button>
          <button
            type="button"
            className="button button--primary"
            onClick={() => onAnswer(copy.confirmWords)}
          >
            <Check weight="bold" aria-hidden="true" />
            {copy.confirm}
          </button>
        </div>
      </div>
    </section>
  )
}
