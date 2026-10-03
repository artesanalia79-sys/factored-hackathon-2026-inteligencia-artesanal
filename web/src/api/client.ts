// The only place the UI talks to the API. Bodies are typed with the generated contracts, so a
// request cannot carry a field the server does not accept (the server rejects `customer_id`
// anyway). Identity travels only as the session token in the Authorization header.
import type {
  ChatTurnRequest,
  ChatTurnResponse,
  LoginRequest,
  LoginResponse,
  PersonaResponse,
  TransactionView,
  VerifyRequest,
  VerifyResponse,
} from './contracts.gen.ts'

// A turn may call the LLM (8 s timeout server side) and several tools.
const TIMEOUT_MS = 30_000

/** A failed call. `code` is the server's safe error code, `network` when nothing came back. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string | null
  readonly retryAfterSeconds: number | null

  constructor(status: number, code: string | null, retryAfterSeconds: number | null = null) {
    super(code ?? `HTTP ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.retryAfterSeconds = retryAfterSeconds
  }
}

interface CallOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  token?: string
}

async function call<T>(path: string, { method = 'GET', body, token }: CallOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (token !== undefined) headers.Authorization = `Bearer ${token}`
  let response: Response
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
      signal: AbortSignal.timeout(TIMEOUT_MS),
    })
  } catch {
    throw new ApiError(0, 'network')
  }
  if (!response.ok) throw await errorOf(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

// Errors look like {"detail": {"error": "invalid_credentials", "retry_after_seconds": 60}}.
async function errorOf(response: Response): Promise<ApiError> {
  let detail: unknown = null
  try {
    detail = ((await response.json()) as { detail?: unknown }).detail
  } catch {
    // not JSON: the status code is all there is
  }
  if (detail === null || typeof detail !== 'object' || Array.isArray(detail)) {
    return new ApiError(response.status, null)
  }
  const { error, retry_after_seconds: retry } = detail as Record<string, unknown>
  return new ApiError(
    response.status,
    typeof error === 'string' ? error : null,
    typeof retry === 'number' ? retry : null,
  )
}

export const api = {
  personas: () => call<PersonaResponse[]>('/api/auth/personas'),
  login: (body: LoginRequest) => call<LoginResponse>('/api/auth/login', { method: 'POST', body }),
  verify: (body: VerifyRequest) =>
    call<VerifyResponse>('/api/auth/verify', { method: 'POST', body }),
  logout: (token: string) => call<void>('/api/auth/logout', { method: 'POST', token }),
  turn: (token: string, body: ChatTurnRequest) =>
    call<ChatTurnResponse>('/api/chat/turn', { method: 'POST', body, token }),
  transactions: (token: string) =>
    call<TransactionView[]>('/api/chat/transactions', { token }),
}
