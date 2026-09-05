import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getRulePerformance } from '../api/analytics'
import {
  captionLine,
  createRule,
  deleteRule,
  listPosts,
  listRules,
  mediaImage,
  patchRule,
  postKind,
  type Post,
  type Rule,
} from '../api/rules'
import { RuleEditor } from '../components/rules/RuleEditor'

function describe(rule: Rule): string {
  const trigger = rule.keywords?.length ? rule.keywords.join(', ') : 'any word'
  if (rule.trigger === 'story_reply') return `Story replies containing ${trigger} → DM`
  if (rule.trigger === 'dm_keyword') return `DMs containing ${trigger} → DM`
  return `Comments containing ${trigger} → DM`
}

/**
 * Which post this automation runs on, in one line.
 *
 * The thumbnail alone is too small to recognise a post from, so every row says
 * in words what it is attached to. A rule with no single post says so rather
 * than leaving the line blank, and a post outside the newest 200 the server
 * returns falls back to its media id -- unhelpful, but still identifying, and
 * better than a row that looks broken.
 */
function subjectLine(rule: Rule, post?: Post): string {
  if (rule.trigger === 'story_reply') {
    const count = rule.instagram_story_ids?.length ?? 0
    if (!count) return 'Any story'
    return count === 1 ? '1 selected story' : `${count} selected stories`
  }
  if (rule.trigger === 'dm_keyword') return 'Direct messages'
  if (rule.scope === 'any' || rule.instagram_media_ids.length === 0) return 'Any post'
  if (!post) return rule.instagram_media_ids[0]
  return captionLine(post.caption) || `${postKind(post)} ${post.instagram_media_id}`
}

/** Stands in for the thumbnail when a rule has no one post to picture. */
function thumbLabel(rule: Rule): string | null {
  if (rule.trigger === 'story_reply') return 'STORY'
  if (rule.trigger === 'dm_keyword') return 'DM'
  if (rule.scope === 'any' || rule.instagram_media_ids.length === 0) return 'ANY'
  return null
}

