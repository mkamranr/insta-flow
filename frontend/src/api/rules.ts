import { apiDelete, apiGet, apiPost, apiUpload } from './client'

export interface LinkButton {
  title: string
  url: string
}

export interface OpeningDm {
  enabled: boolean
  message: string
  button_text: string
}

export interface Toggleable {
  enabled: boolean
  message?: string
  prompt?: string
}

export interface AiMatching {
  enabled: boolean
  intents: string[]
  min_confidence: number | null
}

export interface Attachment {
  type: 'image' | 'video' | 'audio' | 'file'
  url: string
  storage_key: string
  filename: string
  size_bytes: number
}

/** What starts an automation. "comment" is the original and the default. */
export type RuleTrigger = 'comment' | 'story_reply' | 'dm_keyword'

export interface Rule {
  id: string
  name: string
  trigger: RuleTrigger
  scope: 'specific' | 'any'
  instagram_media_ids: string[]
  keywords: string[]
  any_word: boolean
  match_mode: 'exact' | 'contains' | 'word'
  case_sensitive: boolean
  opening_dm: OpeningDm
  message: string
  buttons: LinkButton[]
  attachment: Attachment | null
  /** Seconds to wait before replying. 0 means immediately; capped at 59:59. */
  reply_delay_seconds: number
  public_reply: { enabled: boolean; message: string }
  require_follow: { enabled: boolean; prompt: string }
  collect_email: { enabled: boolean; prompt: string }
  ai: AiMatching
  once_per_person: boolean
  enabled: boolean
  priority: number
}

export interface Post {
  id: string
  instagram_media_id: string
  media_type: string | null
  media_product_type: string | null
  caption: string | null
  permalink: string | null
  thumbnail_url: string | null
  published_at: string | null
  like_count: number | null
  comments_count: number | null
  rule_count: number
}

export const listPosts = (limit = 0, offset = 0) =>
  apiGet<Post[]>(`/api/posts?limit=${limit}&offset=${offset}`)
export const syncPosts = () => apiPost<{ synced: number }>('/api/posts/sync')

export const listRules = (mediaId?: string) =>
  apiGet<Rule[]>(mediaId ? `/api/rules?instagram_media_id=${encodeURIComponent(mediaId)}` : '/api/rules')
export const createRule = (rule: Partial<Rule>) => apiPost<Rule>('/api/rules', rule)
export const deleteRule = (id: string) => apiDelete<void>(`/api/rules/${id}`)
export const getIntents = () =>
  apiGet<{ intents: string[]; ai_enabled: boolean; model: string | null }>('/api/rules/intents')
export const previewMessage = (message: string) =>
  apiPost<{ rendered: string }>('/api/rules/preview', { message })

