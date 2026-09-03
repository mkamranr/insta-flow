import { useQuery } from '@tanstack/react-query'
import { getSuperfans, getSummary, getTopCommenters } from '../api/analytics'

function HeartIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1.1 1L12 21l7.7-7.6 1.1-1a5.5 5.5 0 0 0 0-7.8Z" />
    </svg>
  )
}

function Superfans() {
  const { data, isLoading } = useQuery({ queryKey: ['superfans'], queryFn: () => getSuperfans() })

  return (
    <section className="section">
      <div className="card">
        <div className="superfan-head">
          <span className="superfan-mark" aria-hidden="true">
            <HeartIcon />
          </span>
          <div>
            <h2>Superfans</h2>
            <p className="muted small">People who keep turning up, week after week.</p>
          </div>
        </div>

        {isLoading && <p className="muted small">Loading…</p>}

        {data && data.rows.length === 0 && (
          <div className="empty-state">
            <p className="empty-title">No superfans yet</p>
            <p className="muted small">
              {/* The server's own wording, not a paraphrase of it. */}
              A superfan is someone with {data.rule}.
            </p>
          </div>
        )}

        {data && data.rows.length > 0 && (
          <>
            <p className="muted small">
              {data.rows.length} {data.rows.length === 1 ? 'person' : 'people'} with {data.rule},
              over the last {data.weeks_considered} weeks.
            </p>
            <table className="table">
              <thead>
                <tr>
                  <th>Person</th>
                  <th className="numeric">Streak</th>
                  <th className="numeric">Active weeks</th>
                  <th className="numeric">Comments</th>
                  <th>Last seen</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((fan, index) => (
                  <tr key={fan.instagram_user_id}>
                    <td>
                      <span className="rank">{index + 1}</span>@{fan.username || 'unknown'}
                    </td>
                    <td className="numeric">
                      {fan.streak_weeks} {fan.streak_weeks === 1 ? 'week' : 'weeks'}
                    </td>
                    <td className="numeric">{fan.qualifying_weeks}</td>
                    <td className="numeric">{fan.comments}</td>
                    <td className="muted small">
                      {fan.last_at ? new Date(fan.last_at).toLocaleDateString() : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </section>
  )
}

export function Insights() {
  const { data: summary } = useQuery({ queryKey: ['summary'], queryFn: () => getSummary(7) })
  const { data: commenters } = useQuery({
    queryKey: ['top-commenters'],
    queryFn: getTopCommenters,
  })

  const unique = commenters?.length ?? 0
  const total = summary?.comments ?? 0
  const average = unique ? (total / unique).toFixed(1) : '–'

  return (
    <>
      <h1>Audience insights</h1>
      <p className="muted">Discover your top commenters and most engaged followers.</p>

      <div className="stat-grid">
        <div className="stat">
          <div className="stat-label">Total comments</div>
          <div className="stat-value">{total}</div>
          <div className="small muted">Last 7 days</div>
        </div>
        <div className="stat">
          <div className="stat-label">Unique commenters</div>
          <div className="stat-value">{unique || '–'}</div>
          <div className="small muted">Individual people</div>
        </div>
        <div className="stat">
          <div className="stat-label">Avg comments / person</div>
          <div className="stat-value">{average}</div>
          <div className="small muted">Engagement depth</div>
        </div>
      </div>

      <section className="section">
        <h2>Top commenters</h2>
        <p className="muted small" style={{ marginBottom: 14 }}>
          The people who engage most with your content.
        </p>
        <div className="card">
          {!commenters?.length && (
            <p className="muted">
              No comments recorded yet. They appear here once webhooks start arriving.
            </p>
          )}
          <ol className="ranked">
            {commenters?.map((c, i) => (
              <li key={c.username}>
                <span>
                  <span className="rank">{i + 1}</span>@{c.username}
                </span>
                <span className="muted small">
                  {c.comments} comment{c.comments > 1 ? 's' : ''}
                  {c.last_at && ` · last ${new Date(c.last_at).toLocaleDateString()}`}
                </span>
              </li>
            ))}
          </ol>
        </div>
      </section>
    
      <Superfans />
</>
  )
}
