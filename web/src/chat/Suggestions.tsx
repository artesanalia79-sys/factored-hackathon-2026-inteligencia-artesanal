import { ArrowUpRight } from '@phosphor-icons/react'
import { useId } from 'react'
import type { Scenario } from '../demo/scenarios.ts'

interface Props {
  title: string
  scenarios: readonly Scenario[]
  onPick: (text: string) => void
}

/** Demo openings. Picking one fills the composer; nothing is sent until the customer sends it. */
export function Suggestions({ title, scenarios, onPick }: Props) {
  const titleId = useId()
  return (
    <section className="suggestions" aria-labelledby={titleId}>
      <h2 id={titleId} className="suggestions__title">
        {title}
      </h2>
      <ul className="suggestions__list">
        {scenarios.map((scenario) => (
          <li key={scenario.text}>
            <button type="button" className="suggestion" onClick={() => onPick(scenario.text)}>
              <span className="suggestion__label">
                {scenario.label}
                <ArrowUpRight size={16} aria-hidden="true" />
              </span>
              <span className="suggestion__text">{scenario.text}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
