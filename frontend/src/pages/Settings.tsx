import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { apiDelete, apiGet, apiGetInstance, apiPost, apiPut } from '../api/client'
import { Link } from 'react-router-dom'
import { getMe } from '../api/auth'
import { clearMetaApp, getMetaApp } from '../api/settings'
import { CopyField } from '../components/common/CopyField'
import { MetaAppCredentials } from '../components/settings/MetaAppCredentials'
import { VerifyTokenField } from '../components/settings/VerifyTokenField'
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
  const { can } = usePermissions()
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
      <p className="muted small">
        <strong>One line for the whole server.</strong> Every workspace&rsquo;s automated
        replies use it, so it is an owner-only setting rather than a per-account one.
      </p>

      <label className="inline">
        <input
          type="checkbox"
          checked={current.enabled}
          disabled={!can('instance.manage')}
          onChange={(e) => edit({ enabled: e.target.checked })}
        />
        Add a disclosure line to automated replies
      </label>

      <label htmlFor="disclosure-text">Wording</label>
      <input
        id="disclosure-text"
        maxLength={200}
        value={current.text}
        disabled={!current.enabled || !can('instance.manage')}
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

function MetaAppCard() {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['meta-app'], queryFn: getMetaApp })
  const [note, setNote] = useState<string | null>(null)

  const clear = useMutation({
    mutationFn: clearMetaApp,
    onSuccess: () => {
      setNote('Removed. This workspace falls back to the instance-wide Meta app.')
      queryClient.invalidateQueries({ queryKey: ['meta-app'] })
    },
  })

  return (
    <div className="card">
      <h2>Instagram app</h2>
      <p className="muted small">
        Each account connects through its own Meta app. That is what keeps this out of App
        Review: automating <em>your own</em> Instagram account needs only Live mode, while
        reaching someone else&rsquo;s through your app is the case Meta requires Advanced
        Access, App Review and Business Verification for.
      </p>
      <p className="muted small">
        New to this? The <Link to="/get-started">guided setup</Link> walks through creating
        the app in Meta&rsquo;s dashboard step by step.
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

      <MetaAppCredentials />

      {data?.has_own_row && (
        <div className="row">
          <button className="secondary" disabled={clear.isPending} onClick={() => clear.mutate()}>
            Remove
          </button>
        </div>
      )}

      <h3>Webhook verify token</h3>
      <VerifyTokenField />

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
          <CopyField
            label="Deauthorize callback URL"
            value={data.urls.deauthorize_url}
            hint="Same panel as the redirect URI. Easy to leave blank — Meta does not insist."
          />
          <CopyField label="Privacy Policy URL" value={data.urls.privacy_url} />
          <CopyField
            label="Data Deletion Instructions URL"
            value={data.urls.data_deletion_url}
            hint="App Settings → Basic. Live mode requires this, an App Icon and a Category."
          />
        </>
      )}

      {clear.error && <p className="err small">{(clear.error as Error).message}</p>}
      {note && !clear.error && <p className="ok small">{note}</p>}
    </div>
  )
}


interface InstanceOrigin {
  origin: string
  source: 'settings' | 'env'
  env_value: string
  trackable: boolean
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

