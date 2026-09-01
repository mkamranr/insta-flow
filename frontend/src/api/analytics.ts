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

export interface RulePerformance {
  id: string
  name: string
  keywords: string[]
  enabled: boolean
  dms_sent: number
  link_clicks: number
  click_through_rate: number
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
