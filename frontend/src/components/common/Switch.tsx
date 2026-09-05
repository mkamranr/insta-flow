/**
 * An on/off control.
 *
 * A `<button role="switch">` rather than a checkbox: this is a control that
 * acts, and the global `label { display: block; margin: 14px 0 6px }` would
 * fight a row layout. A `<label for>` cannot target a `<button>` either, so the
 * caller points at its visible text with `labelledBy`.
 *
 * No onKeyDown. A `<button>` already fires onClick for both Space and Enter,
 * which is what role="switch" is specified to do; a handler would double-fire.
 */
export function Switch({
  checked,
  onChange,
  labelledBy,
  disabled,
}: {
  checked: boolean
  onChange: (next: boolean) => void
  /** id of the element holding the visible label. */
  labelledBy: string
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-labelledby={labelledBy}
      disabled={disabled}
      className={checked ? 'switch on' : 'switch'}
      onClick={() => onChange(!checked)}
    >
      <span className="switch-knob" aria-hidden="true" />
    </button>
  )
}
