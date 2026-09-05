/** Minimal stroke icons, sized to the sidebar. */
const base = {
  width: 17,
  height: 17,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.9,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

export const HomeIcon = () => (
  <svg {...base}>
    <path d="M3 10.5 12 3l9 7.5" />
    <path d="M5 9.5V21h14V9.5" />
  </svg>
)

export const AutomationIcon = () => (
  <svg {...base}>
    <circle cx="6" cy="7" r="2.5" />
    <circle cx="18" cy="17" r="2.5" />
    <path d="M8.5 7H14a3 3 0 0 1 3 3v4.5" />
  </svg>
)

export const PostsIcon = () => (
  <svg {...base}>
    <rect x="3" y="3" width="18" height="18" rx="3" />
    <path d="m3 15 4.5-4.5a2 2 0 0 1 2.8 0L15 15" />
    <circle cx="15.5" cy="8.5" r="1.4" />
  </svg>
)

export const StoriesIcon = () => (
  <svg {...base}>
    {/* A story ring: the dashed outer circle is how Instagram itself signals
        "story", and it reads at 18px where a play triangle does not. */}
    <circle cx="12" cy="12" r="9" strokeDasharray="3 2.4" />
    <circle cx="12" cy="12" r="4.6" />
  </svg>
)

export const UpcomingIcon = () => (
  <svg {...base}>
    {/* A clock over a frame: scheduled, and attached to a post. */}
    <rect x="3" y="3" width="18" height="18" rx="3" />
    <circle cx="12" cy="12" r="4.4" />
    <path d="M12 9.8V12l1.6 1.1" />
  </svg>
)

export const ActivityIcon = () => (
  <svg {...base}>
    <path d="M3 12h4l2.5-6 4 12 2.5-6h5" />
  </svg>
)

export const ContactsIcon = () => (
  <svg {...base}>
    <circle cx="9" cy="8" r="3.2" />
    <path d="M3 20a6 6 0 0 1 12 0" />
    <path d="M16 5.5a3 3 0 0 1 0 5.6M17.5 20a5.5 5.5 0 0 0-2-4.3" />
  </svg>
)

/** A ringed pause. The other sidebar glyphs are all circle- or frame-shaped,
    so two bare bars would read lighter than its neighbours. */
export const PauseIcon = () => (
  <svg {...base}>
    <circle cx="12" cy="12" r="9" />
    <path d="M10 9v6M14 9v6" />
  </svg>
)

export const InstagramIcon = () => (
  <svg {...base}>
    <rect x="3" y="3" width="18" height="18" rx="5" />
    <circle cx="12" cy="12" r="3.6" />
    <circle cx="17.2" cy="6.8" r="1" fill="currentColor" stroke="none" />
  </svg>
)

export const SettingsIcon = () => (
  <svg {...base}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" />
  </svg>
)

export const AnalyticsIcon = () => (
  <svg {...base}>
    <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
  </svg>
)

export const TemplatesIcon = () => (
  <svg {...base}>
    <rect x="4" y="3" width="16" height="18" rx="2.5" />
    <path d="M8 8h8M8 12h8M8 16h4" />
  </svg>
)

export const PlusIcon = () => (
  <svg {...base} width={16} height={16}>
    <path d="M12 5v14M5 12h14" />
  </svg>
)

/* Interface glyphs. Previously these were text characters (× ▾ ‹) and one
   emoji (🌐) — an emoji renders differently on every platform and cannot be
   recoloured or aligned to the text baseline reliably. */

export const CloseIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="2" strokeLinecap="round" aria-hidden="true">
    <path d="M6 6l12 12M18 6L6 18" />
  </svg>
)

export const ChevronDownIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M6 9l6 6 6-6" />
  </svg>
)

export const ChevronLeftIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M15 18l-6-6 6-6" />
  </svg>
)

export const GlobeIcon = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18" />
  </svg>
)

export const ShieldIcon = () => (
  <svg {...base}>
    <path d="M12 3l7 3v5.5c0 4.2-2.9 7.7-7 9-4.1-1.3-7-4.8-7-9V6l7-3z" />
  </svg>
)

/** A person on a card: an account, not a contact. */
export const UsersIcon = () => (
  <svg {...base}>
    <rect x="3" y="4" width="18" height="16" rx="3" />
    <circle cx="12" cy="10.5" r="2.4" />
    <path d="M8 17a4 4 0 0 1 8 0" />
  </svg>
)

export const KeyIcon = () => (
  <svg {...base}>
    <circle cx="8" cy="15" r="3.4" />
    <path d="m10.6 12.6 7.4-7.4M15.6 7.6l2 2M17.8 5.4l2 2" />
  </svg>
)
