import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { getRulePerformance, getSummary, getTopCommenters } from '../api/analytics'
import { listAccounts } from '../api/instagram'
import { listPosts, syncPosts, type Post, type Rule } from '../api/rules'
import { usePermissions } from '../hooks/usePermissions'
import { PostCard } from '../components/common/PostCard'
import { RuleEditor } from '../components/rules/RuleEditor'

function Stat({
  label,
  value,
  hint,
  icon,
}: {
  label: string
  value: string | number
  hint?: string
  icon: React.ReactNode
}) {
  return (
    <div className="stat">
      <div className="stat-top">
        <div className="stat-label">{label}</div>
        <span className="stat-icon">{icon}</span>
      </div>
      <div className="stat-value">{value}</div>
      {hint && <div className="small muted">{hint}</div>}
    </div>
  )
}

const si = {
  width: 16,
  height: 16,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.9,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

export function Dashboard() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const [editing, setEditing] = useState<{ post: Post; initial?: Partial<Rule> } | null>(null)

  const { data: summary, isLoading } = useQuery({
    queryKey: ['summary'],
    queryFn: () => getSummary(7),
    refetchInterval: 30_000,
  })
  const { data: accounts } = useQuery({ queryKey: ['instagram-accounts'], queryFn: listAccounts })
  const { data: posts } = useQuery({
    queryKey: ['posts'],
    queryFn: () => listPosts(),
    retry: false,
  })

  // The rail pages independently of the full list above, so scrolling it does
  // not refetch everything the other sections already have.
  const PAGE = 8
  const rail = useInfiniteQuery({
    // A prefix of ['posts'], so invalidating that covers the rail too. A
    // standalone key like ['posts-rail'] silently went stale on every save.
    queryKey: ['posts', 'rail'],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => listPosts(PAGE, pageParam as number),
    // A short page means the end; there is no total to compare against.
    getNextPageParam: (last, all) =>
      last.length < PAGE ? undefined : all.reduce((n, page) => n + page.length, 0),
    retry: false,
  })
  const railPosts = rail.data?.pages.flat() ?? []

  const sync = useMutation({
    mutationFn: syncPosts,
    onSuccess: () => {
      // Prefix match: this covers ['posts', 'rail'] as well.
      queryClient.invalidateQueries({ queryKey: ['posts'] })
      queryClient.invalidateQueries({ queryKey: ['summary'] })
    },
  })

  const onRailScroll = useCallback(
    (event: React.UIEvent<HTMLDivElement>) => {
      const el = event.currentTarget
      // Trigger a screen-width early so the next page is usually already there
      // by the time the reader reaches the end.
      const remaining = el.scrollWidth - el.scrollLeft - el.clientWidth
      if (remaining < el.clientWidth * 0.5 && rail.hasNextPage && !rail.isFetchingNextPage) {
        rail.fetchNextPage()
      }
    },
    [rail],
  )
  const { data: rules } = useQuery({ queryKey: ['rule-performance'], queryFn: getRulePerformance })
  const { data: commenters } = useQuery({ queryKey: ['top-commenters'], queryFn: getTopCommenters })

  const account = accounts?.[0]

  // Neither branch below may replace the tree while the editor is open. Its
  // draft lives in component state initialised at mount (`RuleEditor.tsx`), so
  // unmounting it silently discards whatever has been typed and remounts a
  // blank form -- and `editing` stays set, so the dialog reappears looking like
  // it was simply reset. `editing` is only ever set from the branch below, so
  // guarding these costs nothing on the paths that matter.
  if (!editing && summary && !summary.connected) {
    return (
      <>
        <h1>Welcome</h1>
        <div className="card">
          <h2>Set up your Instagram app</h2>
          <p className="muted">
            Connecting an account needs a Meta app in your own name. The guided setup walks
            through creating one, then connects the account for you.
          </p>
          <Link to="/get-started">
            <button className="cta">Start guided setup</button>
          </Link>
          <p className="muted small">
            Already have your Meta app? <Link to="/instagram">Connect Instagram</Link>{' '}
            directly.
          </p>
        </div>
      </>
    )
  }

  if (!editing && isLoading) return <p className="muted">Loading…</p>

  const withoutRules = posts?.filter((p) => p.rule_count === 0) ?? []

  return (
    <>
      <h1>Welcome {account ? `@${account.username}` : ''}!</h1>

      {/* Sections rather than the whole route: `/` is where the logo, the
          no-access card and every post-login redirect land, so it must never
          refuse anyone. A role that can see none of these gets the card below. */}
      {!can('content.view') && !can('analytics.view') && !can('automations.view') && (
        <div className="card">
          <h2>Nothing to show yet</h2>
          <p className="muted">
            Your role does not include any of the dashboard&rsquo;s sections. Use the sidebar to
            reach what you do have access to, or ask an administrator for more.
          </p>
        </div>
      )}

      {can('content.view') && (
      <section className="section">
        <div className="section-head">
          <div>
            <h2>Today's actions</h2>
            <p className="muted small">
              {withoutRules.length > 0
                ? `${withoutRules.length} recent post${withoutRules.length > 1 ? 's don’t' : ' doesn’t'} have an automation yet.`
                : 'Every synced post has an automation.'}
            </p>
          </div>
          <div className="section-actions">
            {/* This used to only invalidate the react-query cache, which re-reads
                what the server already had and so could never surface a new post.
                Pulling from Instagram is a sync, exactly as the Posts page does. */}
            {can('content.edit') && (
              <button className="link" disabled={sync.isPending} onClick={() => sync.mutate()}>
                {sync.isPending ? 'Syncing…' : 'Sync from Instagram'}
              </button>
            )}
            <button className="link" onClick={() => navigate('/posts')}>
              View all →
            </button>
          </div>
        </div>

        {sync.error && (
          <p className="err small" role="alert">
            {(sync.error as Error).message}
          </p>
        )}
        {sync.isSuccess && !sync.isPending && (
          <p className="ok small">
            Synced {sync.data?.synced ?? 0} post{sync.data?.synced === 1 ? '' : 's'} from Instagram.
          </p>
        )}

        {railPosts.length === 0 && !rail.isLoading ? (
          <div className="card">
            <p className="muted">
              No posts synced yet. <Link to="/posts">Sync from Instagram</Link> to get started.
            </p>
          </div>
        ) : (
          <div className="scroller" onScroll={onRailScroll}>
            {railPosts.map((post) => (
              <PostCard
                key={post.id}
                post={post}
                username={account?.username ?? 'you'}
                onSetUp={() =>
                  post.rule_count > 0 ? navigate('/automations') : setEditing({ post })
                }
                onClone={
                  can('automations.create') ? () => navigate(`/posts/${post.id}/clone`) : undefined
                }
              />
            ))}

            {rail.isFetchingNextPage && (
              <div className="rail-more" role="status">
                <span className="spinner" aria-hidden="true" />
                Loading more…
              </div>
            )}
          </div>
        )}
      </section>
      )}

      {can('analytics.view') && (
      <section className="section">
        <h2>Performance snapshot</h2>
        <p className="muted small" style={{ marginBottom: 14 }}>
          Last 7 days.
        </p>
        <div className="stat-grid">
          <Stat
            label="DMs sent"
            value={summary?.dms_sent ?? 0}
            icon={
              <svg {...si}>
                <path d="M20 12a7.5 7.5 0 0 1-10.9 6.7L4 20l1.4-4.6A7.5 7.5 0 1 1 20 12Z" />
              </svg>
            }
          />
          <Stat
            label="Link clicks"
            value={summary?.link_clicks ?? 0}
            icon={
              <svg {...si}>
                <path d="m5 3 13 8-5.5 1.6L9.6 18 5 3Z" />
              </svg>
            }
          />
          <Stat
            label="Click-through"
            value={`${summary?.click_through_rate ?? 0}%`}
            hint="Clicks per link sent"
            icon={
              <svg {...si}>
                <circle cx="12" cy="12" r="9" />
                <path d="M12 7v5l3 2" />
              </svg>
            }
          />
          <Stat
            label="Leads collected"
            value={summary?.leads_captured ?? 0}
            icon={
              <svg {...si}>
                <circle cx="9" cy="8" r="3.2" />
                <path d="M3 20a6 6 0 0 1 12 0" />
              </svg>
            }
          />
        </div>

        <div className="stat-grid">
          <Stat
            label="Comments"
            value={summary?.comments ?? 0}
            icon={<svg {...si}><path d="M20 12a7.5 7.5 0 0 1-10.9 6.7L4 20l1.4-4.6A7.5 7.5 0 1 1 20 12Z" /></svg>}
          />
          <Stat label="Matched" value={summary?.matched ?? 0} icon={<svg {...si}><path d="m4 12 5 5L20 6" /></svg>} />
          <Stat label="Unmatched" value={summary?.unmatched ?? 0} icon={<svg {...si}><path d="M6 6l12 12M18 6 6 18" /></svg>} />
          <Stat
            label="Dry runs"
            value={summary?.dry_runs ?? 0}
            hint="Recorded, not sent"
            icon={<svg {...si}><path d="M12 3v18M3 12h18" /></svg>}
          />
        </div>
      </section>
      )}

      {can('automations.view') && rules && rules.length > 0 && (
        <section className="section">
          <h2>Automation performance</h2>
          <div className="card">
            <table className="table">
              <thead>
                <tr>
                  <th>Automation</th>
                  <th>Keywords</th>
                  <th>DMs</th>
                  <th>Clicks</th>
                  <th>CTR</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => (
                  <tr key={rule.id}>
                    <td>
                      <strong>{rule.name}</strong>
                      {!rule.enabled && <span className="muted small"> · paused</span>}
                    </td>
                    <td className="muted small">{rule.keywords.join(', ') || 'any word'}</td>
                    <td>{rule.dms_sent}</td>
                    <td>{rule.link_clicks}</td>
                    <td>{rule.click_through_rate}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {commenters && commenters.length > 0 && (
        <section className="section">
          <h2>Top commenters</h2>
          <div className="card">
            <ol className="ranked">
              {commenters.map((c, i) => (
                <li key={c.username}>
                  <span>
                    <span className="rank">{i + 1}</span> @{c.username}
                  </span>
                  <span className="muted small">
                    {c.comments} comment{c.comments > 1 ? 's' : ''}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        </section>
      )}

      {editing && (
        <RuleEditor post={editing.post} initial={editing.initial} onClose={() => setEditing(null)} />
      )}
    </>
  )
}
