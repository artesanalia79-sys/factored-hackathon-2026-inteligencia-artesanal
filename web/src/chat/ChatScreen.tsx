import { Clock, Info, SignOut, Warning } from '@phosphor-icons/react'
import { useCallback, useEffect, useLayoutEffect, useReducer, useRef, useState } from 'react'
import { ApiError, api } from '../api/client.ts'
import type { Language } from '../api/contracts.gen.ts'
import { BrandMark } from '../BrandMark.tsx'
import { scenariosFor } from '../demo/scenarios.ts'
import { clockTime, COPY, countryName } from '../i18n.ts'
import type { ChatSession } from '../session.ts'
import { Composer } from './Composer.tsx'
import { ConfirmationPanel } from './ConfirmationPanel.tsx'
import { Message, Typing } from './Message.tsx'
import { chatReducer, initialChat } from './state.ts'
import { Suggestions } from './Suggestions.tsx'
import { Transactions } from './Transactions.tsx'

interface Props {
  session: ChatSession
  onLanguageChange: (language: Language) => void
  onSignedOut: (reason: 'expired' | 'signed_out') => void
}

export function ChatScreen({ session, onLanguageChange, onSignedOut }: Props) {
  const [state, dispatch] = useReducer(chatReducer, session.language, initialChat)
  const [draft, setDraft] = useState('')
  const inFlight = useRef(false)
  const scroller = useRef<HTMLElement>(null)
  const composer = useRef<HTMLTextAreaElement>(null)
  const confirmPanel = useRef<HTMLElement>(null)
  const copy = COPY[state.language]
  const scenarios = scenariosFor(session.firstName, session.country, state.language)
  const last = state.items.at(-1)
  const expire = useCallback(() => dispatch({ type: 'expired' }), [])

  // Keep the newest entry in view.
  useLayoutEffect(() => {
    const area = scroller.current
    if (area !== null) area.scrollTop = area.scrollHeight
  }, [state.items.length, state.pending, state.confirmation, state.failed])

  // A question to confirm takes the focus, so a keyboard user lands on what is asked; otherwise
  // focus goes to the composer when nothing has it: right after sign-in, or when the control that
  // had it went away.
  useEffect(() => {
    if (state.pending) return
    if (state.confirmation !== null) confirmPanel.current?.focus()
    else if (document.activeElement === document.body) composer.current?.focus()
  }, [state.pending, state.confirmation])

  /** Send a message typed in the composer, or the answer of the confirmation panel. */
  async function send(text: string, from: 'composer' | 'panel') {
    const message = text.trim()
    if (message === '' || inFlight.current || state.expired) return
    inFlight.current = true
    // Whatever answers a confirmation question, typed or from the panel.
    const answer = state.confirmation !== null
    dispatch({ type: 'sent', text: message })
    // A panel answer leaves a half-written message in the composer alone.
    if (from === 'composer') setDraft('')
    try {
      const reply = await api.turn(session.token, { text: message, language: state.language })
      dispatch({ type: 'replied', reply })
      onLanguageChange(reply.language)
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 401) {
        dispatch({ type: 'expired' })
      } else {
        dispatch({ type: 'failed', answer })
        // Give a message back, unless the customer already started a new one. Never an answer:
        // if the server read it before the reply was lost, it now asks the next question (the
        // card block after the dispute), and the same "Sí" sent again would answer that one,
        // which the customer never saw.
        if (!answer) setDraft((current) => (current === '' ? message : current))
      }
    } finally {
      inFlight.current = false
    }
  }

  function signOut() {
    // Leave at once and revoke the session in the background: with the service asleep or the
    // network down, waiting for the call would leave the button doing nothing for up to 30 s.
    // If the call fails, the session still ends at its expiry.
    onSignedOut('signed_out')
    api.logout(session.token).catch(() => undefined)
  }

  function pick(text: string) {
    setDraft(text)
    requestAnimationFrame(() => {
      const field = composer.current
      if (field === null) return
      field.focus()
      field.setSelectionRange(text.length, text.length)
    })
  }

  return (
    <div className="chat">
      <header className="topbar">
        <div className="brand">
          <BrandMark />
          <span className="brand__name">{copy.product}</span>
        </div>
        <div className="topbar__session">
          <span className="who">
            <span className="avatar avatar--sm" aria-hidden="true">
              {session.firstName.slice(0, 1)}
            </span>
            <span>
              {session.firstName}
              <span className="who__country">{countryName(session.country, copy)}</span>
            </span>
          </span>
          <span className="session-time">
            <Clock aria-hidden="true" />
            {copy.sessionUntil(clockTime(session.expiresAt, copy))}
          </span>
          <button type="button" className="button button--ghost button--small" onClick={signOut}>
            <SignOut aria-hidden="true" />
            {copy.signOut}
          </button>
        </div>
      </header>

      <main className="chat__main">
        {/* Focusable so a keyboard user can scroll back through the conversation. */}
        <section
          className="chat__scroll"
          ref={scroller}
          tabIndex={0}
          aria-label={copy.conversationLabel}
        >
          <div className="chat__column">
            <p className="demo-note">
              <Info aria-hidden="true" />
              {copy.demoNotice}
            </p>
            {state.items.length === 0 && (
              <div className="welcome">
                <h1 className="welcome__title">{copy.greeting(session.firstName)}</h1>
                <p className="lead">{copy.greetingLead}</p>
              </div>
            )}
            <Transactions
              token={session.token}
              copy={copy}
              blockedCards={state.blockedCards}
              onExpired={expire}
              onPick={pick}
            />
            {state.items.length === 0 && (
              <Suggestions title={copy.tryLabel} scenarios={scenarios} onPick={pick} />
            )}
            <div className="log" role="log" aria-live="polite" aria-label={copy.messagesLabel}>
              {state.items.map((item) => (
                <Message key={item.id} item={item} copy={copy} />
              ))}
              {state.pending && <Typing copy={copy} />}
            </div>
            {last?.kind === 'ended' && !state.pending && !state.expired && (
              <Suggestions title={copy.tryAgainLabel} scenarios={scenarios} onPick={pick} />
            )}
          </div>
        </section>

        <div className="chat__dock">
          <div className="chat__column">
            {state.confirmation !== null && !state.pending && (
              <ConfirmationPanel
                // One panel per question: each new one waits before it takes an answer.
                key={last?.id}
                view={state.confirmation}
                copy={copy}
                panelRef={confirmPanel}
                onAnswer={(text) => send(text, 'panel')}
              />
            )}
            {state.failed !== null && (
              <p className="alert" role="alert">
                <Warning aria-hidden="true" />
                <span>{state.failed === 'answer' ? copy.answerFailed : copy.sendFailed}</span>
              </p>
            )}
            {state.expired ? (
              <div className="alert alert--block" role="alert">
                <Warning aria-hidden="true" />
                <span>{copy.sessionEnded}</span>
                <button
                  type="button"
                  className="button button--primary button--small"
                  onClick={() => onSignedOut('expired')}
                >
                  {copy.signInAgain}
                </button>
              </div>
            ) : (
              <Composer
                copy={copy}
                draft={draft}
                canSend={!state.pending}
                inputRef={composer}
                onDraftChange={setDraft}
                onSend={(text) => send(text, 'composer')}
              />
            )}
          </div>
        </div>
      </main>
    </div>
  )
}
