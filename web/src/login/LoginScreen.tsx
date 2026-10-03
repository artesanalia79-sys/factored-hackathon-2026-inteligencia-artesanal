import { ArrowLeft, Check, Info, Warning } from '@phosphor-icons/react'
import { type FormEvent, useEffect, useId, useRef, useState } from 'react'
import { ApiError, api } from '../api/client.ts'
import { type Language, LIMITS, type LoginResponse, type PersonaResponse } from '../api/contracts.gen.ts'
import { BrandMark } from '../BrandMark.tsx'
import { COPY, type Copy, countryName } from '../i18n.ts'
import type { ChatSession } from '../session.ts'

type Personas =
  | { kind: 'loading' }
  | { kind: 'failed' }
  | { kind: 'ready'; list: readonly PersonaResponse[] }

/** What went wrong, kept as a kind and worded at render time, so it follows the language. */
type LoginError =
  | {
      kind:
        | 'accessCodeRequired'
        | 'accessCodeWrong'
        | 'personaUnavailable'
        | 'invalidCode'
        | 'networkError'
    }
  | { kind: 'tooManyAttempts'; seconds: number }

interface Props {
  language: Language
  notice: string | null
  /** The demo's shared access code (T15), kept by the app between sign-ins. */
  accessCode: string
  onAccessCodeChange: (code: string) => void
  onLanguageChange: (language: Language) => void
  onSignedIn: (session: ChatSession) => void
}

// What a deployment with a shared access code answers to a login without the right one.
const ACCESS_CODE_REQUIRED = 'access_code_required'

function loginError(error: unknown, step: 'persona' | 'code'): LoginError {
  if (error instanceof ApiError) {
    if (error.status === 429) return { kind: 'tooManyAttempts', seconds: error.retryAfterSeconds ?? 0 }
    if (error.status === 401 && step === 'code') return { kind: 'invalidCode' }
  }
  return { kind: 'networkError' }
}

function errorText(error: LoginError, copy: Copy): string {
  return error.kind === 'tooManyAttempts' ? copy.tooManyAttempts(error.seconds) : copy[error.kind]
}

