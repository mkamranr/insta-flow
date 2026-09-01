import type { Post } from '../../api/rules'

/** Instagram-style card: the post as it looks, with its automation state. */
export function PostCard({
  post,
  username,
  onSetUp,
}: {
  post: Post
  username: string
  onSetUp: () => void
}) {
  const automated = post.rule_count > 0
  const ago = post.published_at ? relativeDay(post.published_at) : ''

  return (
    <article className="ig-card">
      <header className="ig-head">
        <span className="ig-avatar">{username.slice(0, 1).toUpperCase()}</span>
        <span className="ig-user">{username}</span>
        <span className="ig-dots">···</span>
      </header>

      <div className="ig-media">
        {post.thumbnail_url ? (
          <img src={post.thumbnail_url} alt="" loading="lazy" />
        ) : (
          <div className="ig-media-empty">{post.media_product_type ?? 'POST'}</div>
        )}
      </div>

      <div className="ig-body">
        <div className="ig-actions" aria-hidden>
          <HeartIcon />
          {post.like_count != null && <span className="ig-count">{post.like_count}</span>}
          <CommentIcon />
          {post.comments_count != null && <span className="ig-count">{post.comments_count}</span>}
          <ShareIcon />
        </div>

        <p className="ig-caption">
          <strong>{username}</strong> {post.caption ?? ''}
        </p>

        {post.comments_count != null && post.comments_count > 0 && (
          <p className="ig-meta">View all {post.comments_count} comments</p>
        )}
        {ago && <p className="ig-meta ig-ago">{ago}</p>}

        <button className={automated ? 'secondary full' : 'ig-cta full'} onClick={onSetUp}>
          {automated ? 'View automation' : 'Set up automation'}
        </button>
      </div>
    </article>
  )
}

function relativeDay(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000)
  if (days <= 0) return 'TODAY'
  if (days === 1) return 'YESTERDAY'
  if (days < 7) return `${days} DAYS AGO`
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }).toUpperCase()
}

const icon = {
  width: 19,
  height: 19,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.7,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

const HeartIcon = () => (
  <svg {...icon}>
    <path d="M12 20s-7-4.5-7-9.5A3.9 3.9 0 0 1 12 7a3.9 3.9 0 0 1 7 3.5C19 15.5 12 20 12 20Z" />
  </svg>
)
const CommentIcon = () => (
  <svg {...icon}>
    <path d="M20 12a7.5 7.5 0 0 1-10.9 6.7L4 20l1.4-4.6A7.5 7.5 0 1 1 20 12Z" />
  </svg>
)
const ShareIcon = () => (
  <svg {...icon}>
    <path d="M21 3 10.5 13.5M21 3l-6.5 18-4-8-8-4L21 3Z" />
  </svg>
)
