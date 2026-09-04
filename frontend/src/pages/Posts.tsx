import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { usePermissions } from '../hooks/usePermissions'
import { ApiError } from '../api/client'
import { listAccounts } from '../api/instagram'
import { STARTER_TEMPLATES, listPosts, mediaImage, syncPosts, type Post, type Rule } from '../api/rules'
import { PostCard } from '../components/common/PostCard'
import { RuleEditor } from '../components/rules/RuleEditor'

export function Posts() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { can } = usePermissions()
  const [editing, setEditing] = useState<{ post: Post; initial?: Partial<Rule> } | null>(null)
  const [templateFor, setTemplateFor] = useState<Post | null>(null)

  const { data: accounts } = useQuery({ queryKey: ['instagram-accounts'], queryFn: listAccounts })
  const { data: posts, isLoading, isError, error } = useQuery({
    queryKey: ['posts'],
    queryFn: () => listPosts(),
    retry: false,
  })

  // The rail pages on its own, like the dashboard's. The table below still
  // reads the full list, so the two must not share a query key.
  const PAGE = 8
  const rail = useInfiniteQuery({
    queryKey: ['posts', 'rail', 'page'],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => listPosts(PAGE, pageParam as number),
    // A short page means the end; there is no total to compare against.
    getNextPageParam: (last, all) =>
      last.length < PAGE ? undefined : all.reduce((n, page) => n + page.length, 0),
    retry: false,
  })
  const railPosts = rail.data?.pages.flat() ?? []

  const onRailScroll = useCallback(
    (event: React.UIEvent<HTMLDivElement>) => {
      const el = event.currentTarget
      // A screen-width early, so the next page has usually landed by the time
      // the reader reaches the end.
      const remaining = el.scrollWidth - el.scrollLeft - el.clientWidth
      if (remaining < el.clientWidth * 0.5 && rail.hasNextPage && !rail.isFetchingNextPage) {
        rail.fetchNextPage()
      }
    },
    [rail],
  )

  const sync = useMutation({
    mutationFn: syncPosts,
    onSuccess: () => {
      // Prefix match: this covers ['posts', 'rail', 'page'] as well.
      queryClient.invalidateQueries({ queryKey: ['posts'] })
    },
  })

  const username = accounts?.[0]?.username ?? 'you'
  const withoutRules = posts?.filter((p) => p.rule_count === 0).length ?? 0

  const openFor = (post: Post) =>
    post.rule_count > 0 ? navigate('/automations') : setTemplateFor(post)

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Posts &amp; Reels</h1>
          <p className="muted">Manage automations for your recent Instagram posts and reels.</p>
        </div>
        <button className="secondary" onClick={() => sync.mutate()} disabled={sync.isPending}>
          {sync.isPending ? 'Syncing…' : 'Refresh'}
        </button>
      </div>

      {sync.isError && <div className="card err">{(sync.error as ApiError).message}</div>}
      {sync.isSuccess && <div className="card ok">Synced {sync.data.synced} posts.</div>}
      {isError && <div className="card err">{(error as ApiError).message}</div>}
      {isLoading && <p className="muted">Loading posts…</p>}

      {posts?.length === 0 && (
        <div className="card">
          <h2>No posts yet</h2>
          <p className="muted">
            Hit Refresh to pull your recent posts and reels from Instagram, then pick one to
            automate.
          </p>
        </div>
      )}

      {posts && posts.length > 0 && (
        <>
          <section className="section">
            <h2>Recent posts</h2>
            <p className="muted small" style={{ marginBottom: 14 }}>
              Your latest Instagram posts. Set up automations to reply when people comment.
            </p>
            {withoutRules > 0 && (
              <div className="card warn-card">
                <span>
                  <strong>
                    {withoutRules} recent post{withoutRules > 1 ? 's have' : ' has'} no automation.
                  </strong>{' '}
                  You're missing comments that could turn into DMs.
                </span>
              </div>
            )}
            <div className="scroller" onScroll={onRailScroll}>
              {railPosts.map((post) => (
                <PostCard
                  key={post.id}
                  post={post}
                  username={username}
                  onSetUp={() => openFor(post)}
                onClone={
                  can('automations.create')
                    ? () => navigate(`/posts/${post.id}/clone`)
                    : undefined
                }
                />
              ))}
              {rail.isFetchingNextPage && (
                <div className="rail-loading muted small">Loading…</div>
              )}
            </div>
          </section>

          <section className="section">
            <h2>All content</h2>
            <p className="muted small" style={{ marginBottom: 14 }}>
              View and manage all your Instagram posts and reels.
            </p>
            <div className="card table-card">
              <table className="table">
                <thead>
                  <tr>
                    <th>Post</th>
                    <th>Status</th>
                    <th>Likes</th>
                    <th>Comments</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {posts.map((post) => (
                    <tr key={post.id}>
                      <td>
                        <div className="post-row">
                          <span className="row-thumb">
                            {mediaImage(post) ? (
                              <img src={mediaImage(post)!} alt="" loading="lazy" />
                            ) : (
                              <span className="row-thumb-empty" />
                            )}
                          </span>
                          <div style={{ minWidth: 0 }}>
                            <div className="row-caption">{post.caption ?? '(no caption)'}</div>
                            <div className="muted small">
                              {post.media_product_type ?? post.media_type ?? 'POST'}
                              {post.published_at &&
                                ` · ${new Date(post.published_at).toLocaleDateString()}`}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td>
                        <span className={post.rule_count > 0 ? 'pill on' : 'pill off'}>
                          {post.rule_count > 0 ? 'Automated' : 'Not automated'}
                        </span>
                      </td>
                      <td>{post.like_count ?? '–'}</td>
                      <td>{post.comments_count ?? '–'}</td>
                      <td className="actions-cell">
                        <button
                          className={post.rule_count > 0 ? 'secondary' : 'cta'}
                          onClick={() => openFor(post)}
                        >
                          {post.rule_count > 0 ? 'View automation' : 'Set up automation'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      {templateFor && (
        <div className="modal-backdrop" onClick={() => setTemplateFor(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-head">
              <h2>Start from a template</h2>
              <button className="link" onClick={() => setTemplateFor(null)} aria-label="Close">
                ×
              </button>
            </header>
            <div className="modal-body">
              <div className="template-grid">
                {STARTER_TEMPLATES.map((template) => (
                  <button
                    key={template.id}
                    className="template-card"
                    onClick={() => {
                      setEditing({ post: templateFor, initial: template.rule })
                      setTemplateFor(null)
                    }}
                  >
                    <strong>{template.title}</strong>
                    <span className="muted small">{template.blurb}</span>
                  </button>
                ))}
                <button
                  className="template-card"
                  onClick={() => {
                    setEditing({ post: templateFor })
                    setTemplateFor(null)
                  }}
                >
                  <strong>Start from scratch</strong>
                  <span className="muted small">Set the keywords and message yourself.</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {editing && (
        <RuleEditor post={editing.post} initial={editing.initial} onClose={() => setEditing(null)} />
      )}
    </>
  )
}
