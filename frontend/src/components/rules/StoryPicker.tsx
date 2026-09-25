import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { listStories, mediaImage, syncStories, type Story } from '../../api/rules'
import { MediaThumb } from '../common/MediaThumb'

/**
 * Which stories a story-reply automation answers.
 *
 * Empty selection means every story, and that is the default — a story lives
 * 24 hours, so pinning a rule to one makes the rule expire with it. Narrowing
 * is opt-in, and the consequence is stated on screen rather than discovered a
 * day later.
 *
 * Only active stories are offered. An expired one cannot be replied to, so
 * selecting it would build a rule that can never match.
 */

function label(story: Story): string {
  if (story.caption?.trim()) return story.caption.trim().slice(0, 40)
  return story.media_type === 'VIDEO' ? 'Video story' : 'Photo story'
}

function posted(story: Story): string {
  // Meta omits `published_at` on video stories, so `first_seen_at` — when the
  // sync first saw it — is the fallback. It is an approximation, and says so.
  const exact = story.published_at
  const stamp = exact ?? story.first_seen_at
  if (!stamp) return ''
  const when = new Date(stamp)
  if (Number.isNaN(when.getTime())) return ''
  const hours = Math.floor((Date.now() - when.getTime()) / 3_600_000)
  const rough = hours < 1 ? 'just now' : hours === 1 ? '1h ago' : `${hours}h ago`
  return exact ? rough : `seen ${rough}`
}

export function StoryPicker({
  selected,
  expireWithStory,
  onChange,
  onExpireChange,
}: {
  selected: string[]
  expireWithStory: boolean
  onChange: (ids: string[]) => void
  onExpireChange: (value: boolean) => void
}) {
  const queryClient = useQueryClient()
  const { data: stories, isLoading } = useQuery({
    queryKey: ['stories'],
    queryFn: listStories,
  })

  const sync = useMutation({
    mutationFn: syncStories,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['stories'] })
      queryClient.invalidateQueries({ queryKey: ['rules'] })
    },
  })

  const toggle = (id: string) =>
    onChange(selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id])

  const all = selected.length === 0

  return (
    <div className="story-picker">
      <div className="story-picker-head">
        <label className="inline">
          <input
            type="checkbox"
            checked={all}
            onChange={(e) => e.target.checked && onChange([])}
          />
          Reply to every story
        </label>
        <button
          type="button"
          className="link small"
          disabled={sync.isPending}
          onClick={() => sync.mutate()}
        >
          {sync.isPending ? 'Refreshing…' : 'Refresh stories'}
        </button>
      </div>

      {all && (
        <p className="muted small">
          Recommended. Stories expire after 24 hours, so an automation tied to specific ones
          stops working when they do.
        </p>
      )}

      {isLoading && <p className="muted small">Loading stories…</p>}

      {!isLoading && (stories?.length ?? 0) === 0 && (
        <p className="muted small">
          No active stories. Post one and refresh — only live stories can be picked, because an
          expired story cannot be replied to.
        </p>
      )}

      {(stories?.length ?? 0) > 0 && (
        <>
          <div className="story-grid">
            {stories!.map((story) => {
              const on = selected.includes(story.instagram_story_id)
              return (
                <button
                  key={story.instagram_story_id}
                  type="button"
                  className={on ? 'story-card on' : 'story-card'}
                  aria-pressed={on}
                  onClick={() => toggle(story.instagram_story_id)}
                >
                  <span className="story-thumb">
                    <MediaThumb
                      src={mediaImage(story)}
                      empty={<span className="story-thumb-empty" aria-hidden="true" />}
                    />
                    {on && <span className="story-tick" aria-hidden="true">✓</span>}
                  </span>
                  <span className="story-label">{label(story)}</span>
                  <span className="muted small">{posted(story)}</span>
                </button>
              )
            })}
          </div>

          {!all && (
            <label className="inline">
              <input
                type="checkbox"
                checked={expireWithStory}
                onChange={(e) => onExpireChange(e.target.checked)}
              />
              Turn this automation off when {selected.length === 1 ? 'the story' : 'the last story'}{' '}
              expires
            </label>
          )}
          {!all && !expireWithStory && (
            <p className="muted small">
              It will stay switched on with nothing to match, which is rarely what you want.
            </p>
          )}
        </>
      )}
    </div>
  )
}
