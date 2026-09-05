import { CloseIcon } from '../icons/Icons'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { ApiError } from '../../api/client'
import { useQuery } from '@tanstack/react-query'
import { listAccounts } from '../../api/instagram'
import {
  captionLine,
  createRule,
  getIntents,
  mediaImage,
  patchRule,
  postKind,
  type Post,
  type Rule,
  type RuleTrigger,
} from '../../api/rules'
import { AttachmentField } from './AttachmentField'
import { DelayPicker } from './DelayPicker'
import { DmPreview } from './DmPreview'
import { KeywordInput } from './KeywordInput'
import { StoryPicker } from './StoryPicker'

/**
 * Mirrors `FOLLOW_PROMPT_WITH_WINDOW` / `FOLLOW_PROMPT_NO_WINDOW` in
 * `backend/app/models/rules.py`, which is the source of truth -- the worker
 * falls back to these when a rule carries no prompt of its own.
 *
 * Which one is true depends on whether a messaging window will be open when the
 * gate blocks. With an opening DM, tapping the button opens a 24-hour window and
 * the link is delivered automatically the moment the follow is detected, so
 * asking for another comment asks for nothing. Without one, the gate blocks at
 * the comment and the single private reply a comment allows has just been spent
 * on this very prompt -- there is no channel left, so commenting again really is
 * the only way back in.
 */
const FOLLOW_PROMPT_WITH_WINDOW =
  "Follow me and I'll send the link straight over — no need to comment again."
const FOLLOW_PROMPT_NO_WINDOW = 'Follow me first, then comment again and I\'ll send the link!'

/** Mirrors `EMAIL_PROMPT_DEFAULT` in `backend/app/models/rules.py`, which the
    worker falls back to when a rule carries no prompt of its own. */
const EMAIL_PROMPT_DEFAULT = "What's your email? I'll send it straight over."

const BLANK: Partial<Rule> = {
  name: '',
  scope: 'specific',
  keywords: [],
  any_word: false,
  match_mode: 'contains',
  case_sensitive: false,
  opening_dm: { enabled: false, message: '', button_text: 'Send me the link' },
  instagram_story_ids: [],
  expire_with_story: true,
  message: '',
  buttons: [],
  attachment: null,
  reply_delay_seconds: 0,
  public_reply: { enabled: false, message: '' },
  require_follow: { enabled: false, prompt: '' },
  collect_email: { enabled: false, prompt: '' },
  ai: { enabled: false, intents: [], min_confidence: null },
  once_per_person: true,
  enabled: true,
  priority: 100,
}

const TRIGGER_COPY: Record<RuleTrigger, { step1: string; anyWord: string }> = {
  comment: {
    step1: 'When someone comments',
    anyWord: 'Any word — reply to every comment on this post',
  },
  story_reply: {
    step1: 'When someone replies to your story',
    anyWord: 'Any word — reply to every story reply',
  },
  dm_keyword: {
    step1: 'When someone DMs you',
    // Refused server-side: it would answer every real question anyone sends.
    anyWord: 'Any word — not available for DMs',
  },
}

