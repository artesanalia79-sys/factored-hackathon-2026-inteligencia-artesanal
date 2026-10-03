import { CheckCircle, Headset } from '@phosphor-icons/react'
import { BrandMark } from '../BrandMark.tsx'
import { COPY, type Copy } from '../i18n.ts'
import type { Item } from './state.ts'

/** One entry of the conversation log. Agent text is shown as the backend wrote it. */
export function Message({ item, copy }: { item: Item; copy: Copy }) {
  if (item.kind === 'ended') {
    return (
      <div className="log__ended">
        <span>{copy.ended}</span>
      </div>
    )
  }
  if (item.kind === 'customer') {
    return (
      <div className="msg msg--customer">
        <p className="bubble">
          <span className="sr-only">{copy.you}: </span>
          {item.text}
        </p>
      </div>
    )
  }
  const own = COPY[item.language]
  return (
    <div className="msg msg--agent" lang={own.locale}>
      <BrandMark size="sm" />
      <p className="bubble">
        <span className="sr-only">{own.agent}: </span>
        {item.text}
      </p>
      {/* Only what the backend verified by read-back reaches `claimed`. */}
      {item.claimed.map((action) => (
        <p
          key={action}
          className={`status ${action === 'create_handoff' ? 'status--handoff' : 'status--verified'}`}
        >
          {action === 'create_handoff' ? (
            <Headset weight="fill" aria-hidden="true" />
          ) : (
            <CheckCircle weight="fill" aria-hidden="true" />
          )}
          {own.verified[action]}
        </p>
      ))}
    </div>
  )
}

export function Typing({ copy }: { copy: Copy }) {
  return (
    <div className="msg msg--agent msg--typing">
      <BrandMark size="sm" />
      <p className="bubble bubble--typing">
        <span className="dots" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
        <span className="sr-only">{copy.typing}</span>
      </p>
    </div>
  )
}
