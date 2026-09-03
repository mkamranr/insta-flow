import { useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { uploadAttachment, type Attachment } from '../../api/rules'

/**
 * Upload a file for the payoff DM.
 *
 * The file is stored on this server and served publicly, because Meta fetches
 * attachments by URL rather than accepting bytes (docs/meta-api.md §4c).
 * Replacing or clearing one deletes the old file server-side, so nothing is
 * orphaned on disk.
 */

const ACCEPT = '.png,.jpg,.jpeg,.mp4,.ogg,.avi,.mov,.webm,.aac,.m4a,.wav,.pdf'

function prettyBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

export function AttachmentField({
  value,
  onChange,
}: {
  value: Attachment | null
  onChange: (next: Attachment | null) => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function pick(file: File | undefined) {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      onChange(await uploadAttachment(file))
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : 'Upload failed.')
    } finally {
      setBusy(false)
      // Clear the input so re-picking the same file still fires a change.
      if (input.current) input.current.value = ''
    }
  }

  return (
    <div className="attach-field">
      <input
        ref={input}
        type="file"
        accept={ACCEPT}
        hidden
        onChange={(e) => pick(e.target.files?.[0])}
      />

      {value ? (
        <div className="attach-row">
          <span className="attach-name">{value.filename}</span>
          <span className="muted small">
            {prettyBytes(value.size_bytes)} · {value.type}
          </span>
          <button type="button" className="link danger-text" onClick={() => onChange(null)}>
            Remove
          </button>
        </div>
      ) : (
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => input.current?.click()}
        >
          {busy ? 'Uploading…' : 'Choose a file'}
        </button>
      )}

      {error && <p className="err small">{error}</p>}
      <p className="muted small">
        Images to 8MB (png, jpeg). Video, audio and PDF to 25MB. Meta fetches the file from this
        server when the DM is sent, so it has to be reachable at that moment.
      </p>
    </div>
  )
}
