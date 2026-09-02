import { apiGet } from './client'

export interface Summary {
  connected: boolean
  days?: number
  comments?: number
  matched?: number
  unmatched?: number
  dms_sent?: number
  failed?: number
  skipped?: number
  dry_runs?: number
  link_clicks?: number
  click_through_rate?: number
  leads_captured?: number
  active_rules?: number
  total_rules?: number
}

export interface LinkDestination {
  target_url: string
  title: string
  clicks: number
  /** Links minted for this destination — one per recipient. */
  delivered: number
  /** Tokens with at least one click, i.e. distinct people, not distinct taps. */
  unique: number
  click_through_rate: number
  last_clicked_at: string | null
}

export interface RulePerformance {
  id: string
  name: string
  keywords: string[]
  enabled: boolean
  instagram_media_ids: string[]
  dms_sent: number
  link_clicks: number
  click_through_rate: number
  links: LinkDestination[]
}

export interface ActivityRow {
  id: string
  status: string
  action: string
  dry_run: boolean
  username: string
  comment_text: string
  message_text: string | null
  rule_name: string
  policy_decision: string | null
  error: string | null
  attempts: number
  created_at: string | null
}

export interface Contact {
  id: string
  instagram_user_id: string
  email: string
  created_at: string | null
}

export interface TopCommenter {
  username: string
  comments: number
  last_at: string | null
}

export const getSummary = (days = 7) => apiGet<Summary>(`/api/analytics/summary?days=${days}`)
export const getRulePerformance = () => apiGet<RulePerformance[]>('/api/analytics/rules')
export const getActivity = (status?: string) =>
  apiGet<ActivityRow[]>(`/api/analytics/activity${status ? `?status=${status}` : ''}`)
export const getTopCommenters = () => apiGet<TopCommenter[]>('/api/analytics/top-commenters')
export const getContacts = () => apiGet<Contact[]>('/api/analytics/contacts')

export interface AccountPerformance {
  connected: boolean
  username?: string
  account_type?: string
  followers_count?: number | null
  follows_count?: number | null
  media_count?: number | null
  days?: number
  collecting_since?: string | null
  snapshot_count?: number
  /** null until two snapshots exist — one day cannot express a change. */
  net_growth?: number | null
  best_day?: { day: string; change: number } | null
  worst_day?: { day: string; change: number } | null
  series?: { day: string; followers_count: number }[]
  /** Meta gives a total only, so gross new-vs-unfollowed is never derivable. */
  gross_growth_available?: boolean
  gross_growth_note?: string
  webhook_subscribed?: boolean
  subscribed_fields?: string[]
}

export interface PostPerformance {
  id: string
  instagram_media_id: string
  caption?: string | null
  permalink?: string | null
  thumbnail_url?: string | null
  media_product_type?: string | null
  comments: number
  keyword_comments: number
  trigger_rate: number
}

export const getAccountPerformance = (days = 7) =>
  apiGet<AccountPerformance>(`/api/analytics/account?days=${days}`)
export const getPostPerformance = () => apiGet<PostPerformance[]>('/api/analytics/posts')
