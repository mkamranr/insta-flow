import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getContacts, type ContactGroup } from '../api/analytics'
import { captionLine, mediaImage, postKind } from '../api/rules'
import { basename } from '../app/workspace'
import { InviteDialog } from '../components/admin/InviteDialog'
import { usePermissions } from '../hooks/usePermissions'
import { MediaThumb } from '../components/common/MediaThumb'

/**
 * The download is a plain anchor, not a fetch.
 *
 * `apiGet` parses every response as JSON, and a Blob round-trip would buy
 * nothing here: the session cookie is same-origin, so a direct navigation
 * authenticates itself, and `Content-Disposition` already names the file. The
 * accepted cost is that a 403 lands on an error page instead of a toast — the
 * page behind `contacts.view` has to have failed first for that to happen.
 *
 * `basename` is not optional. Without it a workspace at `/u/<slug>/` downloads
 * the bare install's contacts.
 */
function csvUrl(post?: string | null): string {
  const query = post === undefined ? '' : `?post=${encodeURIComponent(post ?? 'none')}`
  return `${basename}/api/analytics/contacts.csv${query}`
}

function DownloadCsv({ post, label }: { post?: string | null; label: string }) {
  return (
    <a href={csvUrl(post)} download>
      <button className="secondary">{label}</button>
    </a>
  )
}

/** Identifies a group in the open/closed state. A media id of null is a real
 *  group -- the one with no post -- so it needs a key of its own. */
function groupKey(group?: ContactGroup): string | null {
  if (!group) return null
  return group.instagram_media_id ?? '__no_post__'
}

/** How to name a group whose post we cannot show. */
function groupTitle(group: ContactGroup): string {
  if (group.post) return captionLine(group.post.caption, 70) || postKind(group.post)
  if (group.instagram_media_id) return `Post ${group.instagram_media_id}`
  return 'Not from a post'
}

