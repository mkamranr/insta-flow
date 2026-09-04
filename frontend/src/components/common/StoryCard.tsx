import { mediaImage, type Story } from '../../api/rules'

/**
 * A story, as Instagram shows it: 9:16, with the automation state underneath.
 *
 * Deliberately the same card language as `PostCard` — the two sit one click
 * apart, and a different visual grammar for the same job would read as a
 * different product.
 */
export function StoryCard({
  story,
  username,
  avatarUrl,
  onSetUp,
}: {
  story: Story
  username: string
  avatarUrl?: string | null
  onSetUp: () => void
}) {
  const automated = story.rule_count > 0
  const image = mediaImage(story)
  const isVideo = story.media_type === 'VIDEO'
  const ago = relativeDay(story.published_at ?? story.first_seen_at)

  return (
    <article className="ig-card story-tile">
      <header className="ig-head">
        {avatarUrl ? (
          <img className="ig-avatar-img" src={avatarUrl} alt="" />
        ) : (
          <span className="ig-avatar">{username.slice(0, 1).toUpperCase()}</span>
        )}
        <span className="ig-user">{username}</span>
        <span className="ig-dots">···</span>
      </header>

      <div className="ig-media story-media">
        {image ? (
          <img src={image} alt="" loading="lazy" />
        ) : (
          <div className="ig-media-empty">STORY</div>
        )}
        {/* Videos get a still, so the play badge is the only thing saying this
            is not a photo. */}
        {isVideo && (
          <span className="story-play" aria-label="Video story">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M8 5.5v13l11-6.5-11-6.5Z" />
            </svg>
          </span>
        )}
      </div>

      <div className="ig-body">
        <p className="ig-caption">
          <strong>{username}</strong> {story.caption?.trim() || <span className="muted">No caption</span>}
        </p>
        {ago && <p className="ig-meta ig-ago">{ago}</p>}

        <button className={automated ? 'secondary full' : 'ig-cta full'} onClick={onSetUp}>
          {automated ? 'View automation' : 'Set up automation'}
        </button>
      </div>
    </article>
  )
}

function relativeDay(iso: string | null): string {
  if (!iso) return ''
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return ''
  const hours = Math.floor((Date.now() - when.getTime()) / 3_600_000)
  // A story lives 24 hours, so hours are the useful unit — "TODAY" on all four
  // says nothing about which is about to expire.
  if (hours < 1) return 'JUST NOW'
  if (hours === 1) return '1 HOUR AGO'
  return `${hours} HOURS AGO`
}
