import type { Language } from './api/contracts.gen.ts'

/** A signed-in customer as the UI knows them: an opaque token, never a customer id. */
export interface ChatSession {
  token: string
  expiresAt: string
  firstName: string
  country: string
  language: Language
}
