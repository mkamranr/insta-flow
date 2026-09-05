import { ChevronLeftIcon, GlobeIcon } from '../icons/Icons'
import { AccountAvatar } from '../common/AccountAvatar'
import type { Attachment, Rule } from '../../api/rules'

/**
 * How the automation will actually land for the person who commented.
 *
 * Rendered from the draft rather than from a saved rule, so it updates as the
 * form is typed into. Variables are filled with sample values — a preview
 * showing a literal `{{username}}` teaches you nothing about the real message.
 *
 * Deliberately not called "inbox". Instagram delivers a private reply to the
 * inbox only for people who already follow the account; everyone else gets it as
 * a message request they must accept first. A preview that implies otherwise is
 * how a working automation gets reported as broken.
 */

const SAMPLES: Record<string, string> = {
  username: 'mia.styles',
  comment: 'Need the GUIDE please!',
  post_caption: '5 AI tools every developer should know',
  link: 'https://example.com',
}

function fill(text: string): string {
  return text.replace(/\{\{(\w+)\}\}/g, (whole, name: string) => SAMPLES[name] ?? whole)
}

function prettyBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

function AttachmentBubble({
  attachment,
  avatarUrl,
  account,
}: {
  attachment: Attachment
  avatarUrl?: string | null
  account?: string
}) {
  const extension = attachment.filename.split('.').pop()?.toLowerCase() ?? ''
  const isImage = attachment.type === 'image'

  return (
    <div className="dm-row">
      <AccountAvatar className="dm-face" url={avatarUrl} name={account} size={22} />
      <div className={isImage ? 'dm-attach dm-attach-image' : 'dm-attach'}>
        {isImage ? (
          <img src={attachment.url} alt="" />
        ) : (
          <>
            <span className="dm-attach-icon" aria-hidden="true">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" />
                <path d="M14 2v6h6" />
              </svg>
            </span>
            <span className="dm-attach-meta">
              <span className="dm-attach-name">{attachment.filename}</span>
              <span className="dm-attach-size">
                {prettyBytes(attachment.size_bytes)} .{extension}
              </span>
            </span>
          </>
        )}
      </div>
    </div>
  )
}

export function DmPreview({
  draft,
  account,
  avatarUrl,
}: {
  draft: Partial<Rule>
  account?: string
  avatarUrl?: string | null
}) {
  const opening = draft.opening_dm
  const usesOpener = Boolean(opening?.enabled && opening.message?.trim())
  const buttons = draft.buttons ?? []
  const attachment = draft.attachment ?? null
  const delay = draft.reply_delay_seconds ?? 0

  return (
    <div className="dm-preview">
      <div className="dm-phone">
        <header className="dm-head">
          <span className="dm-back" aria-hidden="true">
            <ChevronLeftIcon />
          </span>
          <AccountAvatar className="dm-face" url={avatarUrl} name={account} size={22} />
          <strong>{account ?? 'your.account'}</strong>
        </header>

        <div className="dm-thread">
          {delay > 0 && (
            <p className="dm-note">
              Sent {Math.floor(delay / 60)}m {delay % 60}s after the comment
            </p>
          )}

          {usesOpener ? (
            <>
              <div className="dm-row">
                <AccountAvatar className="dm-face" url={avatarUrl} name={account} size={22} />
                <div className="dm-bubble">
                  <p>{fill(opening!.message)}</p>
                  {opening!.button_text?.trim() && (
                    <span className="dm-cta">{opening!.button_text}</span>
                  )}
                </div>
              </div>

              <div className="dm-row dm-row-them">
                <span className="dm-tap">{opening!.button_text || 'Send me the link'}</span>
              </div>
            </>
          ) : (
            <p className="dm-note">No opening DM — the message below is sent straight away.</p>
          )}

          {draft.message?.trim() ? (
            <div className="dm-row">
              <AccountAvatar className="dm-face" url={avatarUrl} name={account} size={22} />
              <div className="dm-bubble">
                <p>{fill(draft.message)}</p>
                {buttons.map((button) => (
                  <span className="dm-cta" key={button.url + button.title}>
                    <GlobeIcon />
                    {button.title || 'Open'}
                  </span>
                ))}
              </div>
            </div>
          ) : (
            <p className="dm-note">Write the message and it will appear here.</p>
          )}

          {attachment && (
            <AttachmentBubble
              attachment={attachment}
              avatarUrl={avatarUrl}
              account={account}
            />
          )}

          {attachment && !usesOpener && (
            <p className="dm-warn">
              A file can only be sent once a messaging window is open, and only the opening DM
              button opens one. Turn on the opening DM or this attachment will never arrive.
            </p>
          )}
        </div>

        <footer className="dm-foot">
          <span className="dm-camera" aria-hidden="true" />
          <span className="muted small">Message…</span>
        </footer>
      </div>

      {/* Stated for every rule, not just follow-gated ones: anyone who does not
          already follow gets this as a request. For a follow-gated rule that is
          everyone it targets, by definition. */}
      <p className="dm-note">
        People who already follow you see this in their inbox. Everyone else gets it as a{' '}
        <strong>message request</strong> they have to accept first — Instagram does not notify
        them the way it does for a normal message.
      </p>
    </div>
  )
}
