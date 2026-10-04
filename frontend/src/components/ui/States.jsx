/**
 * The seven UI states required by spec §22.
 *
 * Every major section must be able to render: loading, empty, error, disconnected,
 * blockchain-pending, blockchain-failure and confirmed. Centralising them here is what
 * makes that guarantee checkable rather than aspirational — a page cannot accidentally
 * forget one, because it composes `<StateGate>`.
 */

import { GlassCard } from './Primitives'

/** Loading placeholder: a titled shimmer block. */
export function Skeleton({ className = '', label = 'Loading' }) {
  return (
    <div className={`shimmer rounded-xl ${className}`} role="status" aria-label={label}>
      <span className="sr-only">{label}…</span>
    </div>
  )
}

/** A few stacked skeletons, so a loading section keeps its shape. */
export function SkeletonList({ rows = 3, className = '' }) {
  return (
    <div className={`space-y-2 ${className}`}>
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-14 w-full" />
      ))}
    </div>
  )
}

/** Nothing here yet — and that is a legitimate state, not an error. */
export function EmptyState({ title = 'Nothing here yet', detail, action, className = '' }) {
  return (
    <div className={`flex flex-col items-center justify-center px-6 py-10 text-center ${className}`}>
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full border border-white/10 bg-white/[0.03] text-ink-faint">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path
            d="M4 7h16M4 12h10M4 17h7"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </svg>
      </div>
      <p className="text-sm font-medium text-ink-muted">{title}</p>
      {detail ? <p className="mt-1.5 max-w-sm text-xs leading-relaxed text-ink-faint">{detail}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  )
}

/** Something failed. Always shows the reason and, when possible, a retry. */
export function ErrorState({ error, title = 'Could not load this section', onRetry, className = '' }) {
  const message =
    typeof error === 'string' ? error : error?.message || 'An unexpected error occurred.'
  const isNetwork = typeof error?.status === 'number' && error.status === 0

  return (
    <div
      className={`flex flex-col items-center justify-center px-6 py-10 text-center ${className}`}
      role="alert"
    >
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full border border-red/25 bg-red/10 text-red">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path
            d="M12 8v5m0 3.5h.01M10.3 3.9 2.6 17.1A1.9 1.9 0 0 0 4.3 20h15.4a1.9 1.9 0 0 0 1.7-2.9L13.7 3.9a1.9 1.9 0 0 0-3.4 0Z"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
      <p className="text-sm font-medium text-ink">{title}</p>
      <p className="mt-1.5 max-w-md text-xs leading-relaxed text-ink-faint">{message}</p>
      {isNetwork ? (
        <p className="mt-2 max-w-md font-mono text-2xs text-amber">
          Start the backend with: python -m uvicorn app.main:app --port 8000
        </p>
      ) : null}
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 rounded-lg border border-cyan/30 bg-cyan/10 px-3 py-1.5 text-xs font-medium text-cyan transition-colors hover:bg-cyan/20"
        >
          Retry
        </button>
      ) : null}
    </div>
  )
}

/**
 * The SSE stream dropped. Distinct from an error: the REST data is still valid, only
 * the live feed is stale. Spec §22 lists this as its own state.
 */
export function DisconnectedState({ status = 'disconnected', attempt = 0, onRetry, className = '' }) {
  const label =
    status === 'stale'
      ? 'Live feed went quiet — reconnecting'
      : status === 'reconnecting'
        ? `Reconnecting to the event stream${attempt ? ` (attempt ${attempt})` : ''}`
        : 'Live event stream disconnected'

  return (
    <div
      className={`flex items-center justify-between gap-3 rounded-xl border border-amber/25 bg-amber/[0.07] px-3 py-2 ${className}`}
      role="status"
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-amber" />
        <span className="truncate text-xs text-amber">{label}</span>
      </div>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded border border-amber/30 px-2 py-0.5 text-2xs font-medium text-amber transition-colors hover:bg-amber/15"
        >
          Reconnect now
        </button>
      ) : null}
    </div>
  )
}

/**
 * Chain-specific states. A pending or failed commitment must never make the page look
 * broken, so these are informational, not alarming.
 */