export function RuleEditor({
  post,
  trigger = 'comment',
  initial,
  editingId,
  saveAs,
  onClose,
}: {
  post?: Post
  trigger?: RuleTrigger
  initial?: Partial<Rule>
  /** When set, saving updates that rule instead of creating a new one. */
  editingId?: string
  /**
   * Where the finished rule goes, if not straight to `/api/rules`. The
   * upcoming-post page stores it as a template instead, and the editor should
   * not know which of those it is building.
   */
  saveAs?: (draft: Partial<Rule>) => Promise<void>
  onClose: () => void
}) {
  const [draft, setDraft] = useState<Partial<Rule>>({ ...BLANK, ...initial })
  const [dirty, setDirty] = useState(false)
  const queryClient = useQueryClient()
  const set = (changes: Partial<Rule>) => {
    setDirty(true)
    setDraft((d) => ({ ...d, ...changes }))
  }

  // The draft lives only in component state, so any full document load takes it
  // with it -- a session expiring, the workspace redirect in `WorkspaceGate`, or
  // simply a mistaken reload. None of those are navigations this app initiates,
  // so the browser's own prompt is the only thing that can interrupt them.
  // Registered only once something has actually been typed: prompting on an
  // untouched form would train people to dismiss the dialog without reading it.
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const save = useMutation({
    mutationFn: async (): Promise<void> => {
      // A story-reply or DM rule has no post to attach to, so it is account
      // wide -- "any" literally means "not restricted to the listed posts".
      const payload = post
        ? { ...draft, trigger, scope: 'specific' as const,
            instagram_media_ids: [post.instagram_media_id] }
        : { ...draft, trigger, scope: 'any' as const, instagram_media_ids: [] }
      // `id` is server-owned; sending it back would be rejected.
      delete (payload as Partial<Rule>).id
      if (saveAs) {
        await saveAs(payload)
        return
      }
      if (editingId) await patchRule(editingId, payload)
      else await createRule(payload)
    },
    onSuccess: () => {
      // Prefix matches, so the paged rails on Home and Posts refresh too --
      // without this the button stays on "Set up automation" until a reload.
      queryClient.invalidateQueries({ queryKey: ['posts'] })
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      queryClient.invalidateQueries({ queryKey: ['stories'] })
      onClose()
    },
  })

  const { data: intentInfo } = useQuery({ queryKey: ['intents'], queryFn: getIntents })
  // Cached by the dashboard already; this just reads it for the preview header.
  const { data: accounts } = useQuery({ queryKey: ['instagram-accounts'], queryFn: listAccounts })
  const buttons = draft.buttons ?? []
  const opening = draft.opening_dm!
  const ai = draft.ai!

  // Mirrors `uses_opening_dm` in `backend/app/workers/tasks.py`: an opener only
  // counts if it is enabled *and* has text, because an empty one sends nothing
  // and so opens no messaging window. The follow gate's wording and its real
  // behaviour both hang off this, so they must be derived from one expression
  // rather than described twice.
  const opensWithDm = Boolean(opening.enabled && opening.message.trim())
  const followPrompt = opensWithDm ? FOLLOW_PROMPT_WITH_WINDOW : FOLLOW_PROMPT_NO_WINDOW

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <div>
            <h2>{editingId ? 'Edit automation' : 'New automation'}</h2>
            <p className="muted small">{TRIGGER_COPY[trigger].step1}</p>
          </div>
          <button className="link" onClick={onClose} aria-label="Close">
            <CloseIcon />
          </button>
        </header>

        <div className="modal-body editor-split">
          <div className="editor-form">
          <section className="step">
            <div className="step-head"><span className="step-num">1</span><h3>{TRIGGER_COPY[trigger].step1}</h3></div>
            <label>Keywords</label>
            <KeywordInput keywords={draft.keywords ?? []} onChange={(k) => set({ keywords: k })} />

            <label className="inline">
              <input
                type="checkbox"
                disabled={trigger === 'dm_keyword'}
                checked={trigger === 'dm_keyword' ? false : (draft.any_word ?? false)}
                onChange={(e) => set({ any_word: e.target.checked })}
              />
              {TRIGGER_COPY[trigger].anyWord}
            </label>
            {trigger === 'dm_keyword' && (
              <p className="muted small">
                It would reply to every message anyone sends you, including real questions.
              </p>
            )}

            <label htmlFor="match">Match</label>
            <select
              id="match"
              value={draft.match_mode}
              onChange={(e) => set({ match_mode: e.target.value as Rule['match_mode'] })}
            >
              <option value="contains">Contains the keyword</option>
              <option value="word">Whole word only</option>
              <option value="exact">Exactly the keyword</option>
            </select>
          </section>

          {trigger === 'story_reply' && (
            <section className="step">
              <div className="step-head">
                <span className="step-num">2</span>
                <h3>On which stories</h3>
              </div>
              <StoryPicker
                selected={draft.instagram_story_ids ?? []}
                expireWithStory={draft.expire_with_story ?? true}
                onChange={(ids) => set({ instagram_story_ids: ids })}
                onExpireChange={(value) => set({ expire_with_story: value })}
              />
            </section>
          )}

          {trigger === 'comment' && (
          <section className="step">
            <div className="step-head"><span className="step-num">2</span><h3>They optionally get an opening DM</h3></div>
            <label className="inline">
              <input
                type="checkbox"
                checked={opening.enabled}
                onChange={(e) => set({ opening_dm: { ...opening, enabled: e.target.checked } })}
              />
              Send an opening message with a button first
            </label>
            {opening.enabled && (
              <>
                <textarea
                  rows={3}
                  maxLength={160}
                  value={opening.message}
                  placeholder="Hey! So happy you're here 😊 Tap below and I'll send the link."
                  onChange={(e) => set({ opening_dm: { ...opening, message: e.target.value } })}
                />
                <p className="muted small">
                  {opening.message.length}/160 — Instagram caps a message carrying a button at
                  160 characters, so the link follows once they tap.
                </p>
                <label htmlFor="btn">Button text</label>
                <input
                  id="btn"
                  maxLength={20}
                  value={opening.button_text}
                  onChange={(e) => set({ opening_dm: { ...opening, button_text: e.target.value } })}
                />
              </>
            )}
          </section>
          )}

          <section className="step">
            <div className="step-head"><span className="step-num">3</span><h3>And they get a DM with</h3></div>
            <textarea
              rows={4}
              maxLength={1000}
              value={draft.message}
              placeholder="Leave a star on the repo if you find it helpful. Cheers 😊"
              onChange={(e) => set({ message: e.target.value })}
            />
            <p className="muted small">{(draft.message ?? '').length}/1000 characters</p>

            <label>Link buttons</label>
            {buttons.map((button, index) => (
              <div className="row" key={index}>
                <input
                  placeholder="Button text"
                  maxLength={20}
                  value={button.title}
                  onChange={(e) => {
                    const next = [...buttons]
                    next[index] = { ...button, title: e.target.value }
                    set({ buttons: next })
                  }}
                />
                <input
                  placeholder="https://…"
                  value={button.url}
                  onChange={(e) => {
                    const next = [...buttons]
                    next[index] = { ...button, url: e.target.value }
                    set({ buttons: next })
                  }}
                />
                <button
                  type="button"
                  className="danger"
                  onClick={() => set({ buttons: buttons.filter((_, i) => i !== index) })}
                >
                  Remove
                </button>
              </div>
            ))}
            {buttons.length < 3 && (
              <button
                type="button"
                className="link"
                onClick={() => set({ buttons: [...buttons, { title: 'Open', url: 'https://' }] })}
              >
                + Add link
              </button>
            )}

            <label style={{ marginTop: 16 }}>File attachment</label>
            <AttachmentField
              value={draft.attachment ?? null}
              onChange={(attachment) => set({ attachment })}
            />
            {draft.attachment && !opening.enabled && (
              <p className="err small">
                A file needs an open messaging window, and only the opening DM button opens
                one. Turn on the opening DM in step 2 or this file will never be delivered.
              </p>
            )}

            <label style={{ marginTop: 16 }}>Reply time</label>
            <DelayPicker
              seconds={draft.reply_delay_seconds ?? 0}
              onChange={(reply_delay_seconds) => set({ reply_delay_seconds })}
            />

          </section>

          {intentInfo?.ai_enabled && (
            <section className="step">
              <div className="step-head"><span className="step-num">AI</span><h3>Match by meaning</h3></div>
              <p className="muted small">
                Only consulted when no keyword matched. Runs on {intentInfo.model} locally.
              </p>
              <label className="inline">
                <input
                  type="checkbox"
                  checked={ai.enabled}
                  onChange={(e) => set({ ai: { ...ai, enabled: e.target.checked } })}
                />
                Also match by what the comment means
              </label>
              {ai.enabled && (
                <>
                  <label>Intents that should trigger this automation</label>
                  <div className="chips">
                    {intentInfo.intents.map((intent) => {
                      const on = ai.intents.includes(intent)
                      return (
                        <button
                          type="button"
                          key={intent}
                          className={on ? 'chip chip-on' : 'chip'}
                          onClick={() =>
                            set({
                              ai: {
                                ...ai,
                                intents: on
                                  ? ai.intents.filter((i) => i !== intent)
                                  : [...ai.intents, intent],
                              },
                            })
                          }
                        >
                          {intent.replace(/_/g, ' ')}
                        </button>
                      )
                    })}
                  </div>
                </>
              )}
            </section>
          )}

          <section className="step">
            <h3>Delivery</h3>
            <label className="inline">
              <input
                type="checkbox"
                checked={draft.once_per_person ?? true}
                onChange={(e) => set({ once_per_person: e.target.checked })}
              />
              Only send this DM once per person
            </label>

            <label className="inline">
              <input
                type="checkbox"
                checked={draft.require_follow?.enabled ?? false}
                onChange={(e) =>
                  set({
                    require_follow: {
                      enabled: e.target.checked,
                      prompt: draft.require_follow?.prompt || followPrompt,
                    },
                  })
                }
              />
              Require them to follow you first
            </label>
            {draft.require_follow?.enabled && (
              <>
                <p className="muted small">
                  {opensWithDm
                    ? 'They still get the opening DM — Instagram won’t tell us who follows you until someone messages you. The link is held back until they tap the button, and sent automatically as soon as they follow, for up to 23 hours. They don’t need to comment or tap again.'
                    : 'Checked at the moment they comment. Without an opening DM there’s no way to reach them afterwards, so they will have to comment again once they follow. Turn on the opening DM above to deliver automatically instead.'}
                </p>
                <input
                  value={draft.require_follow.prompt}
                  maxLength={300}
                  placeholder={followPrompt}
                  onChange={(e) =>
                    set({ require_follow: { ...draft.require_follow!, prompt: e.target.value } })
                  }
                />
              </>
            )}

            <label className="inline">
              <input
                type="checkbox"
                checked={draft.public_reply?.enabled ?? false}
                onChange={(e) =>
                  set({
                    public_reply: {
                      enabled: e.target.checked,
                      message: draft.public_reply?.message || 'Just sent you a DM! 📩',
                    },
                  })
                }
              />
              Also reply publicly under the comment
            </label>
            <p className="muted small">
              {draft.public_reply?.enabled
                ? 'A visible reply is posted under their comment, as well as the DM.'
                : 'Off — nothing is posted under their comment. Only the DM is sent.'}
            </p>
            {draft.public_reply?.enabled && (
              <input
                value={draft.public_reply.message}
                maxLength={300}
                onChange={(e) =>
                  set({ public_reply: { ...draft.public_reply!, message: e.target.value } })
                }
              />
            )}

            <label className="inline">
              <input
                type="checkbox"
                checked={draft.collect_email?.enabled ?? false}
                onChange={(e) =>
                  set({
                    collect_email: {
                      enabled: e.target.checked,
                      prompt: draft.collect_email?.prompt || EMAIL_PROMPT_DEFAULT,
                    },
                  })
                }
              />
              Ask for their email first
            </label>
            {draft.collect_email?.enabled && (
              <>
                {/* Reads off `opensWithDm`, the same expression the follow gate's
                    wording uses. The dependency is real: the prompt is sent from
                    the postback handler, so with no button there is nothing to
                    ask on, and the server refuses to save that combination. */}
                <p className={opensWithDm ? 'muted small' : 'err small'}>
                  {opensWithDm
                    ? 'Asked after they tap the button. The link is held until they reply with an address, which is then saved to Contacts.'
                    : 'This needs the opening DM in step 2 — the email is asked for when they tap its button, and there is no button without it.'}
                </p>
                <input
                  value={draft.collect_email.prompt}
                  maxLength={300}
                  placeholder={EMAIL_PROMPT_DEFAULT}
                  onChange={(e) =>
                    set({ collect_email: { ...draft.collect_email!, prompt: e.target.value } })
                  }
                />
              </>
            )}
          </section>

          {save.isError && <p className="err">{(save.error as ApiError).message}</p>}
          </div>

          <aside className="editor-preview">
            {/* Which post this is for. The editor used to say so in one line of
                header text with no image, which is not enough to recognise a
                post by -- and on a story-reply or DM rule there is no post at
                all, so the card is skipped rather than faked. */}
            {post && (
              <div className="editor-post">
                <span className="row-thumb">
                  {mediaImage(post) ? (
                    <img src={mediaImage(post)!} alt="" loading="lazy" />
                  ) : (
                    <span className="row-thumb-empty" />
                  )}
                </span>
                <div style={{ minWidth: 0 }}>
                  <div className="small editor-post-kind">
                    On this {postKind(post).toLowerCase()}
                  </div>
                  <div className="muted small row-caption">
                    {captionLine(post.caption, 60) || post.instagram_media_id}
                  </div>
                  {post.permalink && (
                    /* An anchor, not a button: the global `button` rule is
                       `white-space: nowrap`. `_blank` also keeps the unsaved
                       draft alive -- this never unloads the page. */
                    <a
                      className="small"
                      href={post.permalink}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      View on Instagram
                    </a>
                  )}
                </div>
              </div>
            )}

            <h3 className="small">Preview</h3>
            <p className="muted small">How this lands in their inbox.</p>
            <DmPreview
              draft={draft}
              account={accounts?.[0]?.username}
              avatarUrl={accounts?.[0]?.profile_picture_url}
            />
          </aside>
        </div>

        <footer className="modal-foot">
          <button className="link" onClick={onClose}>
            Cancel
          </button>
          <button
            className="cta"
            onClick={() => save.mutate()}
            disabled={save.isPending || !draft.message?.trim()}
          >
            {save.isPending ? 'Saving…' : editingId ? 'Save changes' : 'Create automation'}
          </button>
        </footer>
      </div>
    </div>
  )
}