export function Automations() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const [editing, setEditing] = useState<{ post?: Post; rule: Rule } | null>(null)

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['rules'] })
    // Prefix match, so the paged rails follow. Deleting a rule has to return
    // the card to "Set up automation" as promptly as creating one changes it.
    queryClient.invalidateQueries({ queryKey: ['posts'] })
    queryClient.invalidateQueries({ queryKey: ['stories'] })
    queryClient.invalidateQueries({ queryKey: ['rule-performance'] })
  }

  const { data: rules, isLoading } = useQuery({ queryKey: ['rules'], queryFn: () => listRules() })
  const { data: posts } = useQuery({ queryKey: ['posts'], queryFn: () => listPosts(), retry: false })
  const { data: performance } = useQuery({
    queryKey: ['rule-performance'],
    queryFn: getRulePerformance,
  })

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => patchRule(id, { enabled }),
    onSuccess: invalidate,
  })
  const remove = useMutation({ mutationFn: deleteRule, onSuccess: invalidate })
  const duplicate = useMutation({
    mutationFn: (rule: Rule) => {
      const { id: _id, ...rest } = rule
      return createRule({ ...rest, name: `${rule.name || 'Comments → DM'} (copy)`, enabled: false })
    },
    onSuccess: invalidate,
  })

  const postFor = (rule: Rule) =>
    posts?.find((p) => rule.instagram_media_ids.includes(p.instagram_media_id))

  const statsFor = (id: string) => performance?.find((p) => p.id === id)
  const withoutRules = posts?.filter((p) => p.rule_count === 0).length ?? 0

  return (
    <>
      <h1>Automations</h1>
      <p className="muted">Turn your Instagram engagement into DMs, automatically.</p>

      <section className="section">
        <h2>Your automations</h2>
        <p className="muted small" style={{ marginBottom: 14 }}>
          Manage your automations and track their performance below.
        </p>

        {withoutRules > 0 && (
          <div className="card warn-card">
            <span>
              <strong>
                {withoutRules} recent post{withoutRules > 1 ? 's have' : ' has'} no automation.
              </strong>{' '}
              You're missing comments that could turn into DMs.
            </span>
            <button onClick={() => navigate('/posts')}>Set them up</button>
          </div>
        )}

        {isLoading && <p className="muted">Loading…</p>}

        {rules?.length === 0 && (
          <div className="card">
            <h2>No automations yet</h2>
            <p className="muted">
              Go to Posts &amp; Reels, pick a post, and set one up.
            </p>
            <button className="cta" onClick={() => navigate('/posts')}>
              Choose a post
            </button>
          </div>
        )}

        {rules && rules.length > 0 && (
          <div className="card table-card">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>DMs</th>
                  <th>Clicks</th>
                  <th>CTR</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => {
                  const stats = statsFor(rule.id)
                  const post = postFor(rule)
                  const thumb = post ? mediaImage(post) : undefined
                  const label = thumbLabel(rule)
                  return (
                    <tr key={rule.id}>
                      <td>
                        <div className="post-row">
                          <span className="row-thumb">
                            {thumb ? (
                              <img src={thumb} alt="" loading="lazy" />
                            ) : label ? (
                              <span className="row-thumb-label">{label}</span>
                            ) : (
                              <span className="row-thumb-empty" />
                            )}
                          </span>
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontWeight: 650 }}>
                              {rule.name || 'Comments → DM'}
                            </div>
                            <div className="muted small row-caption">{describe(rule)}</div>
                            <div className="muted row-post">{subjectLine(rule, post)}</div>
                          </div>
                        </div>
                      </td>
                      <td>{stats?.dms_sent ?? 0}</td>
                      <td>{stats?.link_clicks ?? 0}</td>
                      <td className={stats?.click_through_rate ? 'ok' : ''}>
                        {stats?.click_through_rate ?? 0}%
                      </td>
                      <td>
                        <span className={rule.enabled ? 'pill on' : 'pill paused'}>
                          {rule.enabled ? 'Active' : 'Paused'}
                        </span>
                      </td>
                      <td className="actions-cell">
                        <button
                          className="secondary"
                          onClick={() => toggle.mutate({ id: rule.id, enabled: !rule.enabled })}
                        >
                          {rule.enabled ? 'Pause' : 'Resume'}
                        </button>
                        <span className="menu-wrap">
                          <button
                            className="link kebab"
                            aria-label="More actions"
                            onClick={() => setMenuFor(menuFor === rule.id ? null : rule.id)}
                          >
                            ⋮
                          </button>
                          {menuFor === rule.id && (
                            <>
                              <div className="menu-backdrop" onClick={() => setMenuFor(null)} />
                              <div className="menu">
                                <button
                                  className="menu-item"
                                  onClick={() => {
                                    setMenuFor(null)
                                    // A story-reply or DM rule has no post, and
                                    // is still perfectly editable. Sending it to
                                    // Posts would make it uneditable forever.
                                    if (post || rule.trigger !== 'comment') {
                                      setEditing({ post, rule })
                                    } else {
                                      navigate('/posts')
                                    }
                                  }}
                                >
                                  Edit automation
                                </button>
                                <button
                                  className="menu-item"
                                  onClick={() => {
                                    setMenuFor(null)
                                    duplicate.mutate(rule)
                                  }}
                                >
                                  Duplicate
                                </button>
                                <button
                                  className="menu-item danger-text"
                                  onClick={() => {
                                    setMenuFor(null)
                                    if (confirm('Delete this automation?')) remove.mutate(rule.id)
                                  }}
                                >
                                  Delete automation
                                </button>
                              </div>
                            </>
                          )}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {editing && (
        <RuleEditor
          post={editing.post}
          trigger={editing.rule.trigger}
          initial={editing.rule}
          editingId={editing.rule.id}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  )
}
