import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { getMetaApp, saveMetaApp } from '../../api/settings'

/** The App ID and secret pair, on its own.
 *
 * Extracted rather than reusing the whole `MetaAppCard`, because the guided
 * setup distributes that card's parts across four different steps: the
 * credentials belong to step 5, the copy-out URLs to steps 6 and 8, and the
 * verify token to step 9. Both surfaces write through the same mutation and
 * invalidate the same `['meta-app']` key, so neither can drift from the other.
 *
 * The input ids are kept from the original: the two surfaces are separate
 * routes and never render at the same time.
 */
export function MetaAppCredentials({ compact = false }: { compact?: boolean }) {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['meta-app'], queryFn: getMetaApp, retry: false })
  const [appId, setAppId] = useState<string | null>(null)
  const [appSecret, setAppSecret] = useState('')
  const [note, setNote] = useState<string | null>(null)

  const currentId = appId ?? data?.app_id ?? ''

  const save = useMutation({
    mutationFn: () => saveMetaApp(currentId, appSecret),
    onSuccess: () => {
      setAppSecret('')
      setAppId(null)
      setNote(
        compact
          ? 'Saved.'
          : 'Saved. Paste the URLs below into your Meta app, then verify the webhook.',
      )
      queryClient.invalidateQueries({ queryKey: ['meta-app'] })
      queryClient.invalidateQueries({ queryKey: ['onboarding', 'progress'] })
    },
  })

  return (
    <>
      <label htmlFor="meta-app-id">App ID</label>
      <input
        id="meta-app-id"
        value={currentId}
        placeholder="From Instagram → API setup with Instagram business login"
        onChange={(e) => {
          setNote(null)
          setAppId(e.target.value)
        }}
      />

      <label htmlFor="meta-app-secret">App Secret</label>
      <input
        id="meta-app-secret"
        type="password"
        value={appSecret}
        placeholder={data?.secret_set ? 'Stored — type to replace' : 'Required'}
        onChange={(e) => {
          setNote(null)
          setAppSecret(e.target.value)
        }}
      />
      <p className="muted small">
        Encrypted before it is stored and never sent back to this page. Leave it blank to
        correct the App ID without retyping the secret.
      </p>

      <div className="row">
        <button disabled={!currentId || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? 'Saving…' : 'Save app'}
        </button>
      </div>

      {save.error && <p className="err small" role="alert">{(save.error as Error).message}</p>}
      {note && !save.error && <p className="ok small">{note}</p>}
    </>
  )
}
