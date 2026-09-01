import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../api/client'
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
