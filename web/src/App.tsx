import { IconContext } from '@phosphor-icons/react'
import { lazy, Suspense, useEffect, useState } from 'react'
import type { Language } from './api/contracts.gen.ts'
import { ChatScreen } from './chat/ChatScreen.tsx'
import { browserLanguage, COPY, PRODUCT_NAME } from './i18n.ts'
import { LoginScreen } from './login/LoginScreen.tsx'
import type { ChatSession } from './session.ts'

const ICONS = { size: 20, weight: 'regular' } as const
const ComparisonScreen = lazy(() => import('./comparison/ComparisonScreen.tsx'))
const ConsoleScreen = lazy(() => import('./console/ConsoleScreen.tsx'))

// The two reviewer pages are in English, like every deliverable but the customer's own
// conversation (docs/submission/checklist.md): the tab title and the document language too, not
// only what the page itself renders. The customer's screens follow the customer's language.
type ReviewerPage = 'compare' | 'console'
const REVIEWER_TITLE: Record<ReviewerPage, string> = {
  compare: 'Side-by-side replay',
  console: 'Bank-side console',
}

function reviewerPage(path: string): ReviewerPage | null {
  if (path === '/compare' || path === '/compare/') return 'compare'
  if (path === '/console' || path === '/console/') return 'console'
  return null
}

export function App() {
  // Kept in memory only: a reload signs out, and nothing about the session reaches storage.
  const [session, setSession] = useState<ChatSession | null>(null)
  const [language, setLanguage] = useState<Language>(browserLanguage)
  // Why the last session ended. The notice is worded at render time, so it follows the language.
  const [ended, setEnded] = useState<'expired' | 'signed_out' | null>(null)
  // The demo's shared access code, once the server asked for it: in memory only, so signing in
  // again after a session ends does not ask twice.
  const [accessCode, setAccessCode] = useState('')
  const reviewer = reviewerPage(window.location.pathname)

  useEffect(() => {
    if (reviewer !== null) {
      document.documentElement.lang = 'en'
      document.title = `${REVIEWER_TITLE[reviewer]} · ${PRODUCT_NAME}`
      return
    }
    document.documentElement.lang = COPY[language].locale
    document.title = `${PRODUCT_NAME} · ${COPY[language].product}`
    document
      .querySelector('meta[name="description"]')
      ?.setAttribute('content', COPY[language].pageDescription)
  }, [language, reviewer])

  return (
    <IconContext.Provider value={ICONS}>
      {reviewer === 'compare' ? (
        <Suspense fallback={<output lang="en">Loading the replay…</output>}>
          <ComparisonScreen />
        </Suspense>
      ) : reviewer === 'console' ? (
        <Suspense fallback={<output lang="en">Loading the console…</output>}>
          <ConsoleScreen />
        </Suspense>
      ) : session === null ? (
        <LoginScreen
          language={language}
          notice={ended === 'expired' ? COPY[language].sessionEnded : null}
          accessCode={accessCode}
          onAccessCodeChange={setAccessCode}
          onLanguageChange={setLanguage}
          onSignedIn={(signedIn) => {
            setEnded(null)
            setLanguage(signedIn.language)
            setSession(signedIn)
          }}
        />
      ) : (
        <ChatScreen
          session={session}
          onLanguageChange={setLanguage}
          onSignedOut={(reason) => {
            setEnded(reason)
            setSession(null)
          }}
        />
      )}
    </IconContext.Provider>
  )
}