export function BlockchainPendingState({ detail, className = '' }) {
  return (
    <div
      className={`flex items-center gap-2.5 rounded-xl border border-amber/25 bg-amber/[0.07] px-3 py-2.5 ${className}`}
      role="status"
    >
      <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-amber" />
      <div className="min-w-0">
        <p className="text-xs font-medium text-amber">Commitment pending</p>
        {detail ? <p className="mt-0.5 text-2xs text-amber/80">{detail}</p> : null}
      </div>
    </div>
  )
}

export function BlockchainFailedState({ detail, onRetry, className = '' }) {
  return (
    <div
      className={`flex items-center justify-between gap-3 rounded-xl border border-red/25 bg-red/[0.07] px-3 py-2.5 ${className}`}
      role="status"
    >
      <div className="min-w-0">
        <p className="text-xs font-medium text-red">Commitment failed</p>
        {detail ? (
          <p className="mt-0.5 text-2xs leading-relaxed text-red/80">{detail}</p>
        ) : (
          <p className="mt-0.5 text-2xs text-red/80">
            Core TrustMesh enforcement is unaffected — only the commitment failed.
          </p>
        )}
      </div>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded border border-red/30 px-2 py-0.5 text-2xs font-medium text-red transition-colors hover:bg-red/15"
        >
          Re-check
        </button>
      ) : null}
    </div>
  )
}

export function BlockchainSimulatedState({ detail, className = '' }) {
  return (
    <div
      className={`flex items-center gap-2.5 rounded-xl border border-cyan/25 bg-cyan/[0.06] px-3 py-2.5 ${className}`}
      role="status"
    >
      <span className="h-2 w-2 shrink-0 rounded-full bg-cyan" />
      <div className="min-w-0">
        <p className="text-xs font-medium text-cyan">Simulated commitment</p>
        <p className="mt-0.5 text-2xs leading-relaxed text-cyan/75">
          {detail ||
            'No EVM RPC is configured, so this receipt was produced locally. The SHA-256 digest is still independently verifiable from the stored canonical JSON — it is simply not an on-chain confirmation.'}
        </p>
      </div>
    </div>
  )
}

export function BlockchainConfirmedState({ detail, className = '' }) {
  return (
    <div
      className={`flex items-center gap-2.5 rounded-xl border border-teal/25 bg-teal/[0.07] px-3 py-2.5 ${className}`}
      role="status"
    >
      <span className="h-2 w-2 shrink-0 rounded-full bg-teal" />
      <div className="min-w-0">
        <p className="text-xs font-medium text-teal">Confirmed on chain</p>
        {detail ? <p className="mt-0.5 text-2xs text-teal/75">{detail}</p> : null}
      </div>
    </div>
  )
}

/** Pick the right chain state component for a commitment status. */
export function CommitmentStateBanner({ status, detail, onRetry, className = '' }) {
  if (status === 'CONFIRMED') return <BlockchainConfirmedState detail={detail} className={className} />
  if (status === 'FAILED') return <BlockchainFailedState detail={detail} onRetry={onRetry} className={className} />
  if (status === 'SIMULATED') return <BlockchainSimulatedState detail={detail} className={className} />
  if (status === 'PENDING') return <BlockchainPendingState detail={detail} className={className} />
  return null
}

/**
 * Compose the standard section states.
 *
 * Renders the skeleton while loading, the error state on failure, the empty state when
 * there is genuinely nothing, and otherwise the children. Keeping the priority order in
 * one place is what makes the seven states reliably implemented across every section.
 */
export function StateGate({
  loading,
  error,
  isEmpty,
  onRetry,
  loadingFallback,
  emptyTitle,
  emptyDetail,
  emptyAction,
  errorTitle,
  className = '',
  children,
}) {
  if (loading) {
    return (
      <div className={className}>
        {loadingFallback || <SkeletonList rows={3} />}
      </div>
    )
  }
  if (error) {
    return (
      <GlassCard className={className}>
        <ErrorState error={error} title={errorTitle} onRetry={onRetry} />
      </GlassCard>
    )
  }
  if (isEmpty) {
    return (
      <GlassCard className={className}>
        <EmptyState title={emptyTitle} detail={emptyDetail} action={emptyAction} />
      </GlassCard>
    )
  }
  return <>{children}</>
}

export default StateGate
