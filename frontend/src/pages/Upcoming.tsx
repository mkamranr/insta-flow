import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError } from '../api/client'
import { createUpcoming, deleteUpcoming, getUpcoming, type Rule } from '../api/rules'
import { RuleEditor } from '../components/rules/RuleEditor'

/**
 * One automation, armed for whatever you post next.
 *
 * It applies to a post published *after* the template was saved — a post from
 * last week is not what "upcoming" means, however late the sync happens to run.
 * If several appear between syncs the earliest wins, so the outcome does not
 * depend on when the sync fired.
 */
export function Upcoming() {
  const queryClient = useQueryClient()
  const [composing, setComposing] = useState(false)

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['upcoming'],
    queryFn: getUpcoming,
    retry: false,
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['upcoming'] })
    queryClient.invalidateQueries({ queryKey: ['rules'] })
  }

  const remove = useMutation({ mutationFn: deleteUpcoming, onSuccess: invalidate })

  const pending = data?.pending ?? null
  const applied = (data?.history ?? []).filter((h) => h.status === 'applied')

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Upcoming post</h1>
          <p className="muted">
            Set up an automation now; it arms itself on your next post.
          </p>
        </div>
        <button
          className="cta"
          disabled={!!pending || isLoading}
          onClick={() => setComposing(true)}
        >
          + Add upcoming post ({pending ? 1 : 0}/1)
        </button>
      </div>

      {isError && <div className="card err">{(error as ApiError).message}</div>}
      {remove.isError && <div className="card err">{(remove.error as ApiError).message}</div>}

      {isLoading && <p className="muted">Loading…</p>}

      {!isLoading && !pending && (
        <div className="card empty-card">
          <h2>Nothing waiting</h2>
          <p className="muted">
            Write the automation before you publish, and the moment the post appears it is
            already answering comments — no window where people comment and get nothing.
          </p>
          <ol className="muted small steps">
            <li>Save the keywords and the message here.</li>
            <li>Publish the post on Instagram as usual.</li>
            <li>
              Social Flow checks for new posts every 15 minutes and arms the automation on the
              first post published after you saved it.
            </li>
          </ol>
          <p className="muted small">
            One at a time. It is consumed when it lands, so the post after next is unaffected.
          </p>
          <button className="cta" onClick={() => setComposing(true)}>
            + Add upcoming post (0/1)
          </button>
        </div>
      )}

      {pending && (
        <div className="card">
          <div className="upcoming-head">
            <span className="pill on">Waiting for your next post</span>
            <button
              className="danger"
              disabled={remove.isPending}
              onClick={() => remove.mutate(pending.id)}
            >
              {remove.isPending ? 'Removing…' : 'Remove'}
            </button>
          </div>

          <dl className="kv">
            <dt>Name</dt>
            <dd>{pending.rule.name || '(unnamed)'}</dd>
            <dt>Trigger</dt>
            <dd>
              {pending.rule.any_word
                ? 'Any comment'
                : `Comments containing ${(pending.rule.keywords ?? []).join(', ')}`}
            </dd>
            <dt>Reply</dt>
            <dd className="clamp-3">{pending.rule.message}</dd>
            <dt>Armed since</dt>
            <dd>{new Date(pending.created_at).toLocaleString()}</dd>
          </dl>

          <p className="muted small">
            Applies to the first post published after this time. Posts from before it are left
            alone.
          </p>
        </div>
      )}

      {applied.length > 0 && (
        <section className="section">
          <h2>Already applied</h2>
          <div className="card table-card">
            <table className="table">
              <thead>
                <tr>
                  <th>Automation</th>
                  <th>Applied to</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {applied.map((row) => (
                  <tr key={row.id}>
                    <td>{row.rule.name || '(unnamed)'}</td>
                    <td className="mono small">{row.instagram_media_id}</td>
                    <td>{row.applied_at ? new Date(row.applied_at).toLocaleString() : '–'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {composing && (
        <RuleEditor
          trigger="comment"
          onClose={() => setComposing(false)}
          saveAs={async (draft: Partial<Rule>) => {
            await createUpcoming(draft)
            invalidate()
          }}
        />
      )}
    </>
  )
}
