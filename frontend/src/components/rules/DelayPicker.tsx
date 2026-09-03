import { useEffect, useRef, useState } from 'react'

/**
 * MM:SS picker for the reply delay.
 *
 * Two scrolling columns rather than number inputs: the value is a duration, and
 * a duration reads as a clock face. Capped at 59:59, which can never push a send
 * outside Meta's seven-day private-reply window.
 */

const MINUTES = Array.from({ length: 60 }, (_, index) => index)
const SECONDS = Array.from({ length: 60 }, (_, index) => index)

const pad = (value: number) => String(value).padStart(2, '0')

function Column({
  label,
  values,
  selected,
  onPick,
}: {
  label: string
  values: number[]
  selected: number
  onPick: (value: number) => void
}) {
  const list = useRef<HTMLDivElement>(null)

  // Open on the current value rather than at zero, so 00:30 shows 30 in view.
  useEffect(() => {
    const active = list.current?.querySelector<HTMLElement>('[data-active="true"]')
    active?.scrollIntoView({ block: 'center' })
  }, [])

  return (
    <div className="delay-col">
      <div className="delay-col-head">{label}</div>
      <div className="delay-col-list" ref={list} role="listbox" aria-label={label}>
        {values.map((value) => (
          <button
            key={value}
            type="button"
            role="option"
            aria-selected={value === selected}
            data-active={value === selected}
            className={value === selected ? 'delay-opt active' : 'delay-opt'}
            onClick={() => onPick(value)}
          >
            {pad(value)}
          </button>
        ))}
      </div>
    </div>
  )
}

export function DelayPicker({
  seconds,
  onChange,
}: {
  seconds: number
  onChange: (next: number) => void
}) {
  const [open, setOpen] = useState(false)
  const wrapper = useRef<HTMLDivElement>(null)

  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60

  useEffect(() => {
    if (!open) return
    const away = (event: MouseEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  return (
    <div className="delay-field" ref={wrapper}>
      <div className="delay-label">
        <span>Delay message</span>
        <span
          className="delay-hint"
          title="Wait this long after the comment before replying, so it does not read as instant automation."
          aria-label="Wait this long after the comment before replying."
        >
          i
        </span>
      </div>

      <div className="delay-control">
        <button
          type="button"
          className={open ? 'delay-trigger open' : 'delay-trigger'}
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 7v5l3 2" />
          </svg>
          <span className="delay-value">
            {pad(minutes)}:{pad(remainder)}
          </span>
        </button>

        {seconds > 0 && (
          <button
            type="button"
            className="delay-clear"
            onClick={() => onChange(0)}
            aria-label="Reply immediately"
            title="Reply immediately"
          >
            ×
          </button>
        )}

        {open && (
          <div className="delay-pop">
            <div className="delay-cols">
              <Column
                label="MIN"
                values={MINUTES}
                selected={minutes}
                onPick={(value) => onChange(value * 60 + remainder)}
              />
              <Column
                label="SEC"
                values={SECONDS}
                selected={remainder}
                onPick={(value) => onChange(minutes * 60 + value)}
              />
            </div>
            <div className="delay-actions">
              <button type="button" onClick={() => setOpen(false)}>
                OK
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
