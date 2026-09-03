import { useNavigate } from 'react-router-dom'
import { CloseIcon } from '../icons/Icons'
import type { RuleTrigger } from '../../api/rules'

/**
 * Pick what starts an automation.
 *
 * All three options are real. A comment rule needs a post, so that route goes
 * to Posts & Reels to choose one; the other two are account-wide and open the
 * editor directly, because a story reply and an inbound DM are not attached to
 * a post.
 */

const OPTIONS: {
  trigger: RuleTrigger
  kicker: string
  title: string
  blurb: string
  tint: string
}[] = [
  {
    trigger: 'comment',
    kicker: 'Comments',
    title: 'DM on post or reel',
    blurb: 'When someone comments a keyword',
    tint: 'tint-lime',
  },
  {
    trigger: 'story_reply',
    kicker: 'Stories',
    title: 'DM on story',
    blurb: 'When someone replies to your story',
    tint: 'tint-blue',
  },
  {
    trigger: 'dm_keyword',
    kicker: 'Keywords',
    title: 'DM on keyword',
    blurb: 'When someone DMs you a keyword',
    tint: 'tint-pink',
  },
]

function Sketch({ tint }: { tint: string }) {
  return (
    <span className={`start-sketch ${tint}`} aria-hidden="true">
      <span className="sketch-card">
        <span className="sketch-bar wide" />
        <span className="sketch-line" />
        <span className="sketch-line short" />
      </span>
    </span>
  )
}

export function NewAutomationDialog({
  onClose,
  onPick,
}: {
  onClose: () => void
  /** Called for the two account-wide triggers; comment routes to Posts. */
  onPick: (trigger: RuleTrigger) => void
}) {
  const navigate = useNavigate()

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal start-modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <div>
            <h2>Start a new automation</h2>
            <p className="muted small">Choose what triggers it, or pick a ready-made template.</p>
          </div>
          <button className="link" onClick={onClose} aria-label="Close">
            <CloseIcon />
          </button>
        </header>

        <div className="modal-body">
          <div className="start-grid">
            {OPTIONS.map((option) => (
              <button
                key={option.trigger}
                className="start-card"
                onClick={() =>
                  option.trigger === 'comment' ? navigate('/posts') : onPick(option.trigger)
                }
              >
                <Sketch tint={option.tint} />
                <span className="start-kicker">{option.kicker}</span>
                <strong className="start-title">{option.title}</strong>
                <span className="muted small">{option.blurb}</span>
                <span className="start-go">
                  Start here <span aria-hidden="true">→</span>
                </span>
              </button>
            ))}
          </div>
        </div>

        <footer className="modal-foot start-foot">
          <button className="link" onClick={() => navigate('/templates')}>
            Browse templates <span aria-hidden="true">→</span>
          </button>
        </footer>
      </div>
    </div>
  )
}
