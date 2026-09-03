import { useEffect, useState } from 'react'

/**
 * Light / dark / system.
 *
 * "System" is the default and stores nothing, so a fresh browser follows the OS
 * rather than whatever this app happened to prefer. An explicit choice writes
 * `data-theme` onto <html>, which is what lets a reader pick light on a dark
 * machine — a bare `prefers-color-scheme` rule cannot be overridden.
 */

export type Theme = 'light' | 'dark' | 'system'

const KEY = 'social-flow-theme'

export function storedTheme(): Theme {
  try {
    const saved = localStorage.getItem(KEY)
    return saved === 'light' || saved === 'dark' ? saved : 'system'
  } catch {
    // Private windows and blocked site data throw on access rather than
    // returning null, so this cannot be a plain null check.
    return 'system'
  }
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement
  if (theme === 'system') {
    root.removeAttribute('data-theme')
  } else {
    root.setAttribute('data-theme', theme)
  }

  // Keep the mobile browser chrome in step with the surface behind it.
  const dark =
    theme === 'dark' ||
    (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', dark ? '#0b0e14' : '#7a3fb5')
}

const OPTIONS: { id: Theme; label: string; icon: React.ReactNode }[] = [
  {
    id: 'light',
    label: 'Light',
    icon: (
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" aria-hidden="true">
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    ),
  },
  {
    id: 'system',
    label: 'System',
    icon: (
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="2" y="4" width="20" height="13" rx="2" />
        <path d="M8 21h8M12 17v4" />
      </svg>
    ),
  },
  {
    id: 'dark',
    label: 'Dark',
    icon: (
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
      </svg>
    ),
  },
]

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(storedTheme)

  useEffect(() => {
    applyTheme(theme)
    try {
      if (theme === 'system') localStorage.removeItem(KEY)
      else localStorage.setItem(KEY, theme)
    } catch {
      // Nothing to do: the theme still applies for this session.
    }
  }, [theme])

  // Follow the OS live while on "system" — otherwise the page stays on
  // whichever mode it happened to load in.
  useEffect(() => {
    if (theme !== 'system') return
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const sync = () => applyTheme('system')
    query.addEventListener('change', sync)
    return () => query.removeEventListener('change', sync)
  }, [theme])

  return (
    <div className="theme-toggle" role="radiogroup" aria-label="Colour theme">
      {OPTIONS.map((option) => (
        <button
          key={option.id}
          type="button"
          role="radio"
          aria-checked={theme === option.id}
          aria-label={option.label}
          title={option.label}
          className={theme === option.id ? 'theme-opt active' : 'theme-opt'}
          onClick={() => setTheme(option.id)}
        >
          {option.icon}
        </button>
      ))}
    </div>
  )
}