export async function patchRule(id: string, changes: Partial<Rule>): Promise<Rule> {
  const response = await fetch(`/api/rules/${id}`, {
    method: 'PATCH',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(changes),
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload?.error?.message ?? 'Update failed')
  return payload as Rule
}

/** Starter templates, limited to comment→DM flows. */
export interface StarterTemplate {
  id: string
  title: string
  blurb: string
  group: (typeof TEMPLATE_GROUPS)[number]
  /** The example exchange shown in the gallery. */
  example: { username: string; comment: string; reply: string; button?: string }
  rule: Partial<Rule>
}

export const TEMPLATE_GROUPS = [
  'Popular',
  'Sell & earn',
  'Engage audience',
  'Capture leads',
  'Book clients',
] as const

export const STARTER_TEMPLATES: StarterTemplate[] = [
  {
    id: 'send-link',
    title: 'Send link on keyword',
    blurb: 'Someone comments "LINK" → they get your link in DMs.',
    group: 'Popular',
    example: { username: 'mia.styles', comment: 'Need the LINK please!', reply: "Here's your link!", button: 'Shop the look' },
    rule: {
      name: 'Send link on keyword',
      keywords: ['link'],
      message: "Here's the link you asked for!",
      buttons: [{ title: 'Open', url: 'https://example.com' }],
    },
  },
  {
    id: 'require-follow',
    title: 'Require follow before link',
    blurb: 'Ask them to follow you first. Grow your audience with every link.',
    group: 'Popular',
    example: { username: 'alex.fits', comment: 'LINK please!', reply: 'Follow me first!', button: "I'm following" },
    rule: {
      name: 'Require follow before link',
      keywords: ['link'],
      message: "Here's the link — thanks for following!",
      buttons: [{ title: 'Open', url: 'https://example.com' }],
      require_follow: {
        enabled: true,
        prompt: "Follow me first and comment again — then I'll send the link!",
      },
    },
  },
  {
    id: 'opening-dm',
    title: 'Two-step with an opening DM',
    blurb: 'Send a friendly opener with a button, then the link once they tap.',
    group: 'Popular',
    example: { username: 'new.follower', comment: 'LINK', reply: "So happy you're here 😊", button: 'Send me the link' },
    rule: {
      name: 'Two-step link',
      keywords: ['link'],
      opening_dm: {
        enabled: true,
        message: "Hey! So happy you're here 😊 Tap below and I'll send it over.",
        button_text: 'Send me the link',
      },
      message: "Here's the link — enjoy!",
      buttons: [{ title: 'Open', url: 'https://example.com' }],
    },
  },
  {
    id: 'discount-code',
    title: 'Send discount codes',
    blurb: 'Someone comments "CODE" → they get an exclusive discount in DMs.',
    group: 'Sell & earn',
    example: { username: 'deal.hunter', comment: 'Drop the CODE!', reply: "Here's 20% off just for you!", button: 'SAVE20' },
    rule: {
      name: 'Send discount codes',
      keywords: ['code', 'discount'],
      message: "Here's 20% off just for you: SAVE20",
      buttons: [{ title: 'Shop now', url: 'https://example.com/shop' }],
    },
  },
  {
    id: 'promote-product',
    title: 'Promote your products',
    blurb: 'Someone comments "BUY" → they get your product page instantly.',
    group: 'Sell & earn',
    example: { username: 'love.it', comment: 'How do I BUY this?', reply: "Here's the product page!", button: 'Shop now' },
    rule: {
      name: 'Promote your products',
      keywords: ['buy', 'shop'],
      message: "Here's the product page!",
      buttons: [{ title: 'Shop now', url: 'https://example.com/product' }],
    },
  },
  {
    id: 'pricing',
    title: 'Share pricing info',
    blurb: 'Someone comments "PRICE" → they get your rates or packages.',
    group: 'Sell & earn',
    example: { username: 'curious.client', comment: "What's the PRICE?", reply: 'Here are my packages!', button: 'View pricing' },
    rule: {
      name: 'Share pricing info',
      keywords: ['price', 'pricing', 'rates'],
      message: 'Here are my packages!',
      buttons: [{ title: 'View pricing', url: 'https://example.com/pricing' }],
    },
  },
  {
    id: 'affiliate',
    title: 'Share affiliate links',
    blurb: 'Auto-send your LTK, Amazon, or ShopMy links on a keyword.',
    group: 'Sell & earn',
    example: { username: 'shopper.sam', comment: "Where's the LINK?", reply: "Here's my LTK link!", button: 'Shop on LTK' },
    rule: {
      name: 'Share affiliate links',
      keywords: ['link', 'where'],
      message: "Here's my link!",
      buttons: [{ title: 'Shop the look', url: 'https://example.com/ltk' }],
    },
  },
  {
    id: 'thank-commenters',
    title: 'Thank commenters',
    blurb: 'Someone comments a keyword → send a thank you DM automatically.',
    group: 'Engage audience',
    example: { username: 'fan.account', comment: 'Love this! AMAZING content!', reply: 'Thanks so much! Means a lot 😊' },
    rule: {
      name: 'Thank commenters',
      keywords: ['love', 'amazing'],
      message: 'Thanks so much! Means a lot 😊',
    },
  },
  {
    id: 'public-reply',
    title: 'Reply publicly too',
    blurb: 'Answer in the comments as well as the DM, to boost engagement.',
    group: 'Engage audience',
    example: { username: 'public.pam', comment: 'LINK!', reply: 'Just sent you a DM! 📩' },
    rule: {
      name: 'DM plus public reply',
      keywords: ['link'],
      message: "Here's the link!",
      buttons: [{ title: 'Open', url: 'https://example.com' }],
      public_reply: { enabled: true, message: 'Just sent you a DM! 📩' },
    },
  },
  {
    id: 'start-conversations',
    title: 'Start conversations',
    blurb: 'Someone comments a keyword → start a real conversation in DMs.',
    group: 'Engage audience',
    example: {
      username: 'curious.chris',
      comment: 'Tell me MORE about this!',
      reply: 'Hey! What would you like to know?',
      button: 'Chat',
    },
    rule: {
      name: 'Start conversations',
      keywords: ['more', 'info', 'tell me'],
      message: 'Hey! What would you like to know? Just reply here and I will get back to you.',
    },
  },
  {
    id: 'lead-magnet',
    title: 'Deliver lead magnets',
    blurb: 'Someone comments "FREE" → they get your PDF, checklist, or guide.',
    group: 'Capture leads',
    example: {
      username: 'fitcoach.mike',
      comment: 'I need the FREE guide!',
      reply: 'Your free guide is ready!',
      button: 'Download PDF',
    },
    rule: {
      name: 'Deliver lead magnets',
      keywords: ['free', 'guide', 'checklist'],
      message: 'Your free guide is ready — grab it below!',
      buttons: [{ title: 'Download PDF', url: 'https://example.com/guide.pdf' }],
    },
  },
  {
    id: 'collect-emails',
    title: 'Collect emails first',
    blurb: 'Someone comments "GUIDE" → ask for their email, then send the content.',
    group: 'Capture leads',
    example: {
      username: 'eager.emma',
      comment: 'Send me the GUIDE!',
      reply: "Drop your email and I'll send it!",
      button: 'Send email',
    },
    rule: {
      name: 'Collect emails first',
      keywords: ['guide'],
      message: 'Thanks! Here is the guide you asked for.',
      buttons: [{ title: 'Open guide', url: 'https://example.com/guide' }],
      collect_email: { enabled: true, prompt: "Drop your email and I'll send it straight over!" },
    },
  },
  {
    id: 'waitlist',
    title: 'Grow your waitlist',
    blurb: 'Someone comments "WAITLIST" → they get your signup link.',
    group: 'Capture leads',
    example: {
      username: 'hyped.hannah',
      comment: 'Add me to the WAITLIST!',
      reply: "You're in! Here's the waitlist:",
      button: 'Join waitlist',
    },
    rule: {
      name: 'Grow your waitlist',
      keywords: ['waitlist', 'early access'],
      message: "You're in! Join the waitlist here and I'll let you know the moment it opens.",
      buttons: [{ title: 'Join waitlist', url: 'https://example.com/waitlist' }],
    },
  },
  {
    id: 'booking-links',
    title: 'Send booking links',
    blurb: 'Someone comments "BOOK" → they get your Calendly or booking page.',
    group: 'Book clients',
    example: {
      username: 'busy.brand',
      comment: 'Can I BOOK a call?',
      reply: 'Pick a time that works!',
      button: 'Book now',
    },
    rule: {
      name: 'Send booking links',
      keywords: ['book', 'call', 'consult'],
      message: 'Pick a time that works for you and I will see you there.',
      buttons: [{ title: 'Book now', url: 'https://calendly.com/example' }],
    },
  },
  {
    id: 'portfolio',
    title: 'Share your portfolio',
    blurb: 'Someone comments "WORK" → they get your portfolio link in DMs.',
    group: 'Book clients',
    example: {
      username: 'potential.client',
      comment: 'Show me your WORK!',
      reply: "Here's my portfolio!",
      button: 'View portfolio',
    },
    rule: {
      name: 'Share your portfolio',
      keywords: ['work', 'portfolio'],
      message: "Here's my portfolio — have a look and tell me what you think!",
      buttons: [{ title: 'View portfolio', url: 'https://example.com/portfolio' }],
    },
  },
  {
    id: 'webinars',
    title: 'Promote webinars',
    blurb: 'Someone comments "REGISTER" → they get your webinar signup link.',
    group: 'Book clients',
    example: {
      username: 'want.to.learn',
      comment: 'I want to REGISTER!',
      reply: "You're in! Here's the link:",
      button: 'Register now',
    },
    rule: {
      name: 'Promote webinars',
      keywords: ['register', 'webinar'],
      message: "You're in! Register here and I'll send the reminder.",
      buttons: [{ title: 'Register now', url: 'https://example.com/webinar' }],
    },
  },
  {
    id: 'share-content',
    title: 'Share your content',
    blurb: 'Someone comments "VIDEO" → they get your YouTube or podcast link.',
    group: 'Book clients',
    example: {
      username: 'viewer.vic',
      comment: 'Send me the VIDEO!',
      reply: "Here's the full video!",
      button: 'Watch on YouTube',
    },
    rule: {
      name: 'Share your content',
      keywords: ['video', 'episode', 'podcast'],
      message: "Here's the full version — enjoy!",
      buttons: [{ title: 'Watch now', url: 'https://youtube.com/@example' }],
    },
  },
  {
    id: 'send-file',
    title: 'Send a file or guide',
    blurb: 'Someone comments "PDF" → they tap once and the file lands in their DMs.',
    group: 'Capture leads',
    example: {
      username: 'reader.robin',
      comment: 'Send me the PDF!',
      reply: 'Your guide is ready!',
      button: 'Send it over',
    },
    rule: {
      name: 'Send a file or guide',
      keywords: ['pdf', 'file', 'guide'],
      // A file cannot ride the reply to a comment -- only the button tap opens
      // the messaging window an attachment needs (docs/meta-api.md §4c), so
      // this template arrives with the opening DM already switched on.
      opening_dm: {
        enabled: true,
        message: "Happy to send that over! Tap below and it's yours.",
        button_text: 'Send it over',
      },
      message: 'Here you go — enjoy!',
      buttons: [],
    },
  },
]

export interface UploadedAttachment {
  type: 'image' | 'video' | 'audio' | 'file'
  url: string
  storage_key: string
  filename: string
  size_bytes: number
}

/** Uploads the file and returns what to store on the rule as `attachment`. */
export const uploadAttachment = (file: File) =>
  apiUpload<UploadedAttachment>('/api/uploads', file)
