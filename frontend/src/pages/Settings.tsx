import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { apiDelete, apiGet, apiPost, apiPut } from '../api/client'
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

interface GeoIpStatus {
  configured: boolean
  account_id?: string | null
  database: { present: boolean; size_bytes?: number; updated_at?: number }
}

function GeoIpCard() {
  const queryClient = useQueryClient()
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
        Encrypted before it is stored, and never returned by the API. Verified against MaxMind
        when you save, so a typo is caught here rather than showing up later as an empty report.
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

      {failure && <p className="err small">{(failure as Error).message}</p>}
      {note && !failure && <p className="ok small">{note}</p>}
    </div>
  )
}

export function Settings() {
  const { data } = useQuery({
    queryKey: ['ready'],
    queryFn: () => apiGet<ReadyResponse>('/ready'),
    refetchInterval: 30_000,
  })

  return (
    <>
      <h1>Settings</h1>
      <p className="muted">
        Configuration lives in <code>.env</code> on the server, not in the browser — Meta
        credentials must never be editable from a web form.
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
            label="Meta configuration"
            value={
              data?.checks.config ? (
                <span className="ok">complete</span>
              ) : (
                <span className="err">missing: {data?.missing_config?.join(', ')}</span>
              )
            }
          />
        </dl>
      </div>

      <GeoIpCard />

      <div className="card">
        <h2>Useful commands</h2>
        <pre className="code-block">{`make up               # start everything
make reload           # apply .env changes
make funnel           # expose publicly via Tailscale
make tunnel-status    # print the URLs for the Meta dashboard
make bootstrap-admin  # create another admin user
make test             # run the backend suite`}</pre>
      </div>
    </>
  )
}
