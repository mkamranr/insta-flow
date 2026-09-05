import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { apiDelete, apiGet, apiGetInstance, apiPost, apiPut } from '../api/client'
import { usePermissions } from '../hooks/usePermissions'
import type { ReadyResponse } from '../types/health'

function Row({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>
        {value}
        {hint && <div className="muted small">{hint}</div>}
      </dd>
    </>
  )
}

interface DisclosureSettings {
  enabled: boolean
  text: string
}

function DisclosureCard() {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ['disclosure'],
    queryFn: () => apiGet<DisclosureSettings>('/api/settings/disclosure'),
  })
  const [draft, setDraft] = useState<DisclosureSettings | null>(null)
  const [saved, setSaved] = useState(false)

  // The server is the source of truth until the operator types; seeding state
  // from `data` in an effect would fight the query on every refetch.
  const current = draft ?? data ?? { enabled: true, text: '' }
  const dirty = draft !== null && data !== undefined &&
    (draft.enabled !== data.enabled || draft.text !== data.text)

  const edit = (patch: Partial<DisclosureSettings>) => {
    setSaved(false)
    setDraft({ ...current, ...patch })
  }

  const save = useMutation({
    mutationFn: () => apiPut('/api/settings/disclosure', current),
    onSuccess: () => {
      setDraft(null)
      setSaved(true)
      queryClient.invalidateQueries({ queryKey: ['disclosure'] })
    },
  })

  return (
    <div className="card">
      <h2>Automated-experience disclosure</h2>
      <p className="muted small">
        Meta requires telling people in California and Germany that they are talking to
        automation. The obligation is yours, so the wording is too — but silence is the wrong
        default, so this starts switched on.
      </p>

      <label className="inline">
        <input
          type="checkbox"
          checked={current.enabled}
          onChange={(e) => edit({ enabled: e.target.checked })}
        />
        Add a disclosure line to automated replies
      </label>

      <label htmlFor="disclosure-text">Wording</label>
      <input
        id="disclosure-text"
        maxLength={200}
        value={current.text}
        disabled={!current.enabled}
        placeholder="Automated reply."
        onChange={(e) => edit({ text: e.target.value })}
      />
      <p className="muted small">
        {current.text.length}/200 · Appended after a blank line. Leaving it empty uses
        &ldquo;Automated reply.&rdquo;
      </p>

      {current.enabled && (
        <div className="disclosure-preview">
          <span className="muted small">Your message</span>
          <span className="disclosure-line">{current.text.trim() || 'Automated reply.'}</span>
        </div>
      )}

      <p className="muted small">
        Applied to story-reply and DM-keyword replies. Replies to comments do not carry it: a
        private reply is one message capped at 160 characters when it has buttons, so appending
        a line there could split a send Meta only allows once.
      </p>

      <div className="row">
        <button disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>

      {save.error && <p className="err small">{(save.error as Error).message}</p>}
      {saved && !save.error && <p className="ok small">Saved.</p>}
    </div>
  )
}

interface MetaAppState {
  configured: boolean
  source: 'workspace' | 'instance' | 'unknown'
  app_id: string
  secret_set: boolean
  verify_token: string
  /** What to register in your own Meta app. Always workspace-prefixed. */
  urls: {
    webhook_url: string
    redirect_uri: string
    privacy_url: string
    data_deletion_url: string
  }
  /** What is in force right now — differs only while on the instance-wide app. */
  active_urls: { webhook_url: string }
  workspace: { slug: string | null; is_own: boolean }
  using_instance_app: boolean
  has_own_row: boolean
  note?: string
}

