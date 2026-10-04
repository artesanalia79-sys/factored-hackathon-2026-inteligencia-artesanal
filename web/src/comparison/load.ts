import type { ComparisonBundle } from '../api/contracts.gen.ts'
import validate from './validate.gen.js'

export const MAX_FILE_BYTES = 8 * 1024 * 1024

export function parseComparison(text: string): ComparisonBundle {
  const value: unknown = JSON.parse(text)
  if (!validate(value) || value.runs.length > 500) throw new Error('Invalid comparison')
  const keys = new Set<string>()
  for (const run of value.runs) {
    const result = run.result
    const key = JSON.stringify([result.case_id, result.repeat_index, result.system])
    if (keys.has(key) || run.turns.length > 50) throw new Error('Invalid runs')
    keys.add(key)
    if (result.turns_used !== run.turns.length) throw new Error('Inconsistent turns')
    if (!Number.isFinite(Number(result.cost_usd_total ?? 0))) throw new Error('Invalid cost')
    if (
      (result.verified_actions ?? []).some(
        (action) => !(result.actions_taken ?? []).includes(action),
      )
    ) {
      throw new Error('Invalid verified actions')
    }
    if (
      result.safe_automated_resolution &&
      (!result.correct ||
        result.final_outcome !== 'automated_resolution' ||
        (result.unsafe_events ?? []).length > 0)
    ) {
      throw new Error('Invalid safe resolution')
    }
    if (run.turns.some((turn) => turn.steps.length > 200)) throw new Error('Too many steps')
    if (
      run.turns.some((turn) =>
        turn.steps.some((step) => step.verified && step.outcome !== 'success'),
      )
    ) {
      throw new Error('Invalid step verification')
    }
  }
  return value
}
