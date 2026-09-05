import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { listAccounts, type InstagramAccount } from '../api/instagram'
import {
  actOnBacklog,
  getPauseStatus,
  pauseAccount,
  resumeAccount,
  type BacklogChoice,
  type PauseStatus,
} from '../api/pause'
import { CloseIcon } from '../components/icons/Icons'
import { Switch } from '../components/common/Switch'

type PresetId = '30m' | '1h' | '3h' | '12h' | '24h' | '7d'

const PRESETS: { id: PresetId; label: string; minutes: number }[] = [
  { id: '30m', label: '30 minutes', minutes: 30 },
  { id: '1h', label: '1 hour', minutes: 60 },
  { id: '3h', label: '3 hours', minutes: 180 },
  { id: '12h', label: '12 hours', minutes: 720 },
  { id: '24h', label: '24 hours', minutes: 1440 },
  { id: '7d', label: '7 days', minutes: 10080 },
]

/** A pause ending inside the next minute is a pause that never happened. */
const MIN_AHEAD = 60_000
/** Matches MAX_PAUSE_DAYS on the server, which re-checks it. Without a ceiling
    a mistyped year would silence the account until then. */
const MAX_AHEAD = 30 * 24 * 60 * 60_000

interface Draft {
  on: boolean
  choice: PresetId | 'custom'
  /** Exactly as <input type="date"|"time"> hand them over. */
  date: string
  time: string
}

const BLANK: Draft = { on: true, choice: '1h', date: '', time: '' }

/**
 * Local wall clock to an instant.
 *
 * `new Date('2026-09-08T09:00')` carries no offset, and an offsetless date-TIME
 * is parsed as local (only a bare date is UTC). So the operator types the time
 * on their own clock, `toISOString()` at the call site turns it into the UTC
 * instant the API wants, and nothing here does timezone arithmetic.
 */
function customInstant(date: string, time: string): number | null {
  if (!date || !time) return null
  const ms = new Date(`${date}T${time}`).getTime()
  return Number.isNaN(ms) ? null : ms
}

/** Presets are measured from `from` -- the moment Save is pressed, not the
    moment the chip was picked, or reading the notes would eat the pause. */
function resumeAt(draft: Draft, from: number): number | null {
  if (draft.choice === 'custom') return customInstant(draft.date, draft.time)
  const preset = PRESETS.find((p) => p.id === draft.choice)
  return preset ? from + preset.minutes * 60_000 : null
}

function problemWith(at: number | null, now: number): string | null {
  if (at === null) return 'Choose the date and time Auto DM should resume.'
  if (at < now + MIN_AHEAD) return 'That time has already passed. Pick a time in the future.'
  if (at > now + MAX_AHEAD) return 'A pause can last at most 30 days.'
  return null
}

