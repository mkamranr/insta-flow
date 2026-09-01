import { useState } from 'react'

export function KeywordInput({
  keywords,
  onChange,
}: {
  keywords: string[]
  onChange: (next: string[]) => void
}) {
  const [draft, setDraft] = useState('')

  const add = () => {
    const value = draft.trim()
    // Matching is case-insensitive by default, so link/LINK/Link are one keyword.
    if (value && !keywords.some((k) => k.toLowerCase() === value.toLowerCase())) {
      onChange([...keywords, value])
    }
    setDraft('')
  }

  return (
    <>
      {keywords.length > 0 && (
        <div className="chips">
          {keywords.map((keyword) => (
            <span key={keyword} className="chip">
              {keyword}
              <button
                type="button"
                className="chip-x"
                aria-label={`Remove ${keyword}`}
                onClick={() => onChange(keywords.filter((k) => k !== keyword))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <input
        value={draft}
        placeholder="Type a keyword and press Enter"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault()
            add()
          }
        }}
        onBlur={add}
      />
      <p className="muted small">
        Matching ignores case, so <code>link</code> already catches LINK and Link.
      </p>
    </>
  )
}
