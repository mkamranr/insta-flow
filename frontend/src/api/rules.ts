import { apiDelete, apiGet, apiPost } from './client'

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

export interface Rule {
  id: string
  name: string
  scope: 'specific' | 'any'
  instagram_media_ids: string[]
  keywords: string[]
  any_word: boolean
  match_mode: 'exact' | 'contains' | 'word'
  case_sensitive: boolean
  opening_dm: OpeningDm
  message: string
  buttons: LinkButton[]
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

export const listPosts = () => apiGet<Post[]>('/api/posts')
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
  group: 'Popular' | 'Sell & earn' | 'Engage audience'
  /** The example exchange shown in the gallery. */
  example: { comment: string; reply: string; button?: string }
  rule: Partial<Rule>
}

export const TEMPLATE_GROUPS = ['Popular', 'Sell & earn', 'Engage audience'] as const

export const STARTER_TEMPLATES: StarterTemplate[] = [
  {
    id: 'send-link',
    title: 'Send link on keyword',
    blurb: 'Someone comments "LINK" → they get your link in DMs.',
    group: 'Popular',
    example: { comment: 'Need the LINK please!', reply: "Here's your link!", button: 'Shop the look' },
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
    example: { comment: 'LINK please!', reply: 'Follow me first!', button: "I'm following" },
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
    example: { comment: 'LINK', reply: "So happy you're here 😊", button: 'Send me the link' },
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
    example: { comment: 'Drop the CODE!', reply: "Here's 20% off just for you!", button: 'SAVE20' },
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
    example: { comment: 'How do I BUY this?', reply: "Here's the product page!", button: 'Shop now' },
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
    example: { comment: "What's the PRICE?", reply: 'Here are my packages!', button: 'View pricing' },
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
    example: { comment: "Where's the LINK?", reply: "Here's my LTK link!", button: 'Shop on LTK' },
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
    example: { comment: 'Love this! AMAZING content!', reply: 'Thanks so much! Means a lot 😊' },
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
    example: { comment: 'LINK!', reply: 'Just sent you a DM! 📩' },
    rule: {
      name: 'DM plus public reply',
      keywords: ['link'],
      message: "Here's the link!",
      buttons: [{ title: 'Open', url: 'https://example.com' }],
      public_reply: { enabled: true, message: 'Just sent you a DM! 📩' },
    },
  },
]
