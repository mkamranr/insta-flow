import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  getProgress,
  getWizardContent,
  setStepDone,
  type WizardStep,
} from '../api/onboarding'
import { StepBlocks } from '../components/onboarding/StepBlocks'
import { StepEmbeds } from '../components/onboarding/StepEmbeds'
import { ChevronDownIcon } from '../components/icons/Icons'

/**
 * The guided Meta-app setup.
 *
 * Every step is visible, exactly one is expanded, and the first not-done one
 * opens by default. Chosen over a strict next/back stepper for two reasons that
 * come from how it is actually used: somebody alt-tabbing to Meta's dashboard
 * mostly wants to re-read the current step and then check what step 4 said
 * while on step 8 -- the trap tables exist *because* the steps refer to each
 * other -- and a visible list makes both one click while leaving the browser's
 * own find-in-page working across the titles. And resuming days later needs no
 * stored cursor: "where was I" is derivable as the first not-done step, so
 * nothing can drift out of step with the ticks.
 *
 * The guided-forward feel survives: marking a step done opens the next one.
 */
export function GetStarted() {
  const queryClient = useQueryClient()
  const { data: content } = useQuery({
    queryKey: ['onboarding', 'steps'],
    queryFn: getWizardContent,
    staleTime: Infinity,
  })
  const { data: progress, refetch } = useQuery({
    queryKey: ['onboarding', 'progress'],
    queryFn: getProgress,
    // The one query in this app that opts into this, for the same shape of
    // reason `AutoDmPause` does: the user has been away in another tab.
    refetchOnWindowFocus: true,
  })

  // Three-valued, like the Contacts accordion: `undefined` means "the user has
  // not chosen yet", so the first not-done step opens; `null` means all closed.
  const [openId, setOpenId] = useState<string | null | undefined>(undefined)

  const toggle = useMutation({
    mutationFn: ({ id, done }: { id: string; done: boolean }) => setStepDone(id, done),
    onSuccess: (updated, { id, done }) => {
      queryClient.setQueryData(['onboarding', 'progress'], updated)
      if (done) {
        const next = content?.steps.find(
          (step) => !step.optional && step.id !== id && !updated.done.includes(step.id),
        )
        setOpenId(next?.id ?? null)
      }
    },
  })

  if (!content || !progress) return <div className="centered muted">Loading…</div>

  const done = new Set(progress.done)
  const required = content.steps.filter((step) => !step.optional)
  const completed = required.filter((step) => done.has(step.id)).length
  const percent = required.length ? Math.round((completed / required.length) * 100) : 0
  const firstOutstanding = required.find((step) => !done.has(step.id))
  const current = openId === undefined ? firstOutstanding?.id ?? null : openId

  const stepNumber = (step: WizardStep) =>
    done.has(step.id) ? 'done' : step.id === current ? '' : 'todo'

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Get started</h1>
          <p className="muted">
            Connect your own Instagram account by creating your own Meta app.
          </p>
        </div>
      </div>

      {progress.connected ? (
        <div className="card ok">
          <h2>Your Instagram account is connected</h2>
          <p className="muted">
            Setup is complete. The steps below stay here for reference — if something stops
            working, the troubleshooting table at the bottom is the fastest way in.
          </p>
        </div>
      ) : (
        <div className="card">
          <div className="wiz-progress">
            <div className="meter-track">
              <div className="meter-fill" style={{ width: `${percent}%` }} />
            </div>
            <span className="muted small">
              {completed} of {required.length} done
            </span>
          </div>
          <StepBlocks blocks={content.intro} />
        </div>
      )}

      <div className="card auto-list">
        {content.steps.map((step) => {
          const isOpen = step.id === current
          const isDone = done.has(step.id)
          return (
            <div className="auto-card" key={step.id}>
              <button
                className="wiz-head"
                aria-expanded={isOpen}
                onClick={() => setOpenId(isOpen ? null : step.id)}
              >
                <span className={`step-num ${stepNumber(step)}`.trim()}>
                  {isDone ? '✓' : step.number}
                </span>
                <span className="wiz-title">
                  <span className="auto-name">{step.title}</span>
                  <span className="muted small">{step.summary}</span>
                </span>
                {step.optional && <span className="pill pill-muted">only if needed</span>}
                <span className={isOpen ? 'chev open' : 'chev'} aria-hidden="true">
                  <ChevronDownIcon />
                </span>
              </button>

              {isOpen && (
                <div className="wiz-body">
                  <StepBlocks
                    blocks={step.body}
                    interactive={(block, index) => (
                      <StepEmbeds
                        key={index}
                        block={block}
                        urls={content.urls}
                        progress={progress}
                        onRecheck={() => refetch()}
                      />
                    )}
                  />
                  <div className="row">
                    <button
                      className={isDone ? 'secondary' : 'cta'}
                      disabled={toggle.isPending}
                      onClick={() => toggle.mutate({ id: step.id, done: !isDone })}
                    >
                      {isDone ? 'Not yet' : 'Mark as done'}
                    </button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>

      <div className="card">
        <h2>If something is not working</h2>
        <StepBlocks blocks={content.troubleshooting} />
      </div>
    </>
  )
}
