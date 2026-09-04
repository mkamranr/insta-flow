import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { listAccounts } from '../api/instagram'
import { listRules, listStories, mediaImage, syncStories, type Story } from '../api/rules'
import { StoryCard } from '../components/common/StoryCard'
import { RuleEditor } from '../components/rules/RuleEditor'

/**
 * Stories, and the automations that answer replies to them.
 *
 * Only *active* stories are listed. A story lives 24 hours and cannot be
 * replied to afterwards, so showing expired ones would invite automations that
 * can never fire — the same reason the picker refuses them.
 */
export function Stories() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [editing, setEditing] = useState<Story | null>(null)

  const { data: accounts } = useQuery({ queryKey: ['instagram-accounts'], queryFn: listAccounts })
  const { data: stories, isLoading, isError, error } = useQuery({
    queryKey: ['stories'],
    queryFn: listStories,
    retry: false,
  })
  // Rules, so a story can say whether an *account-wide* story rule already
  // covers it — `rule_count` only counts rules that name this story.
  const { data: rules } = useQuery({ queryKey: ['rules'], queryFn: () => listRules() })

  const sync = useMutation({
    mutationFn: syncStories,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['stories'] })
      queryClient.invalidateQueries({ queryKey: ['rules'] })
    },
  })

  const account = accounts?.[0]
  const username = account?.username ?? 'you'
  const storyRules = rules?.filter((r) => r.trigger === 'story_reply') ?? []
  const coverAll = storyRules.filter(
    (r) => r.enabled && (r.instagram_story_ids ?? []).length === 0,
  ).length

  const openFor = (story: Story) =>
    story.rule_count > 0 ? navigate('/automations') : setEditing(story)

  return (
    <>
      <div className="page-head">
        <div>
          <h1>My Stories</h1>
          <p className="muted">Manage automations for your Instagram stories.</p>
        </div>
        <button className="secondary" onClick={() => sync.mutate()} disabled={sync.isPending}>
          {sync.isPending ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {sync.isError && <div className="card err">{(sync.error as ApiError).message}</div>}
      {sync.isSuccess && (
        <div className="card ok">
          {sync.data.active} active
          {sync.data.expired > 0 && `, ${sync.data.expired} expired`}
          {sync.data.rules_disabled > 0 &&
            ` — ${sync.data.rules_disabled} automation${
              sync.data.rules_disabled > 1 ? 's' : ''
            } switched off with the stor${sync.data.rules_disabled > 1 ? 'ies' : 'y'}`}
          .
        </div>
      )}
      {isError && <div className="card err">{(error as ApiError).message}</div>}
      {isLoading && <p className="muted">Loading stories…</p>}

      {stories?.length === 0 && !isLoading && (
        <div className="card">
          <h2>No active stories</h2>
          <p className="muted">
            Stories expire after 24 hours, and only live ones are listed here — nobody can reply
            to a story that has ended. Post one and hit Refresh.
          </p>
          <p className="muted small">
            An automation that answers replies to <em>every</em> story does not need a story to
            exist first, and keeps working as you post.
          </p>
        </div>
      )}

      {stories && stories.length > 0 && (
        <>
          <section className="section">
            <h2>Recent stories</h2>
            <p className="muted small" style={{ marginBottom: 14 }}>
              Your live Instagram stories. Set up automations to reply when people answer them.
            </p>

            {coverAll > 0 && (
              <div className="card ok-card">
                <span>
                  <strong>
                    {coverAll} automation{coverAll > 1 ? 's' : ''} already answer
                    {coverAll > 1 ? '' : 's'} replies to every story.
                  </strong>{' '}
                  These stories are covered without being picked individually.
                </span>
              </div>
            )}

            <div className="scroller">
              {stories.map((story) => (
                <StoryCard
                  key={story.id}
                  story={story}
                  username={username}
                  avatarUrl={account?.profile_picture_url}
                  onSetUp={() => openFor(story)}
                />
              ))}
            </div>
          </section>

          <section className="section">
            <h2>All stories</h2>
            <p className="muted small" style={{ marginBottom: 14 }}>
              Every story currently live on your account.
            </p>
            <div className="card table-card">
              <table className="table">
                <thead>
                  <tr>
                    <th>Story</th>
                    <th>Status</th>
                    <th>Expires</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {stories.map((story) => (
                    <tr key={story.id}>
                      <td>
                        <div className="post-row">
                          <span className="row-thumb">
                            {mediaImage(story) ? (
                              <img src={mediaImage(story)!} alt="" loading="lazy" />
                            ) : (
                              <span className="row-thumb-empty" />
                            )}
                          </span>
                          <div style={{ minWidth: 0 }}>
                            <div className="row-caption">
                              {story.caption?.trim() || '(no caption)'}
                            </div>
                            <div className="muted small">
                              {story.media_type ?? 'STORY'}
                              {story.published_at &&
                                ` · ${new Date(story.published_at).toLocaleTimeString([], {
                                  hour: 'numeric',
                                  minute: '2-digit',
                                })}`}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td>
                        <span className={story.rule_count > 0 ? 'pill on' : 'pill off'}>
                          {story.rule_count > 0 ? 'Automated' : 'Not automated'}
                        </span>
                      </td>
                      <td>{expiresIn(story)}</td>
                      <td className="actions-cell">
                        <button
                          className={story.rule_count > 0 ? 'secondary' : 'cta'}
                          onClick={() => openFor(story)}
                        >
                          {story.rule_count > 0 ? 'View automation' : 'Set up automation'}
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

      {editing && (
        <RuleEditor
          trigger="story_reply"
          initial={{ instagram_story_ids: [editing.instagram_story_id] }}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  )
}

/** How long this story has left. Instagram expires them 24 hours after posting. */
function expiresIn(story: Story): string {
  const posted = story.published_at ?? story.first_seen_at
  if (!posted) return '–'
  const at = new Date(posted).getTime()
  if (Number.isNaN(at)) return '–'
  const left = Math.round((at + 24 * 3_600_000 - Date.now()) / 3_600_000)
  // A story Meta still lists is live by definition, so a non-positive number
  // means the clock is off, not that it has gone. Say "soon" rather than a
  // negative hour count.
  if (left <= 0) return 'soon'
  return left === 1 ? 'in 1 hour' : `in ${left} hours`
}
