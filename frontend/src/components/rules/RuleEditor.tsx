import { CloseIcon } from '../icons/Icons'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import { useQuery } from '@tanstack/react-query'
import { listAccounts } from '../../api/instagram'
import {
  createRule,
  getIntents,
  patchRule,
  type Post,
  type Rule,
  type RuleTrigger,
} from '../../api/rules'
import { AttachmentField } from './AttachmentField'
import { DelayPicker } from './DelayPicker'
import { DmPreview } from './DmPreview'
import { KeywordInput } from './KeywordInput'
import { StoryPicker } from './StoryPicker'

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
  const queryClient = useQueryClient()
  const set = (changes: Partial<Rule>) => setDraft((d) => ({ ...d, ...changes }))

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

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <div>
            <h2>{editingId ? 'Edit automation' : 'New automation'}</h2>
            <p className="muted small">
              {post ? post.caption?.slice(0, 70) || post.instagram_media_id : TRIGGER_COPY[trigger].step1}
            </p>
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
            <h3>Advanced</h3>
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
                      prompt:
                        draft.require_follow?.prompt || 'Follow me first, then comment again!',
                    },
                  })
                }
              />
              Require them to follow you first
            </label>
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
            {draft.public_reply?.enabled && (
              <input
                value={draft.public_reply.message}
                maxLength={300}
                onChange={(e) =>
                  set({ public_reply: { ...draft.public_reply!, message: e.target.value } })
                }
              />
            )}
          </section>

          {save.isError && <p className="err">{(save.error as ApiError).message}</p>}
          </div>

          <aside className="editor-preview">
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
