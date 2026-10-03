// First messages offered per demo persona. They name charges of the synthetic fixture bank, and
// tests/orchestrator/test_api_acceptance.py checks each one still reaches the question it expects.
import type { Language } from '../api/contracts.gen.ts'
import data from './scenarios.json'

export interface Scenario {
  label: string
  text: string
}

const personas: Record<string, readonly Scenario[]> = data.personas
const fallback: Record<Language, readonly Scenario[]> = data.fallback

/** The persona's own examples, or generic openings to complete when the persona is unknown. */
export function scenariosFor(firstName: string, country: string, language: Language) {
  return personas[`${firstName}/${country}`] ?? fallback[language]
}
