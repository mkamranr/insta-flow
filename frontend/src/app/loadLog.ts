/**
 * A record of how each document load happened.
 *
 * A dialog losing its contents mid-session was reported, and the polling that
 * looked responsible was measured and cleared: seven consecutive 30s refetches,
 * including four with deliberately changed payloads, left an open editor and its
 * typed text untouched. The one mechanism that produces every reported symptom
 * at once -- page refresh, posts refetched, dialog reset -- is a full document
 * load, and by then the URL that caused it is gone.
 *
 * So each load leaves a note behind. `navigation.type` separates the cases that
 * matter: `reload` is the user or `location.reload()`, `navigate` is a link or
 * `location.assign`/`replace` (which is what `WorkspaceGate` does on a
 * prefix-less URL), `back_forward` is history. Read it with
 * `__loadLog()` in the console.
 *
 * localStorage rather than sessionStorage: a tab that gets closed in
 * frustration is exactly the one worth reading afterwards.
 */
const KEY = 'flow.loadLog'
const KEEP = 25

type Entry = { at: string; path: string; type: string; referrer: string }

export function loadLog(): Entry[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '[]') as Entry[]
  } catch {
    return []
  }
}

export function recordLoad(): void {
  try {
    const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined
    const entry: Entry = {
      at: new Date().toISOString(),
      path: window.location.pathname + window.location.search,
      type: nav?.type ?? 'unknown',
      referrer: document.referrer,
    }
    localStorage.setItem(KEY, JSON.stringify([...loadLog(), entry].slice(-KEEP)))
  } catch {
    // Private mode, or storage full. A diagnostic that breaks the app it is
    // diagnosing is worse than no diagnostic.
  }
  ;(window as unknown as { __loadLog: () => Entry[] }).__loadLog = loadLog
}
