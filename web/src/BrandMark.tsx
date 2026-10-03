import { ShieldCheck } from '@phosphor-icons/react'

/** The product mark: also the agent's avatar in the conversation. Decorative. */
export function BrandMark({ size = 'md' }: { size?: 'sm' | 'md' }) {
  return (
    <span className={`mark mark--${size}`} aria-hidden="true">
      <ShieldCheck weight="fill" size={size === 'sm' ? 16 : 20} />
    </span>
  )
}