function GroupCard({
  group,
  open,
  onToggle,
  selectable,
  selected,
  onToggleEmail,
  onSelectGroup,
  onClearGroup,
}: {
  group: ContactGroup
  open: boolean
  onToggle: () => void
  selectable: boolean
  selected: Set<string>
  onToggleEmail: (email: string) => void
  onSelectGroup: (emails: string[]) => void
  onClearGroup: (emails: string[]) => void
}) {
  const thumbnail = group.post ? mediaImage(group.post) : null
  const placed = group.instagram_media_id !== null

  return (
    <div className="auto-card">
      <button className="auto-head" onClick={onToggle} aria-expanded={open}>
        <MediaThumb
          className="auto-thumb"
          src={thumbnail ?? null}
          empty={<div className="auto-thumb auto-thumb-empty" aria-hidden="true" />}
        />

        <div className="auto-id">
          <div className="auto-name">
            {groupTitle(group)}
            <span className="pill">
              {group.count} contact{group.count === 1 ? '' : 's'}
            </span>
          </div>
          <div className="muted small">
            {group.post
              ? postKind(group.post)
              : placed
                ? 'This post has not been synced'
                : 'Story replies, DM keywords, and leads we could not trace to a post'}
          </div>
        </div>

        <span className={open ? 'chev open' : 'chev'} aria-hidden="true">
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M6 9l6 6 6-6" />
          </svg>
        </span>
      </button>

      {open && (
        <div className="auto-body">
          {/* The download lives here, not in the header: an anchor nested inside
              the disclosure button is invalid markup, and its click would bubble
              up and collapse the panel it was meant to export. */}
          <div className="auto-body-head">
            <strong className="small">
              {group.count} contact{group.count === 1 ? '' : 's'}
            </strong>
            <DownloadCsv post={group.instagram_media_id} label="Download CSV" />
          </div>

          {selectable && (
            <div className="row">
              <button
                className="link"
                onClick={() => onSelectGroup(group.contacts.map((c) => c.email))}
              >
                Select all {group.count}
              </button>
              <button
                className="link"
                onClick={() => onClearGroup(group.contacts.map((c) => c.email))}
              >
                Clear
              </button>
            </div>
          )}

          <table className="table">
            <thead>
              <tr>
                {selectable && <th className="select-cell" />}
                <th>Email</th>
                <th>Instagram ID</th>
                <th>Captured</th>
                <th>Automation</th>
              </tr>
            </thead>
            <tbody>
              {group.contacts.map((contact) => (
                <tr key={`${contact.id}-${contact.rule_name}`}>
                  {selectable && (
                    <td className="select-cell">
                      {contact.invite_state === 'accepted' ? (
                        // Not a disabled checkbox: "already has an account" is
                        // a different claim from "not selected", and a checkbox
                        // that silently ignores a click is the worst thing a
                        // checkbox can do.
                        <span className="muted" aria-hidden="true">—</span>
                      ) : (
                        <label className="pick">
                          <input
                            type="checkbox"
                            aria-label={`Select ${contact.email}`}
                            checked={selected.has(contact.email.toLowerCase())}
                            onChange={() => onToggleEmail(contact.email)}
                          />
                        </label>
                      )}
                    </td>
                  )}
                  <td>
                    {contact.email}
                    {contact.invite_state === 'invited' && (
                      <span className="pill paused" title="An invitation is outstanding.">
                        Invited
                      </span>
                    )}
                    {contact.invite_state === 'accepted' && (
                      <span className="pill on">Has an account</span>
                    )}
                  </td>
                  <td className="muted small">{contact.instagram_user_id}</td>
                  <td className="muted small">
                    {contact.captured_at
                      ? new Date(contact.captured_at).toLocaleDateString()
                      : '—'}
                  </td>
                  <td className="muted small">
                    {contact.rule_name || '—'}
                    {contact.backfilled && (
                      <span
                        className="pill pill-muted"
                        title="Worked out from past activity, not recorded at the time."
                      >
                        Inferred
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export function Contacts() {
  const { data: groups, isLoading } = useQuery({
    queryKey: ['contacts'],
    queryFn: getContacts,
  })
  // One panel at a time, and the first opens by default: with a single post the
  // page would otherwise show nothing but a closed row. `undefined` is "the user
  // has not chosen yet" and `null` is "all closed" -- they cannot be the same
  // value, because a media id of null is itself a real group and would then be
  // impossible to collapse.
  const [openKey, setOpenKey] = useState<string | null | undefined>(undefined)
  const current = openKey === undefined ? groupKey(groups?.[0]) : openKey

  // Keyed on the normalised email, never on the row. The same address appears
  // in several groups *and* can repeat inside one -- rows are keyed
  // `id-rule_name` precisely because two automations on one post capture the
  // same person -- so keying on rows would double-count and could send two
  // invitations to one inbox. Deduplication is structural this way rather than
  // a step somebody has to remember.
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [inviting, setInviting] = useState<string[] | null>(null)
  const { can } = usePermissions()
  const selectable = can('users.manage')

  const toggleEmail = (email: string) =>
    setSelected((current) => {
      const next = new Set(current)
      const key = email.toLowerCase()
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const selectMany = (emails: string[]) =>
    setSelected((current) => {
      const next = new Set(current)
      emails.forEach((email) => next.add(email.toLowerCase()))
      return next
    })

  const clearMany = (emails: string[]) =>
    setSelected((current) => {
      const next = new Set(current)
      emails.forEach((email) => next.delete(email.toLowerCase()))
      return next
    })

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Contacts</h1>
          <p className="muted">
            Emails captured by automations with the email gate switched on, grouped by the
            post that earned them.
          </p>
        </div>
        {groups && groups.length > 0 && <DownloadCsv label="Download all (CSV)" />}
      </div>

      {isLoading && <p className="muted">Loading…</p>}

      {groups?.length === 0 && (
        <div className="card">
          <h2>No contacts yet</h2>
          <p className="muted">
            Switch on <strong>Collect email before sending</strong> in an automation, and
            addresses people reply with will appear here.
          </p>
        </div>
      )}

      {selectable && selected.size > 0 && (
        <div className="select-bar">
          <span className="count">
            {selected.size} {selected.size === 1 ? 'recipient' : 'recipients'} selected
          </span>
          <button className="link" onClick={() => setSelected(new Set())}>
            Clear
          </button>
          <button className="cta" onClick={() => setInviting([...selected])}>
            Invite {selected.size} {selected.size === 1 ? 'person' : 'people'}…
          </button>
        </div>
      )}

      {groups && groups.length > 0 && (
        <div className="card auto-list">
          {groups.map((group) => {
            const key = groupKey(group)
            return (
              <GroupCard
                key={key}
                group={group}
                open={current === key}
                onToggle={() => setOpenKey(current === key ? null : key)}
                selectable={selectable}
                selected={selected}
                onToggleEmail={toggleEmail}
                onSelectGroup={selectMany}
                onClearGroup={clearMany}
              />
            )
          })}
        </div>
      )}

      {inviting && (
        <InviteDialog
          emails={inviting}
          onClose={() => {
            setInviting(null)
            setSelected(new Set())
          }}
        />
      )}
    </>
  )
}