/** Copy-to-clipboard for a value that has to be pasted into Meta's dashboard. */
function CopyField({ label, value, hint }: { label: string; value: string; hint?: string }) {
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

function MetaAppCard() {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ['meta-app'],
    queryFn: () => apiGet<MetaAppState>('/api/settings/meta-app'),
  })
  const [appId, setAppId] = useState<string | null>(null)
  const [appSecret, setAppSecret] = useState('')
  const [showToken, setShowToken] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['meta-app'] })
  const currentId = appId ?? data?.app_id ?? ''

  const save = useMutation({
    mutationFn: () =>
      apiPut<MetaAppState>('/api/settings/meta-app', {
        app_id: currentId,
        app_secret: appSecret,
      }),
    onSuccess: () => {
      setAppSecret('')
      setAppId(null)
      setNote('Saved. Paste the URLs below into your Meta app, then verify the webhook.')
      invalidate()
    },
  })
  const regenerate = useMutation({
    mutationFn: () => apiPost<MetaAppState>('/api/settings/meta-app/verify-token'),
    onSuccess: (result) => {
      setShowToken(true)
      setNote(result?.note ?? 'A new verify token was generated.')
      invalidate()
    },
  })
  const clear = useMutation({
    mutationFn: () => apiDelete('/api/settings/meta-app'),
    onSuccess: () => {
      setNote('Removed. This workspace falls back to the instance-wide Meta app.')
      invalidate()
    },
  })

  const busy = save.isPending || regenerate.isPending || clear.isPending
  const failure = save.error ?? regenerate.error ?? clear.error

  return (
    <div className="card">
      <h2>Instagram app</h2>
      <p className="muted small">
        Each account connects through its own Meta app. That is what keeps this out of App
        Review: automating <em>your own</em> Instagram account needs only Live mode, while
        reaching someone else&rsquo;s through your app is the case Meta requires Advanced
        Access, App Review and Business Verification for.
      </p>

      {data?.using_instance_app && (
        <div className="notice">
          <strong>This workspace is on the instance-wide app from <code>.env</code>.</strong> It
          is configured with <code>{data.active_urls.webhook_url}</code> and keeps working
          exactly as it does today until you save an App ID below.
          <br />
          <br />
          You can either <strong>reuse that same app</strong> — paste its App ID and Secret
          here, then add the prefixed URLs below to it in Meta — or create a new one. Reusing it
          keeps your connected account and its token; a new app means reconnecting Instagram and
          re-subscribing webhooks.
        </div>
      )}

      <label htmlFor="meta-app-id">App ID</label>
      <input
        id="meta-app-id"
        value={currentId}
        placeholder="From Instagram → API setup with Instagram business login"
        onChange={(e) => setAppId(e.target.value)}
      />

      <label htmlFor="meta-app-secret">App Secret</label>
      <input
        id="meta-app-secret"
        type="password"
        value={appSecret}
        placeholder={data?.secret_set ? 'Stored — type to replace' : 'Required'}
        onChange={(e) => setAppSecret(e.target.value)}
      />
      <p className="muted small">
        Encrypted before it is stored and never sent back to this page. Leave it blank to
        correct the App ID without retyping the secret.
      </p>

      <div className="row">
        <button disabled={!currentId || busy} onClick={() => save.mutate()}>
          {save.isPending ? 'Saving…' : 'Save app'}
        </button>
        {data?.has_own_row && (
          <button className="secondary" disabled={busy} onClick={() => clear.mutate()}>
            Remove
          </button>
        )}
      </div>

      <h3>Webhook verify token</h3>
      <p className="muted small">
        Generated here, pasted into Meta&rsquo;s webhook configuration. Regenerating invalidates
        the old one, so the subscription has to be verified again.
      </p>
      <div className="row">
        <code className="copy-field-value">
          {showToken ? data?.verify_token || '—' : '••••••••••••••••••••'}
        </code>
        <button className="link" onClick={() => setShowToken((v) => !v)}>
          {showToken ? 'Hide' : 'Reveal'}
        </button>
        <button
          className="link"
          onClick={() => data?.verify_token && navigator.clipboard?.writeText(data.verify_token)}
        >
          Copy
        </button>
        <button className="secondary" disabled={busy} onClick={() => regenerate.mutate()}>
          Regenerate
        </button>
      </div>

      <h3>Paste these into your Meta app</h3>
      <p className="muted small">
        These are <strong>your workspace&rsquo;s</strong> URLs — the ones to register in the app
        you configure above, whether or not you have saved it yet.
      </p>
      <p className="muted small">
        Only the &ldquo;Valid OAuth Redirect URIs&rdquo; field under{' '}
        <strong>Instagram → API setup with Instagram business login</strong> is read. The
        Facebook Login page has a field with the same name that will validate your URL and then
        be ignored.
      </p>
      {data && (
        <>
          <CopyField label="Webhook callback URL" value={data.urls.webhook_url} />
          <CopyField label="Valid OAuth Redirect URI" value={data.urls.redirect_uri} />
          <CopyField label="Privacy Policy URL" value={data.urls.privacy_url} />
          <CopyField
            label="Data Deletion Instructions URL"
            value={data.urls.data_deletion_url}
            hint="App Settings → Basic. Live mode requires this, an App Icon and a Category."
          />
        </>
      )}

      {failure && <p className="err small">{(failure as Error).message}</p>}
      {note && !failure && <p className="ok small">{note}</p>}
    </div>
  )
}

