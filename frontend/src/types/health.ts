export interface ReadyResponse {
  status: 'ready' | 'not_ready'
  checks: { mongodb: boolean; redis: boolean; config: boolean }
  dry_run: boolean
  missing_config?: string[]
}
