import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { listPosts, STARTER_TEMPLATES, TEMPLATE_GROUPS, type Post, type Rule } from '../api/rules'
import { RuleEditor } from '../components/rules/RuleEditor'

const BLURB: Record<string, string> = {
  Popular: 'The templates most creators start with.',
  'Sell & earn': 'Send links to products you sell or promote.',
  'Engage audience': 'Say thanks and start conversations.',
}

export function Templates() {
  const { data: posts } = useQuery({ queryKey: ['posts'], queryFn: listPosts, retry: false })
  const [chosen, setChosen] = useState<Partial<Rule> | null>(null)
  const [target, setTarget] = useState<Post | null>(null)

  return (
    <>
      <h1>Templates</h1>
      <p className="muted">
        Start from a proven setup, then change the keywords and message to suit you.
      </p>

      {TEMPLATE_GROUPS.map((group) => (
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
                onClick={() => setChosen(template.rule)}
              >
                <strong>{template.title}</strong>
                <span className="muted small">{template.blurb}</span>

                <span className="tpl-preview">
                  <span className="tpl-comment">
                    <span className="tpl-avatar" />
                    {template.example.comment}
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
              <h2>Which post?</h2>
              <button className="link" onClick={() => setChosen(null)} aria-label="Close">
                ×
              </button>
            </header>
            <div className="modal-body">
              {!posts?.length && (
                <p className="muted">
                  No posts synced yet. Go to Posts &amp; Reels and hit Refresh first.
                </p>
              )}
              <div className="template-grid">
                {posts?.map((post) => (
                  <button
                    key={post.id}
                    className="template-card"
                    onClick={() => setTarget(post)}
                  >
                    <span className="row-thumb" style={{ width: '100%', height: 90 }}>
                      {post.thumbnail_url && <img src={post.thumbnail_url} alt="" />}
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
