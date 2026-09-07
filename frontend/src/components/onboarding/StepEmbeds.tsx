import { useMutation, useQuery } from '@tanstack/react-query'
import type { Block, OnboardingProgress } from '../../api/onboarding'
import { startConnect } from '../../api/instagram'
import { getMetaApp } from '../../api/settings'
import { ApiError } from '../../api/client'
import { CopyField } from '../common/CopyField'
import { MetaAppCredentials } from '../settings/MetaAppCredentials'
import { VerifyTokenField } from '../settings/VerifyTokenField'

/** The three interactive block kinds. The only file in the wizard that touches
 *  the Meta-app endpoints, so a role without `instagram.manage` degrades one
 *  block rather than the whole page. */
export function StepEmbeds({
  block,
  urls,
  progress,
  onRecheck,
}: {
  block: Block
  urls: Record<string, string>
  progress?: OnboardingProgress
  onRecheck: () => void
}) {
  const metaApp = useQuery({ queryKey: ['meta-app'], queryFn: getMetaApp, retry: false })
  const connect = useMutation({
    mutationFn: startConnect,
    onSuccess: ({ authorize_url }) => {
      window.location.href = authorize_url
    },
  })

  const forbidden = (metaApp.error as ApiError | undefined)?.status === 403
  const needsMetaApp =
    (block.kind === 'embed' && block.embed !== 'connect_instagram') ||
    (block.kind === 'copy' && block.value === 'verify_token')

  if (forbidden && needsMetaApp) {
    return (
      <div className="notice">
        This step needs the Instagram-app permission, which your role does not have. Ask
        whoever runs this install to grant it, or to do this step for you.
      </div>
    )
  }

  if (block.kind === 'copy') {
    const value =
      block.value === 'verify_token'
        ? (metaApp.data?.verify_token ?? '')
        : (urls[block.value] ?? '')
    return <CopyField label={block.label} value={value} hint={block.hint} />
  }

  if (block.kind === 'embed') {
    if (block.embed === 'meta_app_credentials') return <MetaAppCredentials compact />
    if (block.embed === 'verify_token') return <VerifyTokenField />
    return (
      <div className="row">
        <button className="cta" disabled={connect.isPending} onClick={() => connect.mutate()}>
          {connect.isPending ? 'Opening Instagram…' : 'Connect Instagram'}
        </button>
        {connect.error && (
          <p className="err small" role="alert">{(connect.error as Error).message}</p>
        )}
      </div>
    )
  }

  if (block.kind === 'check') {
    const confirmed =
      block.check === 'instagram_connected'
        ? Boolean(progress?.connected)
        : block.check === 'meta_app_saved'
          ? Boolean(progress?.meta_app_configured)
          : Boolean(progress?.webhooks_subscribed)

    return (
      <div className="row">
        {confirmed ? (
          <span className="pill on">Confirmed</span>
        ) : (
          <span className="pill off">Not yet</span>
        )}
        <span className="muted small">{block.label}</span>
        <button className="link" onClick={onRecheck}>
          Check again
        </button>
      </div>
    )
  }

  return null
}
