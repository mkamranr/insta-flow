import { useQuery } from '@tanstack/react-query'
import { getSummary, getTopCommenters } from '../api/analytics'

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
    </>
  )
}
