import { PaperPlaneRight } from '@phosphor-icons/react'
import { type KeyboardEvent, type RefObject, useId, useLayoutEffect } from 'react'
import { LIMITS } from '../api/contracts.gen.ts'
import type { Copy } from '../i18n.ts'

const MAX_LENGTH = LIMITS.ChatTurnRequest.text.maxLength
const MAX_HEIGHT_PX = 168

interface Props {
  copy: Copy
  draft: string
  canSend: boolean
  inputRef: RefObject<HTMLTextAreaElement | null>
  onDraftChange: (text: string) => void
  onSend: (text: string) => void
}

export function Composer({ copy, draft, canSend, inputRef, onDraftChange, onSend }: Props) {
  const inputId = useId()
  const hintId = useId()
  const ready = canSend && draft.trim() !== ''

  // Grow with the text up to a few lines, then scroll inside.
  useLayoutEffect(() => {
    const field = inputRef.current
    if (field === null) return
    field.style.height = 'auto'
    field.style.height = `${Math.min(field.scrollHeight, MAX_HEIGHT_PX)}px`
  }, [draft, inputRef])

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault()
    if (ready) onSend(draft)
  }

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault()
        if (ready) onSend(draft)
      }}
    >
      <label htmlFor={inputId} className="sr-only">
        {copy.composerLabel}
      </label>
      <div className="composer__box">
        <textarea
          id={inputId}
          ref={inputRef}
          className="composer__input"
          rows={1}
          maxLength={MAX_LENGTH}
          value={draft}
          placeholder={copy.composerLabel}
          aria-describedby={hintId}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="submit" className="composer__send" disabled={!ready} aria-label={copy.send}>
          <PaperPlaneRight weight="fill" aria-hidden="true" />
        </button>
      </div>
      <p id={hintId} className="composer__hint">
        {copy.composerHint}
      </p>
    </form>
  )
}
