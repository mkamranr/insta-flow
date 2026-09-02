import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getActivity } from '../api/analytics'

const FILTERS = [
  { value: '', label: 'All' },
  { value: 'success', label: 'Sent' },
  { value: 'dry_run', label: 'Dry run' },
  { value: 'skipped', label: 'Skipped' },
  { value: 'failed', label: 'Failed' },
]

const STATUS_CLASS: Record<string, string> = {
  success: 'ok',
  dry_run: 'warn-text',
  failed: 'err',
  skipped: 'muted',
  processing: 'muted',
}

/** Why a comment was skipped, in words rather than a machine reason code. */
const REASONS: Record<string, string> = {
  already_messaged: 'Already messaged this person',
  outside_reply_window: 'Comment older than 7 days',
  not_following: 'Not following you',
  rule_disabled: 'Automation is paused',
  account_not_connected: 'Instagram account not connected',
  no_message: 'Automation has no message',
  rate_limited_minute: 'Rate limit (per minute)',
  rate_limited_hour: 'Rate limit (per hour)',
  rate_limited_day: 'Rate limit (per day)',
}

/** The Activity Log tab. Rendered inside Analytics, so it carries no page heading. */
export function ActivityLog() {
  const [status, setStatus] = useState('')
  const { data: rows, isLoading } = useQuery({
    queryKey: ['activity', status],
    queryFn: () => getActivity(status || undefined),
    refetchInterval: 15_000,
  })

  return (
    <>
      <p className="muted">
        Every comment that triggered an automation, and what happened. Hover a skipped row to
        see why it was skipped.
      </p>

      <div className="filters">
        {FILTERS.map((filter) => (
          <button
            key={filter.value}
            className={status === filter.value ? 'filter active' : 'filter'}
            onClick={() => setStatus(filter.value)}
          >
            {filter.label}
          </button>
        ))}
      </div>

      {isLoading && <p className="muted">Loading…</p>}
      {rows?.length === 0 && (
        <div className="card">
          <p className="muted">Nothing yet. Comments that match an automation will appear here.</p>
        </div>
      )}

      {rows && rows.length > 0 && (
        <div className="card">
          <table className="table">
            <thead>
              <tr>
                <th>When</th>
                <th>From</th>
                <th>Comment</th>
                <th>Automation</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td className="muted small">
                    {row.created_at ? new Date(row.created_at).toLocaleString() : '—'}
                  </td>
                  <td>@{row.username || '—'}</td>
                  <td className="clamp">{row.comment_text}</td>
                  <td className="muted small">{row.rule_name}</td>
                  <td>
                    <span className={STATUS_CLASS[row.status] ?? ''}>{row.status}</span>
                    {row.policy_decision && row.status === 'skipped' && (
                      <div className="muted small">
                        {REASONS[row.policy_decision] ?? row.policy_decision}
                      </div>
                    )}
                    {row.error && <div className="err small clamp">{row.error}</div>}
                    {row.attempts > 1 && (
                      <div className="muted small">{row.attempts} attempts</div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
