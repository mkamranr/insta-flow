import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getRulePerformance } from '../api/analytics'
import {
  createRule,
  deleteRule,
  listPosts,
  listRules,
  patchRule,
  type Post,
  type Rule,
} from '../api/rules'
import { RuleEditor } from '../components/rules/RuleEditor'

function describe(rule: Rule): string {
  const trigger = rule.any_word
    ? 'any comment'
    : rule.keywords.map((k) => `'${k}'`).slice(0, 2).join(', ') +
      (rule.keywords.length > 2 ? ` +${rule.keywords.length - 2} more` : '')
  const opener = rule.opening_dm?.enabled ? ' · Opening message' : ''
  const dm = rule.message.length > 44 ? `${rule.message.slice(0, 44)}…` : rule.message
  return `Comments containing ${trigger}${opener} · DM: '${dm}'`
}

export function Automations() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const [editing, setEditing] = useState<{ post: Post; rule: Rule } | null>(null)

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['rules'] })
    queryClient.invalidateQueries({ queryKey: ['posts'] })
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
  const thumbFor = (rule: Rule) =>
    posts?.find((p) => rule.instagram_media_ids.includes(p.instagram_media_id))?.thumbnail_url
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
                  const thumb = thumbFor(rule)
                  return (
                    <tr key={rule.id}>
                      <td>
                        <div className="post-row">
                          <span className="row-thumb">
                            {thumb ? <img src={thumb} alt="" /> : <span className="row-thumb-empty" />}
                          </span>
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontWeight: 650 }}>
                              {rule.name || 'Comments → DM'}
                            </div>
                            <div className="muted small row-caption">{describe(rule)}</div>
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
                                    const post = postFor(rule)
                                    if (post) setEditing({ post, rule })
                                    else navigate('/posts')
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
          initial={editing.rule}
          editingId={editing.rule.id}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  )
}