      {/* Shown whenever the origin is not HTTPS, not just at the moment of
          saving. Both features fail silently, so the only symptom is a
          click-through rate of zero -- and this is the page somebody checks
          when they are trying to explain that zero. */}
      {data && !data.trackable && (
        <p className="err small">
          This origin is not <code>https://</code>, so link clicks are not tracked and file
          attachments cannot be fetched by Instagram. Both fail silently — the click-through
          rate simply reads 0%.
        </p>
      )}

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

interface EmailLogRow {
  message_id: string
  to_address: string
  subject: string
  status: string
  error: string | null
  smtp_code: number | null
  created_at: string | null
}

interface EmailSettings {
  configured: boolean
  host: string
  port: number
  security: 'starttls' | 'ssl' | 'none'
  username: string
  from_address: string
  from_name: string
  password_set: boolean
  auth_refused_at: string | null
  auth_error: string
  dry_run: boolean
  recent: EmailLogRow[]
}

/** The six fields that round-trip. The password is deliberately not among them. */
type EmailDraft = Pick<
  EmailSettings,
  'host' | 'port' | 'security' | 'username' | 'from_address' | 'from_name'
>

const EMAIL_DEFAULTS: EmailDraft = {
  host: '',
  port: 587,
  security: 'starttls',
  username: '',
  from_address: '',
  from_name: '',
}

function statusClass(status: string): string {
  if (status === 'sent') return 'ok'
  if (status === 'failed') return 'err'
  return 'muted'
}

function EmailCard() {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: getMe, retry: false })
  const [watching, setWatching] = useState(false)
  const { data } = useQuery({
    queryKey: ['email'],
    queryFn: () => apiGet<EmailSettings>('/api/settings/email'),
    // Bounded, and only after a test send: the send is asynchronous, so the
    // immediate response can only say "queued" and the result arrives seconds
    // later. Polling forever would be a request every few seconds for a page
    // nobody is watching.
    refetchInterval: watching ? 2_000 : false,
  })

  // `draft ?? data` for everything that round-trips, plus one separate piece of
  // state for the password. GeoIpCard uses independent fields throughout, which
  // is right for a write-only secret -- a draft-shaped form would need `data`
  // to carry the password, which it must not -- but it only gets away with that
  // because it has two fields. With six, fixing the sender name would mean
  // retyping the host. So: a draft for the six, and one field that is
  // structurally incapable of holding a server value. No effect seeds either.
  const [draft, setDraft] = useState<EmailDraft | null>(null)
  const [password, setPassword] = useState('')
  const [note, setNote] = useState<string | null>(null)

  const current: EmailDraft = draft ?? (data ? {
    host: data.host,
    port: data.port,
    security: data.security,
    username: data.username,
    from_address: data.from_address,
    from_name: data.from_name,
  } : EMAIL_DEFAULTS)
  const dirty = draft !== null || password !== ''

  const edit = (patch: Partial<EmailDraft>) => {
    setNote(null)
    setDraft({ ...current, ...patch })
  }
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['email'] })

  const save = useMutation({
    mutationFn: () => apiPut('/api/settings/email', { ...current, password }),
    onSuccess: () => {
      setDraft(null)
      setPassword('')
      setNote('Saved.')
      invalidate()
    },
  })
  const [testTo, setTestTo] = useState('')
  const sendTest = useMutation({
    mutationFn: () => apiPost('/api/settings/email/test', { to: testTo || me?.email }),
    onSuccess: () => {
      setNote('Queued. The result appears below within a few seconds.')
      setWatching(true)
      window.setTimeout(() => setWatching(false), 20_000)
      invalidate()
    },
  })
  const clear = useMutation({
    mutationFn: () => apiDelete('/api/settings/email'),
    onSuccess: () => {
      setDraft(null)
      setPassword('')
      setNote('Mail settings removed.')
      invalidate()
    },
  })

  const busy = save.isPending || sendTest.isPending || clear.isPending
  const failure = save.error ?? sendTest.error ?? clear.error
  const editable = can('instance.manage')

  return (
    <div className="card">
      <h2>Email</h2>
      <p className="muted small">
        Used to send invitations. One account sends for the whole server. With a Gmail
        address this wants an <strong>App Password</strong> — a 16-character code from
        Google, not your normal password — which needs two-step verification switched on.
        Gmail also rewrites a sender address it does not own, so keep{' '}
        <em>Send from</em> the same as the username.
      </p>

      <dl className="kv">
        <Row
          label="Status"
          value={
            data?.configured
              ? <span className="ok">Configured</span>
              : <span className="muted">Not set up</span>
          }
        />
        <Row
          label="Sending"
          value={<span className="ok">Live</span>}
          hint={
            data?.dry_run
              ? 'Email is not affected by DRY_RUN. Messages really are sent, even though Meta replies are only recorded.'
              : undefined
          }
        />
      </dl>

      {data?.auth_refused_at && (
        <p className="err small">
          The mail server refused these credentials on{' '}
          {new Date(data.auth_refused_at).toLocaleString()}. The App Password is probably no
          longer valid — enter a new one.
        </p>
      )}

      {editable && (
        <>
          <label htmlFor="smtp-host">SMTP server</label>
          <input
            id="smtp-host"
            value={current.host}
            placeholder="smtp.gmail.com"
            onChange={(e) => edit({ host: e.target.value })}
          />

          <label htmlFor="smtp-port">Port</label>
          <input
            id="smtp-port"
            type="number"
            value={current.port}
            onChange={(e) => edit({ port: Number(e.target.value) })}
          />

          <label htmlFor="smtp-security">Connection security</label>
          <select
            id="smtp-security"
            value={current.security}
            onChange={(e) => edit({ security: e.target.value as EmailDraft['security'] })}
          >
            <option value="starttls">STARTTLS (port 587)</option>
            <option value="ssl">SSL/TLS (port 465)</option>
            <option value="none">None</option>
          </select>

          <label htmlFor="smtp-username">Username</label>
          <input
            id="smtp-username"
            value={current.username}
            placeholder="you@gmail.com"
            onChange={(e) => edit({ username: e.target.value })}
          />

          <label htmlFor="smtp-password">Password</label>
          <input
            id="smtp-password"
            type="password"
            value={password}
            placeholder={data?.password_set ? 'Stored — type to replace' : 'App Password'}
            onChange={(e) => {
              setNote(null)
              setPassword(e.target.value)
            }}
          />
          <p className="muted small">
            Encrypted before it is stored and never returned by the API. Leave it blank to
            keep the stored one. It is not checked when you save — use{' '}
            <em>Send test email</em> for that, which exercises the same path a real
            invitation takes.
          </p>

          <label htmlFor="smtp-from">Send from</label>
          <input
            id="smtp-from"
            type="email"
            value={current.from_address}
            placeholder="you@gmail.com"
            onChange={(e) => edit({ from_address: e.target.value })}
          />

          <label htmlFor="smtp-from-name">Sender name</label>
          <input
            id="smtp-from-name"
            value={current.from_name}
            placeholder="Social Flow"
            onChange={(e) => edit({ from_name: e.target.value })}
          />

          <div className="row">
            <button
              disabled={!dirty || busy || !current.host.trim() || !current.from_address.trim()}
              onClick={() => {
                setNote(null)
                save.mutate()
              }}
            >
              {save.isPending ? 'Saving…' : 'Save'}
            </button>
            {data?.configured && (
              <button className="danger" disabled={busy} onClick={() => clear.mutate()}>
                Remove
              </button>
            )}
          </div>

          {data?.configured && (
            <>
              <label htmlFor="smtp-test">Send a test email to</label>
              <div className="row">
                <input
                  id="smtp-test"
                  type="email"
                  value={testTo}
                  placeholder={me?.email ?? 'you@example.com'}
                  onChange={(e) => setTestTo(e.target.value)}
                />
                <button className="secondary" disabled={busy} onClick={() => sendTest.mutate()}>
                  {sendTest.isPending ? 'Queueing…' : 'Send test email'}
                </button>
              </div>
            </>
          )}
        </>
      )}

      {failure && <p className="err small">{(failure as Error).message}</p>}
      {note && !failure && <p className="ok small">{note}</p>}

      {data?.recent && data.recent.length > 0 && (
        <>
          <h3>Recent messages</h3>
          <p className="muted small">
            &ldquo;Sent&rdquo; means the mail server accepted it, which is not the same as
            it arriving — check the spam folder before anything else.
          </p>
          <table className="table">
            <thead>
              <tr>
                <th>When</th>
                <th>To</th>
                <th>Status</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {data.recent.map((row) => (
                <tr key={row.message_id}>
                  <td>{row.created_at ? new Date(row.created_at).toLocaleString() : '—'}</td>
                  <td className="clamp">{row.to_address}</td>
                  <td>
                    <span className={statusClass(row.status)}>{row.status}</span>
                  </td>
                  <td className="clamp">
                    {row.error ?? (row.smtp_code ? `SMTP ${row.smtp_code}` : '—')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
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

      {can('instance.view') && <DisclosureCard />}

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
      {can('instance.view') && <EmailCard />}

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
