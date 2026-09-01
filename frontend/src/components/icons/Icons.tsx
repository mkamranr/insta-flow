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
