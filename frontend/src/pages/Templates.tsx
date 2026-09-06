import { CloseIcon } from '../components/icons/Icons'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { listPosts, mediaImage, STARTER_TEMPLATES, TEMPLATE_GROUPS, type Post, type Rule } from '../api/rules'
import { Avatar } from '../components/common/Avatar'
import { RuleEditor } from '../components/rules/RuleEditor'

const BLURB: Record<string, string> = {
  Popular: 'The templates most creators start with.',
  'Sell & earn': 'Send links to products you sell or promote.',
  'Engage audience': 'Say thanks and start conversations.',
  'Capture leads': 'Collect emails and share free guides.',
  'Book clients': 'Send booking links and portfolios.',
}

export function Templates() {
  const { data: posts } = useQuery({ queryKey: ['posts'], queryFn: () => listPosts(), retry: false })
  const [chosen, setChosen] = useState<Partial<Rule> | null>(null)
  const [target, setTarget] = useState<Post | null>(null)
  const [filter, setFilter] = useState<string>('All')

  const groups = TEMPLATE_GROUPS.filter((g) => filter === 'All' || g === filter)

  // Only posts without an automation. A template starts a new one, and Posts
  // already takes this line -- `rule_count > 0` sends you to Automations there
  // rather than into the template flow. Adding a second automation to a post is
  // a different job, done from Automations or by cloning one across posts.
  const free = posts?.filter((post) => post.rule_count === 0) ?? []

  return (
    <>
      <h1>Automation templates</h1>
      <p className="muted">
        Pre-filled automations to get you started. Pick one, customise the message and link,
        then go live.
      </p>

      <div className="filters" style={{ marginTop: 18 }}>
        {['All', ...TEMPLATE_GROUPS].map((name) => (
          <button
            key={name}
            className={filter === name ? 'filter active' : 'filter'}
            onClick={() => setFilter(name)}
          >
            {name}
          </button>
        ))}
      </div>

      {groups.map((group) => (
        <section className="section" key={group}>
          <h2>{group}</h2>
          <p className="muted small" style={{ marginBottom: 14 }}>
            {BLURB[group]}
          </p>
          <div className="template-grid">
            {STARTER_TEMPLATES.filter((t) => t.group === group).map((template) => (
              <button
                key={template.id}
                className="template-card"
                onClick={() => setChosen({ ...template.rule, template: template.id })}
              >
                <strong>{template.title}</strong>
                <span className="muted small tpl-blurb">{template.blurb}</span>

                <span className="tpl-preview">
                  <span className="tpl-comment">
                    <Avatar name={template.example.username} size={26} />
                    <span className="tpl-comment-text">
                      <span className="tpl-handle">{template.example.username}</span>
                      <span>{template.example.comment}</span>
                    </span>
                  </span>
                  <span className="tpl-reply">
                    {template.example.reply}
                    {template.example.button && (
                      <span className="tpl-button">{template.example.button}</span>
                    )}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </section>
      ))}

      {chosen && !target && (
        <div className="modal-backdrop" onClick={() => setChosen(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-head">
              <div>
                <h2>Which post?</h2>
                <p className="muted small">Posts that don’t have an automation yet.</p>
              </div>
              <button className="link" onClick={() => setChosen(null)} aria-label="Close">
                <CloseIcon />
              </button>
            </header>
            <div className="modal-body">
              {!posts?.length ? (
                <p className="muted">
                  No posts synced yet. Go to Posts &amp; Reels and hit Refresh first.
                </p>
              ) : (
                free.length === 0 && (
                  <div className="empty-state">
                    <p className="empty-title">Every synced post already has an automation</p>
                    <p className="muted small">
                      Edit one on <Link to="/automations">Automations</Link>, or copy an existing
                      automation onto more posts from there.
                    </p>
                  </div>
                )
              )}
              <div className="template-grid">
                {free.map((post) => (
                  <button
                    key={post.id}
                    className="template-card"
                    onClick={() => setTarget(post)}
                  >
                    <span className="row-thumb" style={{ width: '100%', height: 90 }}>
                      {mediaImage(post) && <img src={mediaImage(post)!} alt="" />}
                    </span>
                    <span className="small">{post.caption?.slice(0, 60) ?? '(no caption)'}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {chosen && target && (
        <RuleEditor
          post={target}
          initial={chosen}
          onClose={() => {
            setChosen(null)
            setTarget(null)
          }}
        />
      )}
    </>
  )
}
