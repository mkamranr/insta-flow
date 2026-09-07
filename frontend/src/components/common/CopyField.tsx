import { useState } from 'react'

/** Copy-to-clipboard for a value that has to be pasted into Meta's dashboard.
 *
 * Extracted from `pages/Settings.tsx` when the guided setup arrived, which
 * needs the same field in five places. Unchanged otherwise -- same classes,
 * same 1500ms reset -- so the Settings page renders identically.
 */
export function CopyField({
  label,
  value,
  hint,
}: {
  label: string
  value: string
  hint?: string
}) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="copy-field">
      <div className="copy-field-head">
        <span className="small muted">{label}</span>
        <button
          className="link"
          onClick={() => {
            navigator.clipboard?.writeText(value)
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
          }}
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <code className="copy-field-value">{value}</code>
      {hint && <div className="small faint">{hint}</div>}
    </div>
  )
}
