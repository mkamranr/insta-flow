import { createElement, type ReactNode } from 'react'

/** The two bits of inline markup the step content uses: `code` and **bold**.
 *
 * Hand-rolled rather than pulling in a markdown renderer, and certainly not
 * `dangerouslySetInnerHTML` -- which appears nowhere in this codebase and which
 * the CSP would not help with anyway. Links live in their own block kind, which
 * keeps this to two rules and keeps every href type-checked.
 *
 * Pure and exported for the reason `PermissionGrid.togglePermission` is: there
 * is no frontend test runner yet, so the fiddly logic stays somewhere it can be
 * covered the day there is one.
 */
export function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = []
  // Split on `code` and **bold**, keeping the delimiters.
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g)

  parts.forEach((part, index) => {
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      out.push(createElement('code', { key: index }, part.slice(1, -1)))
    } else if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      out.push(createElement('strong', { key: index }, part.slice(2, -2)))
    } else if (part) {
      out.push(part)
    }
  })

  return out
}
