import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { cloneRule, listPosts, listRules, mediaImage, type Post, type Rule } from '../api/rules'
import type { ApiError } from '../api/client'

/** Meta's documented ceiling, mirrored from backend/app/constants.py. */
const PRIVATE_REPLIES_PER_HOUR = 750

export function CloneAutomation() {
  const { postId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [sourceRuleId, setSourceRuleId] = useState<string | null>(null)

  const { data: posts, isLoading } = useQuery({
    queryKey: ['posts'],
    queryFn: () => listPosts(),
    retry: false,
  })
  // The unfiltered list on purpose: `listRules(mediaId)` filters `enabled: true`
  // server-side, so a paused automation would be invisible here even though it
  // makes the post read "Automated".
  const { data: rules } = useQuery({ queryKey: ['rules'], queryFn: () => listRules() })

  const source = posts?.find((p) => p.id === postId)

  const candidates = useMemo(
    () => (rules ?? []).filter((r) => source && r.instagram_media_ids.includes(source.instagram_media_id)),
    [rules, source],
  )
  // A post may hold several automations, so "clone this post's automation" is
  // ambiguous. Picking the first silently would be unexplainable.
  const rule: Rule | undefined =
    candidates.length === 1 ? candidates[0] : candidates.find((r) => r.id === sourceRuleId)

  const unlinked = useMemo(() => (posts ?? []).filter((p) => p.rule_count === 0), [posts])

  const clone = useMutation({
    mutationFn: () => cloneRule(rule!.id, [...selected]),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['posts'] })
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      queryClient.invalidateQueries({ queryKey: ['rule-performance'] })
      if (result.skipped.length === 0) navigate('/automations')
      else setSelected(new Set())
    },
  })

  const toggle = (mediaId: string) =>
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(mediaId)) next.delete(mediaId)
      else next.add(mediaId)
      return next
    })

  if (isLoading) return <p className="muted">Loading…</p>
  if (!source) {
    return (
      <div className="card">
        <h2>Post not found</h2>
        <p className="muted">
          It may not be synced yet. <button className="link" onClick={() => navigate('/posts')}>Back to posts</button>
        </p>
      </div>
    )
  }

  // Limits are counted per rule, so every clone gets its own allowance. Twenty
  // clones of a 100/hour rule authorise 2,000/hour against Meta's 750.
  const perHour = rule?.limits?.per_hour ?? 0
  const projected = selected.size * perHour
  const overCeiling = perHour > 0 && projected > PRIVATE_REPLIES_PER_HOUR

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Clone automation</h1>
          <p className="muted">Copy this post&rsquo;s automation onto other posts.</p>
        </div>
        <button className="secondary" onClick={() => navigate('/posts')}>
          Back to posts
        </button>
      </div>

      <div className="card">
        <div className="post-row">
          <span className="row-thumb">
            {mediaImage(source) ? <img src={mediaImage(source)!} alt="" loading="lazy" /> : null}
          </span>
          <span>
            <strong>Source post</strong>
            <span className="row-caption muted small">{source.caption ?? 'No caption'}</span>
          </span>
        </div>

        {candidates.length === 0 && (
          <p className="err small">This post has no automation to clone.</p>
        )}

        {candidates.length > 1 && (
          <>
            <label htmlFor="source-rule">Which automation?</label>
            <select
              id="source-rule"
              value={sourceRuleId ?? ''}
              onChange={(e) => setSourceRuleId(e.target.value)}
            >
              <option value="">Choose one…</option>
              {candidates.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  {candidate.name || candidate.keywords.join(', ') || 'Untitled automation'}
                </option>
              ))}
            </select>
            <p className="muted small">This post has more than one automation.</p>
          </>
        )}
      </div>

      {rule && (
        <div className="card">
          <h2>
            Posts with no automation <span className="pill">{unlinked.length}</span>
          </h2>
          {unlinked.length === 0 ? (
            <p className="muted">Every synced post already has an automation.</p>
          ) : (
            <div className="story-grid post-picker">
              {unlinked.map((post: Post) => {
                const on = selected.has(post.instagram_media_id)
                return (
                  <button
                    key={post.id}
                    type="button"
                    className={on ? 'story-card on' : 'story-card'}
                    aria-pressed={on}
                    // The caption clamps at three lines, and it is the only
                    // thing telling one post from another. Screen readers get
                    // the whole thing already -- CSS clamping does not truncate
                    // a button's accessible name -- so this closes the gap for
                    // pointer users, which is the gap that exists.
                    title={post.caption ?? undefined}
                    onClick={() => toggle(post.instagram_media_id)}
                  >
                    <span className="story-thumb">
                      {mediaImage(post) ? (
                        <img src={mediaImage(post)!} alt="" loading="lazy" />
                      ) : (
                        <span className="story-thumb-empty" aria-hidden="true" />
                      )}
                      {on && (
                        <span className="story-tick" aria-hidden="true">
                          ✓
                        </span>
                      )}
                    </span>
                    <span className="story-label">{post.caption ?? 'No caption'}</span>
                  </button>
                )
              })}
            </div>
          )}

          {clone.error && (
            <p className="err small" role="alert">
              {(clone.error as ApiError).message}
            </p>
          )}
          {clone.data && clone.data.skipped.length > 0 && (
            <p className="err small" role="alert">
              Cloned to {clone.data.created.length}.{' '}
              {clone.data.skipped.length} skipped: {clone.data.skipped[0].reason}
            </p>
          )}
          {overCeiling && (
            <p className="err small">
              Each clone carries its own limit of {perHour} DMs an hour, so {selected.size} of them
              allow up to {projected.toLocaleString()} an hour — above Meta&rsquo;s ceiling of{' '}
              {PRIVATE_REPLIES_PER_HOUR} per account. Lower the limit on the clones afterwards, or
              select fewer posts.
            </p>
          )}

          <div className="card-foot">
            <p className="muted small">
              {selected.size === 0
                ? 'The same automation will be applied to the posts you select.'
                : `${selected.size} selected. Someone who comments on several of them will get a DM from each — "only once per person" counts per automation.`}
            </p>
            <button className="link" onClick={() => navigate('/posts')}>
              Cancel
            </button>
            <button
              className="cta"
              disabled={selected.size === 0 || clone.isPending}
              onClick={() => clone.mutate()}
            >
              {clone.isPending ? 'Cloning…' : `Clone to ${selected.size || ''} ${selected.size === 1 ? 'post' : 'posts'}`}
            </button>
          </div>
        </div>
      )}
    </>
  )
}