export function LoginScreen({
  language,
  notice,
  accessCode,
  onAccessCodeChange,
  onLanguageChange,
  onSignedIn,
}: Props) {
  const copy = COPY[language]
  const [personas, setPersonas] = useState<Personas>({ kind: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const [selected, setSelected] = useState<PersonaResponse | null>(null)
  const [challenge, setChallenge] = useState<LoginResponse | null>(null)
  const [code, setCode] = useState('')
  // Shown once the server asked for the access code, or when one is already known.
  const [askAccessCode, setAskAccessCode] = useState(accessCode !== '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<LoginError | null>(null)
  const accessCodeInvalid = error?.kind === 'accessCodeWrong'
  const codeHeading = useRef<HTMLHeadingElement>(null)
  const personaHeading = useRef<HTMLLegendElement>(null)
  const accessField = useRef<HTMLInputElement>(null)
  const ids = {
    title: useId(),
    code: useId(),
    error: useId(),
    lead: useId(),
    access: useId(),
    accessHint: useId(),
  }

  useEffect(() => {
    let current = true
    api.personas().then(
      (list) => current && setPersonas({ kind: 'ready', list }),
      () => current && setPersonas({ kind: 'failed' }),
    )
    return () => {
      current = false
    }
  }, [attempt])

  useEffect(() => {
    if (challenge !== null) codeHeading.current?.focus()
  }, [challenge])

  async function requestCode(event: FormEvent) {
    event.preventDefault()
    if (selected === null || busy) return
    setBusy(true)
    setError(null)
    const presented = accessCode.trim()
    try {
      setChallenge(
        await api.login(
          presented === ''
            ? { persona_id: selected.persona_id }
            : { persona_id: selected.persona_id, access_code: presented },
        ),
      )
      setCode('')
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === ACCESS_CODE_REQUIRED) {
        setAskAccessCode(true)
        setError({ kind: presented === '' ? 'accessCodeRequired' : 'accessCodeWrong' })
        requestAnimationFrame(() => accessField.current?.focus())
      } else if (failure instanceof ApiError && failure.status === 401) {
        // Persona ids are keyed by the server's secret: after a redeploy the list on screen is
        // stale. Load it again instead of blaming the connection.
        setSelected(null)
        setPersonas({ kind: 'loading' })
        setAttempt((value) => value + 1)
        setError({ kind: 'personaUnavailable' })
      } else {
        setError(loginError(failure, 'persona'))
      }
    } finally {
      setBusy(false)
    }
  }

  async function signIn(event: FormEvent) {
    event.preventDefault()
    if (selected === null || challenge === null || busy || code.trim() === '') return
    setBusy(true)
    setError(null)
    try {
      const verified = await api.verify({ challenge_id: challenge.challenge_id, code: code.trim() })
      onSignedIn({
        token: verified.token,
        expiresAt: verified.expires_at,
        firstName: verified.first_name,
        country: selected.country,
        language,
      })
    } catch (failure) {
      setError(loginError(failure, 'code'))
      setBusy(false)
    }
  }

  function choosePersonaAgain() {
    setChallenge(null)
    setCode('')
    setError(null)
    requestAnimationFrame(() => personaHeading.current?.focus())
  }

  return (
    <div className="login">
      <header className="topbar">
        <div className="brand">
          <BrandMark />
          <span className="brand__name">{copy.product}</span>
        </div>
        <fieldset className="segmented">
          <legend className="sr-only">{copy.chooseLanguage}</legend>
          {(['es', 'pt'] as const).map((code) => (
            <button
              key={code}
              type="button"
              lang={COPY[code].locale}
              aria-pressed={language === code}
              onClick={() => onLanguageChange(code)}
            >
              {copy.languageName[code]}
            </button>
          ))}
        </fieldset>
      </header>

      <main className="login__main">
        <section className="login__intro" aria-labelledby={ids.title}>
          <h1 id={ids.title} className="display">
            {copy.loginTitle}
          </h1>
          <p className="lead">{copy.loginLead}</p>
          <ul className="promises">
            {copy.promises.map((promise) => (
              <li key={promise}>
                <Check weight="bold" aria-hidden="true" />
                {promise}
              </li>
            ))}
          </ul>
        </section>

        <section className="panel login__panel" aria-label={copy.signIn}>
          {notice !== null && (
            <output className="notice">
              <Info aria-hidden="true" />
              {notice}
            </output>
          )}

          {challenge === null || selected === null ? (
            <form onSubmit={requestCode} noValidate>
              {askAccessCode && (
                <div className="field field--first">
                  <label htmlFor={ids.access}>{copy.accessCodeLabel}</label>
                  {/* A password field: a screen recording of the demo does not show the code. */}
                  <input
                    ref={accessField}
                    id={ids.access}
                    className="input"
                    type="password"
                    autoComplete="off"
                    maxLength={LIMITS.LoginRequest.access_code.maxLength}
                    value={accessCode}
                    onChange={(event) => onAccessCodeChange(event.target.value)}
                    aria-describedby={
                      accessCodeInvalid ? `${ids.accessHint} ${ids.error}` : ids.accessHint
                    }
                    aria-invalid={accessCodeInvalid}
                  />
                  <p id={ids.accessHint} className="field__hint">
                    {copy.accessCodeHint}
                  </p>
                </div>
              )}
              <fieldset className="personas" disabled={busy} aria-describedby={error ? ids.error : undefined}>
                <legend ref={personaHeading} tabIndex={-1} className="panel__title">
                  {copy.personasLegend}
                </legend>
                {personas.kind === 'loading' && (
                  <div className="personas__grid" aria-busy="true" aria-label={copy.personasLoading}>
                    {[0, 1, 2, 3, 4, 5].map((index) => (
                      <span key={index} className="persona persona--skeleton" />
                    ))}
                  </div>
                )}
                {personas.kind === 'failed' && (
                  <div className="alert" role="alert">
                    <Warning aria-hidden="true" />
                    <span>{copy.personasFailed}</span>
                    <button
                      type="button"
                      className="button button--secondary button--small"
                      onClick={() => {
                        setPersonas({ kind: 'loading' })
                        setAttempt((value) => value + 1)
                      }}
                    >
                      {copy.retry}
                    </button>
                  </div>
                )}
                {personas.kind === 'ready' && (
                  <div className="personas__grid">
                    {personas.list.map((persona) => (
                      <label key={persona.persona_id} className="persona">
                        <input
                          type="radio"
                          name="persona"
                          value={persona.persona_id}
                          checked={selected?.persona_id === persona.persona_id}
                          onChange={() => setSelected(persona)}
                        />
                        <span className="avatar" aria-hidden="true">
                          {persona.first_name.slice(0, 1)}
                        </span>
                        <span className="persona__text">
                          <span className="persona__name">{persona.first_name}</span>
                          <span className="persona__meta">
                            {countryName(persona.country, copy)}
                            {persona.language !== null &&
                              ` · ${copy.profileLanguageName[persona.language]}`}
                          </span>
                        </span>
                      </label>
                    ))}
                  </div>
                )}
              </fieldset>
              {error !== null && (
                <p id={ids.error} className="field-error" role="alert">
                  <Warning aria-hidden="true" />
                  {errorText(error, copy)}
                </p>
              )}
              <button
                type="submit"
                className="button button--primary button--wide"
                disabled={selected === null || busy}
              >
                {busy ? copy.sendingCode : copy.sendCode}
              </button>
            </form>
          ) : (
            <form onSubmit={signIn} noValidate>
              <h2 ref={codeHeading} tabIndex={-1} className="panel__title">
                {copy.codeTitle(selected.first_name)}
              </h2>
              <p id={ids.lead} className="muted">
                {challenge.mock_otp ? copy.codeLead : copy.codeLeadNoDemo}
              </p>
              {challenge.mock_otp && (
                <div className="democode">
                  <span className="democode__label">{copy.demoCode}</span>
                  <code className="democode__value">{challenge.mock_otp}</code>
                  <button
                    type="button"
                    className="button button--secondary button--small"
                    onClick={() => setCode(challenge.mock_otp ?? '')}
                  >
                    {copy.useCode}
                  </button>
                </div>
              )}
              <div className="field">
                <label htmlFor={ids.code}>{copy.codeLabel}</label>
                <input
                  id={ids.code}
                  className="input input--code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={LIMITS.VerifyRequest.code.maxLength}
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                  aria-invalid={error !== null}
                  aria-describedby={error !== null ? ids.error : ids.lead}
                />
                {error !== null && (
                  <p id={ids.error} className="field-error" role="alert">
                    <Warning aria-hidden="true" />
                    {errorText(error, copy)}
                  </p>
                )}
              </div>
              <button
                type="submit"
                className="button button--primary button--wide"
                disabled={code.trim() === '' || busy}
              >
                {busy ? copy.signingIn : copy.signIn}
              </button>
              <button type="button" className="button button--ghost" onClick={choosePersonaAgain}>
                <ArrowLeft aria-hidden="true" />
                {copy.otherPersona}
              </button>
            </form>
          )}
        </section>
      </main>

      <footer className="login__foot">
        <Info aria-hidden="true" />
        <p>
          {copy.demoNotice}. {copy.credit}
        </p>
      </footer>
    </div>
  )
}