interface InstanceOrigin {
  origin: string
  source: 'settings' | 'env'
  env_value: string
}

function InstanceOriginCard() {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const { data } = useQuery({
    queryKey: ['instance-origin'],
    queryFn: () => apiGet<InstanceOrigin>('/api/settings/instance'),
  })
  const [draft, setDraft] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const current = draft ?? data?.origin ?? ''
  const save = useMutation({
    mutationFn: () =>
      apiPut<{ warning?: string; note?: string }>('/api/settings/instance', { origin: current }),
    onSuccess: (result) => {
      setDraft(null)
      setNote([result?.warning, result?.note].filter(Boolean).join(' '))
      queryClient.invalidateQueries({ queryKey: ['instance-origin'] })
      queryClient.invalidateQueries({ queryKey: ['meta-app'] })
    },
  })

  return (
    <div className="card">
      <h2>Public address</h2>
      <p className="muted small">
        The origin every workspace URL is built from. One value for the whole instance —{' '}
        <code>make tunnel-status</code> prints the current Tailscale hostname.
      </p>

      <label htmlFor="instance-origin">Origin</label>
      <input
        id="instance-origin"
        value={current}
        disabled={!can('instance.manage')}
        placeholder="https://your-machine.your-tailnet.ts.net"
        onChange={(e) => setDraft(e.target.value)}
      />
      <p className="muted small">
        Host only, with no path. Currently taken from{' '}
        {data?.source === 'settings' ? 'this setting' : <code>PUBLIC_BASE_URL</code>}.
      </p>

      {can('instance.manage') && (
        <div className="row">
          <button disabled={!current || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
      )}

      <p className="muted small">
        Changing this does not update anything at Meta. Every Meta app configured with the old
        address keeps calling it until its URLs are re-pasted by hand.
      </p>

      {save.error && <p className="err small">{(save.error as Error).message}</p>}
      {note && !save.error && <p className="ok small">{note}</p>}
    </div>
  )
}

interface GeoIpStatus {
  configured: boolean
  account_id?: string | null
  database: { present: boolean; size_bytes?: number; updated_at?: number }
}

function GeoIpCard() {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const { data } = useQuery({ queryKey: ['geoip'], queryFn: () => apiGet<GeoIpStatus>('/api/settings/geoip') })
  const [accountId, setAccountId] = useState('')
  const [licenseKey, setLicenseKey] = useState('')
  const [note, setNote] = useState<string | null>(null)

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['geoip'] })

  const save = useMutation({
    mutationFn: () =>
      apiPut<{ warning?: string }>('/api/settings/geoip', {
        account_id: accountId,
        license_key: licenseKey,
      }),
    onSuccess: (result) => {
      setLicenseKey('')
      setNote(result?.warning ?? 'Saved. The database is installed.')
      invalidate()
    },
  })
  const refresh = useMutation({
    mutationFn: () => apiPost('/api/settings/geoip/refresh'),
    onSuccess: () => {
      setNote('Database updated.')
      invalidate()
    },
  })
  const clear = useMutation({
    mutationFn: () => apiDelete('/api/settings/geoip'),
    onSuccess: () => {
      setNote('Credentials removed.')
      invalidate()
    },
  })

  const busy = save.isPending || refresh.isPending || clear.isPending
  const failure = save.error ?? refresh.error ?? clear.error

  return (
    <div className="card">
      <h2>Click geography</h2>
      <p className="muted small">
        Resolves the country and city of each link click using MaxMind&rsquo;s GeoLite2
        database. The database is downloaded here and every lookup runs locally, so no
        visitor&rsquo;s IP address is ever sent to MaxMind — or stored. Only the country and
        city derived from it are kept.
      </p>

      <dl className="kv">
        <Row
          label="Status"
          value={data?.configured ? <span className="ok">Configured</span> : <span className="muted">Not set up</span>}
        />
        <Row
          label="Database"
          value={
            data?.database?.present ? (
              <span className="ok">
                Installed ({Math.round((data.database.size_bytes ?? 0) / (1024 * 1024))} MB)
              </span>
            ) : (
              <span className="muted">Not downloaded</span>
            )
          }
          hint={
            data?.database?.updated_at
              ? `Updated ${new Date(data.database.updated_at * 1000).toLocaleDateString()}`
              : undefined
          }
        />
      </dl>

      {can('instance.manage') && (
        <>
          <label htmlFor="mm-account">MaxMind account ID</label>
          <input
            id="mm-account"
            value={accountId}
            placeholder={data?.account_id ?? '123456'}
            onChange={(e) => setAccountId(e.target.value)}
          />

          <label htmlFor="mm-key">License key</label>
          <input
            id="mm-key"
            type="password"
            value={licenseKey}
            placeholder={data?.configured ? '•••••••• (stored)' : 'Paste your license key'}
            onChange={(e) => setLicenseKey(e.target.value)}
          />
          <p className="muted small">
            Encrypted before it is stored, and never returned by the API. Verified against
            MaxMind when you save, so a typo is caught here rather than showing up later as an
            empty report.
          </p>

          <div className="row">
            <button
              disabled={busy || !accountId.trim() || !licenseKey.trim()}
              onClick={() => {
                setNote(null)
                save.mutate()
              }}
            >
              {save.isPending ? 'Verifying…' : 'Save and download'}
            </button>
            {data?.configured && (
              <>
                <button className="secondary" disabled={busy} onClick={() => refresh.mutate()}>
                  {refresh.isPending ? 'Updating…' : 'Update database'}
                </button>
                <button className="danger" disabled={busy} onClick={() => clear.mutate()}>
                  Remove
                </button>
              </>
            )}
          </div>
        </>
      )}

      {failure && <p className="err small">{(failure as Error).message}</p>}
      {note && !failure && <p className="ok small">{note}</p>}
    </div>
  )
}

