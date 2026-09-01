import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import {
  disconnect,
  listAccounts,
  refreshToken,
  startConnect,
  subscribeWebhooks,
  type InstagramAccount,
} from '../api/instagram'

const CONNECT_ERRORS: Record<string, string> = {
  invalid_state: 'That sign-in link had expired or was already used. Try connecting again.',
  exchange_failed: 'Meta rejected the authorization. Check META_APP_ID and META_APP_SECRET.',
  user_denied: 'You declined the permission request.',
  missing_code: 'Meta did not return an authorization code.',
  no_account_id: 'Meta did not return an Instagram account id.',
}

function AccountCard({ account }: { account: InstagramAccount }) {
  const queryClient = useQueryClient()
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['instagram-accounts'] })

  const subscribe = useMutation({ mutationFn: () => subscribeWebhooks(account.id), onSuccess: invalidate })
  const refresh = useMutation({ mutationFn: () => refreshToken(account.id), onSuccess: invalidate })
  const remove = useMutation({ mutationFn: () => disconnect(account.id), onSuccess: invalidate })

  const failure = [subscribe, refresh, remove].find((m) => m.isError)

  return (
    <div className="card">
      <h2>@{account.username}</h2>
      <dl className="kv">
        <dt>Account type</dt>
        <dd>{account.account_type ?? '—'}</dd>
        <dt>Status</dt>
        <dd className={account.status === 'connected' ? 'ok' : 'err'}>{account.status}</dd>
        <dt>Permissions</dt>
        <dd>{account.permissions.join(', ') || '—'}</dd>
        <dt>Webhook</dt>
        <dd>
          <span className={account.webhook_subscribed ? 'pill on' : 'pill paused'}>
            {account.webhook_subscribed ? 'subscribed' : 'not subscribed'}
          </span>
          {account.webhook_subscribed && (
            <div className="muted small" style={{ marginTop: 4 }}>
              {account.subscribed_fields.join(', ')}
            </div>
          )}
        </dd>
        <dt>Token expires</dt>
        <dd className={account.token_expired ? 'err' : ''}>
          {account.token_expires_at ? new Date(account.token_expires_at).toLocaleDateString() : '—'}
          {account.token_expired && ' (expired)'}
        </dd>
      </dl>

      {account.status_error && <p className="err">{account.status_error}</p>}
      {failure && <p className="err">{(failure.error as ApiError).message}</p>}

      <div className="actions">
        <button className="secondary" onClick={() => subscribe.mutate()} disabled={subscribe.isPending}>
          {account.webhook_subscribed ? 'Re-subscribe webhooks' : 'Subscribe webhooks'}
        </button>
        <button className="secondary" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
          Refresh token
        </button>
        <button
          className="danger"
          onClick={() => {
            if (confirm(`Disconnect @${account.username}? Campaigns will stop firing.`)) {
              remove.mutate()
            }
          }}
        >
          Disconnect
        </button>
      </div>
    </div>
  )
}

export function Instagram() {
  const [params] = useSearchParams()
  const { data: accounts, isLoading } = useQuery({
    queryKey: ['instagram-accounts'],
    queryFn: listAccounts,
  })

  const connect = useMutation({
    mutationFn: startConnect,
    onSuccess: ({ authorize_url }) => {
      // Full navigation: Meta will redirect the browser back to our callback.
      window.location.href = authorize_url
    },
  })

  const errorParam = params.get('error')

  return (
    <>
      <h1>Instagram</h1>

      {params.get('connected') && <div className="card ok">Account connected.</div>}
      {errorParam && (
        <div className="card err">{CONNECT_ERRORS[errorParam] ?? `Connection failed: ${errorParam}`}</div>
      )}

      {isLoading && <p className="muted">Loading…</p>}

      {accounts?.map((account) => <AccountCard key={account.id} account={account} />)}

      {accounts?.length === 0 && (
        <div className="card">
          <h2>No account connected</h2>
          <p className="muted">
            Connect an Instagram Professional account to start receiving comment webhooks.
            Your account must be an Instagram Tester on the Meta app — see{' '}
            <code>docs/meta-api.md</code>.
          </p>
          {connect.isError && <p className="err">{(connect.error as ApiError).message}</p>}
          <button className="cta" onClick={() => connect.mutate()} disabled={connect.isPending}>
            {connect.isPending ? 'Redirecting…' : 'Connect Instagram'}
          </button>
        </div>
      )}
    </>
  )
}
