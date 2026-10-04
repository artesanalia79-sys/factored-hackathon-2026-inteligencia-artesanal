import type { ComparisonBundle } from '../api/contracts.gen.ts'

/** Type guard implemented by the schema-generated standalone validator. */
export default function validate(value: unknown): value is ComparisonBundle
