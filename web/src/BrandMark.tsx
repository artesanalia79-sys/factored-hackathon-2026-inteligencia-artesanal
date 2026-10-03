/** The product mark: also the agent's avatar in the conversation. Decorative. */
export function BrandMark({ size = 'md' }: { size?: 'sm' | 'md' }) {
  return (
    <span className={`mark mark--${size}`} aria-hidden="true">
      <svg viewBox="80 40 432 440" fill="none" focusable="false">
        <g stroke="#1ceae7" strokeWidth="44" strokeLinecap="round" strokeLinejoin="round">
          <path d="M132 226 L226 132 Q256 102 286 132 L350 196" />
          <path d="M162 286 L224 348 Q238 362 256 362 Q274 362 288 348 L386 250 Q418 218 450 250 Q482 282 450 314 L338 426 Q306 458 274 426 L162 314 Q130 282 162 250 Q194 218 226 250" />
        </g>
      </svg>
    </span>
  )
}