export function Settings() {
  const { data } = useQuery({
    queryKey: ['ready'],
    queryFn: () => apiGetInstance<ReadyResponse>('/ready'),
    refetchInterval: 30_000,
  })
  const { can } = usePermissions()

  return (
    <>
      <h1>Settings</h1>
      <p className="muted">
        Infrastructure settings live in <code>.env</code> on the server. The Meta app each
        account connects through is configured here instead, so a second person can bring
        their own without shell access.
      </p>

      <div className="card">
        <h2>Sending</h2>
        <dl className="kv">
          <Row
            label="Mode"
            value={
              data?.dry_run ? (
                <span className="warn-text">Dry run — nothing is sent</span>
              ) : (
                <span className="err">Live — real DMs are being sent</span>
              )
            }
            hint={
              data?.dry_run
                ? 'The full pipeline runs and records executions, but no Instagram message leaves the server.'
                : 'Matching comments trigger real Instagram private replies.'
            }
          />
        </dl>
        <p className="muted small">
          To change this, edit <code>DRY_RUN</code> in <code>.env</code> and run{' '}
          <code>make reload</code>. A plain <code>docker compose restart</code> will not pick it
          up — Compose reads <code>env_file</code> when a container is created, not restarted.
        </p>
      </div>

      {can('instagram.manage') && <MetaAppCard />}
      {can('instance.view') && <InstanceOriginCard />}

      <DisclosureCard />

      <div className="card">
        <h2>Services</h2>
        <dl className="kv">
          <Row
            label="MongoDB"
            value={<span className={data?.checks.mongodb ? 'ok' : 'err'}>
              {data?.checks.mongodb ? 'connected' : 'unavailable'}
            </span>}
          />
          <Row
            label="Redis"
            value={<span className={data?.checks.redis ? 'ok' : 'err'}>
              {data?.checks.redis ? 'connected' : 'unavailable'}
            </span>}
          />
          <Row
            label="Core configuration"
            value={
              data?.checks.config ? (
                <span className="ok">complete</span>
              ) : (
                <span className="err">missing: {data?.missing_config?.join(', ')}</span>
              )
            }
            hint="The encryption key and session secret. Meta credentials are per workspace now, and are reported on this page rather than here."
          />
        </dl>
      </div>

      {can('instance.view') && <GeoIpCard />}

      <div className="card">
        <h2>Useful commands</h2>
        <pre className="code-block">{`make up               # start everything
make reload           # apply .env changes
make funnel           # expose publicly via Tailscale
make tunnel-status    # print the URLs for the Meta dashboard
make bootstrap-admin  # recover access if you are locked out
make test             # run the backend suite`}</pre>
      </div>
    </>
  )
}