/** 2d 04:13:07 / 04:13:07 / 13:07 -- never a leading unit that is always zero. */
function formatLeft(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const pad = (n: number) => String(n).padStart(2, '0')
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  if (days > 0) return `${days}d ${pad(hours)}:${pad(minutes)}:${pad(seconds)}`
  if (hours > 0) return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`
  return `${pad(minutes)}:${pad(seconds)}`
}

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

function Breadcrumb() {
  return (
    <nav className="crumbs" aria-label="Breadcrumb">
      <Link to="/">Home</Link>
      <span className="crumb-sep" aria-hidden="true">
        ›
      </span>
      <span aria-current="page">Auto DM pause</span>
    </nav>
  )
}

/**
 * The clock, and nothing else.
 *
 * Its own component so the once-a-second setState repaints eleven characters
 * rather than the card, the picker and every sibling account. On reaching zero
 * it decides nothing: it invalidates and lets the server say what is waiting.
 *
 * `until` is epoch ms rather than a Date so the dependency array compares by
 * value instead of identity.
 */
function Countdown({ until, accountId }: { until: number; accountId: string }) {
  const queryClient = useQueryClient()
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const id = window.setInterval(() => {
      const tick = Date.now()
      setNow(tick)
      if (tick >= until) {
        window.clearInterval(id)
        queryClient.invalidateQueries({ queryKey: ['pause', accountId] })
        queryClient.invalidateQueries({ queryKey: ['instagram-accounts'] })
      }
    }, 1000)
    return () => window.clearInterval(id)
  }, [until, accountId, queryClient])

  // No aria-live: a per-second live region would read the clock aloud sixty
  // times a minute. "Resumes <time>" sits beside it as the same fact in a form
  // a screen reader can actually use.
  return <div className="pause-clock">{formatLeft(until - now)}</div>
}

function PausePanel({
  account,
  status,
  paused,
}: {
  account: InstagramAccount
  status?: PauseStatus
  paused: boolean
}) {
  const held = status?.held ?? 0

  if (!paused) {
    return (
      <div className="pause-panel">
        <span className="pause-mark running" aria-hidden="true">
          <PanelGlyph running />
        </span>
        <p className="pause-state">Auto DM is running</p>
        <p className="muted small pause-sub">
          Every matching comment gets its DM. Switch the pause on to hold them.
        </p>
        {held > 0 && (
          <p className="pause-skipped warn-text small">
            {plural(held, 'comment')} from the last pause {held === 1 ? 'is' : 'are'} still
            waiting on a decision.
          </p>
        )}
      </div>
    )
  }

  const until = account.automation_paused_until
  return (
    <div className="pause-panel">
      <span className="pause-mark" aria-hidden="true">
        <PanelGlyph />
      </span>
      <span className="pill paused">Paused</span>
      {until && <Countdown until={Date.parse(until)} accountId={account.id} />}
      {until && <p className="muted small pause-sub">Resumes {when(until)}</p>}
      <p className="pause-skipped small">
        <strong>{held}</strong> comment{held === 1 ? '' : 's'} held so far
      </p>
    </div>
  )
}

/** Bigger than a sidebar icon, so it does not share the nav glyph sizing. */
function PanelGlyph({ running }: { running?: boolean }) {
  return (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.9}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {running ? <path d="M8 5.5v13l11-6.5z" /> : <path d="M9.5 6v12M14.5 6v12" />}
    </svg>
  )
}

function DurationPicker({
  value,
  onChange,
  preview,
}: {
  value: Draft
  onChange: (patch: Partial<Draft>) => void
  preview: string | null
}) {
  const today = new Date().toISOString().slice(0, 10)

  return (
    <div className="pause-picker">
      <p className="pause-picker-label">Pause for</p>

      {/* .chips / .chip / .chip-on already exist and already have an
          accent-filled selected state. role=radio on plain buttons with no
          arrow handling matches ThemeToggle. */}
      <div className="chips" role="radiogroup" aria-label="Pause duration">
        {PRESETS.map((preset) => (
          <button
            key={preset.id}
            type="button"
            role="radio"
            aria-checked={value.choice === preset.id}
            className={value.choice === preset.id ? 'chip chip-on' : 'chip'}
            onClick={() => onChange({ choice: preset.id })}
          >
            {preset.label}
          </button>
        ))}
        <button
          type="button"
          role="radio"
          aria-checked={value.choice === 'custom'}
          className={value.choice === 'custom' ? 'chip chip-on' : 'chip'}
          onClick={() => onChange({ choice: 'custom' })}
        >
          Resume at…
        </button>
      </div>

      {value.choice === 'custom' && (
        <div className="pause-custom">
          <input
            type="date"
            aria-label="Resume date"
            min={today}
            value={value.date}
            onChange={(e) => onChange({ date: e.target.value })}
          />
          <input
            type="time"
            aria-label="Resume time"
            value={value.time}
            onChange={(e) => onChange({ time: e.target.value })}
          />
          <span className="muted small">your local time</span>
        </div>
      )}

      {preview && <p className="muted small pause-preview">{preview}</p>}
    </div>
  )
}

/**
 * `mode` changes only the wording. Both paths settle the same backlog: whether
 * the pause is ending now or ended by itself an hour ago, the pause is over and
 * the question owed is the same one.
 */
function BacklogDialog({
  status,
  username,
  mode,
  pending,
  error,
  onChoose,
  onClose,
}: {
  status: PauseStatus
  username: string
  mode: 'resuming' | 'expired'
  pending: boolean
  error: string | null
  onChoose: (choice: BacklogChoice) => void
  onClose: () => void
}) {
  const undeliverable = status.held - status.deliverable

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="backlog-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <div>
            <h2 id="backlog-title">{plural(status.held, 'comment')} arrived while paused</h2>
            <p className="muted small">
              {mode === 'expired'
                ? `The pause on @${username} has ended. Nothing has been sent yet.`
                : `Auto DM for @${username} has resumed.`}
            </p>
          </div>
          <button className="link" onClick={onClose} aria-label="Close">
            <CloseIcon />
          </button>
        </header>

        <div className="modal-body">
          <div className="backlog-choices">
            <button
              className="backlog-choice"
              disabled={pending || status.deliverable === 0}
              onClick={() => onChoose('deliver')}
            >
              <strong>
                {status.deliverable === 0
                  ? 'Nothing can be sent'
                  : `Send ${plural(status.deliverable, 'DM')} now`}
              </strong>
              <span className="muted small">
                {status.deliverable === 0
                  ? 'Instagram only accepts a reply within 7 days of the comment, and all of these are older than that.'
                  : 'Queued at the usual rate, one at a time. Each one still goes through your rules and limits.'}
              </span>
            </button>
            <button
              className="backlog-choice"
              disabled={pending}
              onClick={() => onChoose('ignore')}
            >
              <strong>Ignore them</strong>
              <span className="muted small">
                Nothing is sent. The comments stay unanswered and the count clears.
              </span>
            </button>
          </div>

          {undeliverable > 0 && status.deliverable > 0 && (
            <p className="muted small">
              {plural(undeliverable, 'comment')} can no longer be answered — Instagram only
              accepts a reply within 7 days.
            </p>
          )}
          {error && (
            <p className="err small" role="alert">
              {error}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function PauseCard({ account, sole }: { account: InstagramAccount; sole: boolean }) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<Draft | null>(null)
  const [saved, setSaved] = useState(false)
  const [asking, setAsking] = useState(false) // they toggled off
  const [acked, setAcked] = useState(false) // they dismissed the standing question

  const { data: status } = useQuery({
    queryKey: ['pause', account.id],
    queryFn: () => getPauseStatus(account.id),
    refetchInterval: 30_000,
  })

  // A `paused_until` in the past is not a pause: nothing runs at expiry, so a
  // lapsed pause still carries its old instant.
  const until = account.automation_paused_until
  const paused = until !== null && Date.parse(until) > Date.now()

  const current = draft ?? { ...BLANK, on: paused }

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['instagram-accounts'] })
    queryClient.invalidateQueries({ queryKey: ['pause', account.id] })
  }

  const pause = useMutation({
    mutationFn: () => {
      const at = resumeAt(current, Date.now()) // measured at click, not at render
      if (at === null) return Promise.reject(new Error('Choose when Auto DM should resume.'))
      return pauseAccount(account.id, new Date(at).toISOString())
    },
    onSuccess: () => {
      setDraft(null)
      setSaved(true)
      invalidate()
    },
  })

  const resume = useMutation({
    mutationFn: () => resumeAccount(account.id),
    onSuccess: () => {
      setDraft(null)
      setSaved(false)
      invalidate()
    },
  })

  const settle = useMutation({
    mutationFn: (choice: BacklogChoice) => actOnBacklog(account.id, choice),
    onSuccess: () => {
      setAsking(false)
      setAcked(true)
      invalidate()
    },
  })

  const busy = pause.isPending || resume.isPending || settle.isPending

  const toggle = async (next: boolean) => {
    setSaved(false)
    if (next) {
      setDraft({ ...BLANK, on: true })
      return
    }
    if (!paused) {
      setDraft(null) // armed but never committed: nothing to undo on the server
      return
    }
    await resume.mutateAsync()
    if ((status?.held ?? 0) > 0) setAsking(true)
  }

  const showPicker = current.on && (!paused || draft !== null)
  const previewAt = showPicker ? resumeAt(current, Date.now()) : null
  const problem = showPicker && draft !== null ? problemWith(previewAt, Date.now()) : null
  const canSave = showPicker && draft !== null && problem === null && !busy

  // Derived rather than seeded by an effect, which is also what stops the
  // 30-second refetch resurrecting the dialog after it is dismissed.
  const owed = !paused && (status?.held ?? 0) > 0 && !acked
  const labelId = `pause-label-${account.id}`

  return (
    <div className="card">
      <div className="pause-row">
        <span id={labelId} className="pause-row-label">
          {sole ? 'Auto DM pause' : `@${account.username}`}
        </span>
        <Switch checked={current.on} onChange={toggle} labelledBy={labelId} disabled={busy} />
      </div>

      <hr className="card-divider" />

      <PausePanel account={account} status={status} paused={paused} />

      {paused && draft === null && (
        <div className="pause-save">
          <button className="link" onClick={() => setDraft({ ...BLANK, on: true })}>
            Change the pause length
          </button>
        </div>
      )}

      {showPicker && (
        <DurationPicker
          value={current}
          onChange={(patch) => {
            setSaved(false)
            setDraft({ ...current, ...patch })
          }}
          preview={
            previewAt !== null && !problem
              ? `Auto DM resumes ${when(new Date(previewAt).toISOString())}.`
              : null
          }
        />
      )}

      <p className="pause-notes muted small">
        <em>
          Notes: This feature temporarily pauses Auto DM for the selected account. Choose a pause
          duration or cancel an active pause anytime. Disabling the toggle will resume Auto DM
          immediately.
        </em>
      </p>

      <div className="pause-save">
        <button className="cta" disabled={!canSave} onClick={() => pause.mutate()}>
          {pause.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>

      {problem && (
        <p className="err small pause-feedback" role="alert">
          {problem}
        </p>
      )}
      {pause.error && (
        <p className="err small pause-feedback" role="alert">
          {(pause.error as Error).message}
        </p>
      )}
      {resume.error && (
        <p className="err small pause-feedback" role="alert">
          {(resume.error as Error).message}
        </p>
      )}
      {saved && !pause.error && <p className="ok small pause-feedback">Saved.</p>}

      {(asking || owed) && status && (
        <BacklogDialog
          status={status}
          username={account.username}
          mode={asking ? 'resuming' : 'expired'}
          pending={settle.isPending}
          error={settle.error ? (settle.error as Error).message : null}
          onChoose={(choice) => settle.mutate(choice)}
          onClose={() => {
            setAsking(false)
            setAcked(true)
          }}
        />
      )}
    </div>
  )
}

export function AutoDmPause() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['instagram-accounts'],
    queryFn: listAccounts,
    // Coming back to the tab after lunch should not show a countdown that
    // stopped when the laptop slept.
    refetchOnWindowFocus: true,
  })

  const accounts = (data ?? []).filter((account) => account.status !== 'disconnected')

  return (
    <>
      <Breadcrumb />
      <h1>Auto DM pause</h1>

      {error && <div className="card err">{(error as Error).message}</div>}
      {isLoading && <p className="muted">Loading…</p>}

      {accounts.map((account) => (
        <PauseCard key={account.id} account={account} sole={accounts.length === 1} />
      ))}

      {data !== undefined && accounts.length === 0 && (
        <div className="card">
          <div className="empty-state">
            <p className="empty-title">No Instagram account connected</p>
            <p className="muted small">
              There is nothing to pause yet. Connect an account on{' '}
              <Link to="/instagram" className="text-link">
                Instagram
              </Link>{' '}
              first.
            </p>
          </div>
        </div>
      )}
    </>
  )
}
