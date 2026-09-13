/**
 * Page controls for a table that is sliced in the browser.
 *
 * Client-side on purpose: both tables that use this need their full dataset for
 * reasons unrelated to display -- the Automations page builds its filter chips
 * and its search from every rule, and joins each row to per-rule analytics by
 * id -- so there is nothing to gain by fetching a page at a time.
 *
 * `page` is zero-based and is expected to be clamped by the caller against the
 * current `total`. That clamp belongs in the render rather than in an effect:
 * deleting a row elsewhere shrinks the list under a page index that is already
 * set, and a component that trusts its prop here would draw an empty table for
 * one frame before an effect could correct it.
 */
export function Pager({
  page,
  pageSize,
  total,
  onPage,
  label,
}: {
  page: number
  pageSize: number
  /** Rows after filtering -- not the length of the current page. */
  total: number
  onPage: (page: number) => void
  /** Names the control for a screen reader, e.g. 'Automation performance'. */
  label: string
}) {
  // Nothing to page through, so nothing to draw. An account with three
  // automations should not gain a row of chrome telling it so.
  if (total <= pageSize) return null

  const pages = Math.ceil(total / pageSize)
  const first = page * pageSize
  const last = Math.min(first + pageSize, total)

  return (
    <nav className="pager" aria-label={`${label} pages`}>
      <span className="muted small" aria-live="polite">
        Showing {first + 1}–{last} of {total}
      </span>
      <span className="pager-controls">
        <button className="secondary" disabled={page === 0} onClick={() => onPage(page - 1)}>
          Previous
        </button>
        <span className="muted small">
          Page {page + 1} of {pages}
        </span>
        <button
          className="secondary"
          disabled={page >= pages - 1}
          onClick={() => onPage(page + 1)}
        >
          Next
        </button>
      </span>
    </nav>
  )
}
