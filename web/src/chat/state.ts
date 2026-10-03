import type {
  ActionType,
  ChatTurnResponse,
  ConfirmationView,
  Language,
} from '../api/contracts.gen.ts'

export type Item =
  | { id: number; kind: 'customer'; text: string }
  | { id: number; kind: 'agent'; text: string; language: Language; claimed: ActionType[] }
  | { id: number; kind: 'ended' }

export interface ChatState {
  items: Item[]
  nextId: number
  /** A turn is on its way; nothing else may be sent until it answers. */
  pending: boolean
  /** The write the last reply asks to confirm. Cleared as soon as anything is sent. */
  confirmation: ConfirmationView | null
  /** The language the agent last replied in; the chrome follows it. */
  language: Language
  /**
   * The last send got no reply, so it may or may not have arrived. `answer` when it answered a
   * confirmation question: the server may already be asking the next one.
   */
  failed: 'message' | 'answer' | null
  expired: boolean
}

export type ChatEvent =
  | { type: 'sent'; text: string }
  | { type: 'replied'; reply: ChatTurnResponse }
  | { type: 'failed'; answer: boolean }
  | { type: 'expired' }

export function initialChat(language: Language): ChatState {
  return {
    items: [],
    nextId: 1,
    pending: false,
    confirmation: null,
    language,
    failed: null,
    expired: false,
  }
}

export function chatReducer(state: ChatState, event: ChatEvent): ChatState {
  switch (event.type) {
    case 'sent':
      return {
        ...state,
        items: [...state.items, { id: state.nextId, kind: 'customer', text: event.text }],
        nextId: state.nextId + 1,
        pending: true,
        confirmation: null,
        failed: null,
      }
    case 'replied': {
      const { reply } = event
      const items: Item[] = [
        ...state.items,
        {
          id: state.nextId,
          kind: 'agent',
          text: reply.reply_text,
          language: reply.language,
          claimed: reply.claimed_actions,
        },
      ]
      if (reply.ended) items.push({ id: state.nextId + 1, kind: 'ended' })
      return {
        ...state,
        items,
        nextId: state.nextId + 2,
        pending: false,
        // An ended conversation asks nothing: the next message starts a new one, where a "yes"
        // would answer no question.
        confirmation: reply.ended ? null : (reply.confirmation ?? null),
        language: reply.language,
      }
    }
    case 'failed':
      return { ...state, pending: false, failed: event.answer ? 'answer' : 'message' }
    case 'expired':
      return { ...state, pending: false, confirmation: null, expired: true }
  }
}
