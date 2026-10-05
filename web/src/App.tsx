import { IconContext } from '@phosphor-icons/react'
import { lazy, Suspense, useEffect, useState } from 'react'
import type { Language } from './api/contracts.gen.ts'
import { ChatScreen } from './chat/ChatScreen.tsx'
import { browserLanguage, COPY } from './i18n.ts'
import { LoginScreen } from './login/LoginScreen.tsx'
import type { ChatSession } from './session.ts'

const ICONS = { size: 20, weight: 'regular' } as const
const ComparisonScreen = lazy(() => import('./comparison/ComparisonScreen.tsx'))
const ConsoleScreen = lazy(() => import('./console/ConsoleScreen.tsx'))

export function App() {
  // Kept in memory only: a reload signs out, and nothing about the session reaches storage.
  const [session, setSession] = useState<ChatSession | null>(null)
  const [language, setLanguage] = useState<Language>(browserLanguage)
  // Why the last session ended. The notice is worded at render time, so it follows the language.
  const [ended, setEnded] = useState<'expired' | 'signed_out' | null>(null)
  // The demo's shared access code, once the server asked for it: in memory only, so signing in
  // again after a session ends does not ask twice.
  const [accessCode, setAccessCode] = useState('')

  useEffect(() => {
    document.documentElement.lang = COPY[language].locale
    document.title = COPY[language].product
    document
      .querySelector('meta[name="description"]')
      ?.setAttribute('content', COPY[language].pageDescription)
  }, [language])

  return (
    <IconContext.Provider value={ICONS}>
      {['/compare', '/compare/'].includes(window.location.pathname) ? (
        <Suspense fallback={<output>Cargando comparación…</output>}>
          <ComparisonScreen />
        </Suspense>
      ) : ['/console', '/console/'].includes(window.location.pathname) ? (
        <Suspense fallback={<output>Cargando consola…</output>}>
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
