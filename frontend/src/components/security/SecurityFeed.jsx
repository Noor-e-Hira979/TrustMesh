/**
 * Security alert card + the Security Center stream (spec §20).
 *
 * Each alert shows severity, agent, organization, the requested action, the decision,
 * the reason and the timestamp — exactly the seven fields the spec names — with the
 * evidence available on expand.
 */

import { useState } from 'react'
import { formatRelative, formatTime, humanize, truncateId } from '../../lib/format'
import { GlassCard, JsonBlock, SeverityBadge, StatusPill } from '../ui/Primitives'
import { EmptyState, SkeletonList, StateGate } from '../ui/States'

export function AlertCard({ alert, onStatusChange, pending = false, className = '' }) {
  const [expanded, setExpanded] = useState(false)

  const tone = alert.severity === 'CRITICAL' ? 'bad' : alert.severity === 'HIGH' ? 'warn' : 'neutral'
  const glow = alert.severity === 'CRITICAL' ? 'glow-red' : null

  return (
    <GlassCard
      className={`overflow-hidden transition-colors ${className}`}
      glow={glow}
    >
      {/* Severity stripe */}
      <div
        className={`h-0.5 w-full ${
          alert.severity === 'CRITICAL'
            ? 'bg-red'
            : alert.severity === 'HIGH'
              ? 'bg-amber'
              : alert.severity === 'MEDIUM'
                ? 'bg-cyan'
                : 'bg-ink-faint'
        }`}
      />

      <div className="p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <SeverityBadge severity={alert.severity} />
              <StatusPill tone="bad">{alert.decision || 'BLOCKED'}</StatusPill>
              <span className="truncate font-mono text-2xs text-ink-faint">
                {humanize(alert.alert_type)}
              </span>
              {alert.is_simulation ? (
                <StatusPill tone="warn" title="Produced by a controlled simulation">
                  simulated
                </StatusPill>
              ) : null}
            </div>

            <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="text-sm font-medium text-ink">
                {alert.agent_name || alert.agent_id}
              </span>
              <span className="text-2xs text-ink-faint">
                {alert.organization_name || alert.organization_id || 'unknown organization'}
              </span>
            </div>
          </div>

          <div className="shrink-0 text-right">
            <time
              className="block font-mono text-2xs tabular-nums text-ink-faint"
              dateTime={alert.ts}
              title={formatTime(alert.ts, { withDate: true })}
            >
              {formatTime(alert.ts)}
            </time>
            <span className="mt-0.5 block text-2xs text-ink-faint/60">
              {formatRelative(alert.ts)}
            </span>
          </div>
        </div>

        {/* The requested action — the thing that was refused. */}
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          <div className="glass-subtle px-3 py-2">
            <div className="eyebrow">Requested action</div>
            <div className="mt-1 truncate font-mono text-xs text-ink-muted" title={alert.requested_action}>
              {alert.requested_action || '—'}
            </div>
          </div>
          <div className="glass-subtle px-3 py-2">
            <div className="eyebrow">Requested capability</div>
            <div
              className="mt-1 truncate font-mono text-xs text-ink-muted"
              title={alert.requested_capability}
            >
              {alert.requested_capability || '—'}
            </div>
          </div>
        </div>

        <p className="mt-3 text-xs leading-relaxed text-ink-muted">{alert.reason}</p>

        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone={tone} dot={false}>
              stage {alert.stage}
            </StatusPill>
            {alert.policy_rule_id ? (
              <span className="font-mono text-2xs text-ink-faint">{alert.policy_rule_id}</span>
            ) : null}
            {alert.transaction_id ? (
              <span className="font-mono text-2xs text-ink-faint/70">
                {truncateId(alert.transaction_id)}
              </span>
            ) : null}
            {alert.status && alert.status !== 'OPEN' ? (
              <StatusPill tone="neutral">{humanize(alert.status)}</StatusPill>
            ) : null}
          </div>

          <div className="flex items-center gap-2">
            {onStatusChange && alert.status === 'OPEN' ? (
              <button
                type="button"
                disabled={pending}
                onClick={() => onStatusChange(alert.id, 'ACKNOWLEDGED')}
                className="rounded border border-white/10 px-2 py-0.5 text-2xs text-ink-faint transition-colors hover:border-cyan/30 hover:text-cyan disabled:opacity-40"
              >
                Acknowledge
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="rounded border border-white/10 px-2 py-0.5 text-2xs text-ink-faint transition-colors hover:border-cyan/30 hover:text-cyan"
              aria-expanded={expanded}
            >
              {expanded ? 'Hide evidence' : 'Evidence'}
            </button>
          </div>
        </div>

        {expanded ? (
          <div className="mt-3 space-y-2 border-t border-white/[0.06] pt-3">
            <div className="grid gap-2 text-2xs sm:grid-cols-2">
              <div>
                <span className="text-ink-faint">alert id: </span>
                <span className="font-mono text-ink-muted">{alert.id}</span>
              </div>
              <div>
                <span className="text-ink-faint">rule: </span>
                <span className="font-mono text-ink-muted">{alert.policy_rule_id || '—'}</span>
              </div>
            </div>
            <JsonBlock value={alert.evidence} label="Decision evidence" />
          </div>
        ) : null}
      </div>
    </GlassCard>
  )
}

/**
 * The Security Center stream. Newest first, fed from the live event state so an alert
 * appears the instant the Gateway blocks something.
 */
export function SecurityFeed({
  alerts = [],
  loading = false,
  error = null,
  onRetry,
  onStatusChange,
  pendingStatus = false,
  className = '',
}) {
  return (
    <StateGate
      loading={loading}
      error={error}
      isEmpty={alerts.length === 0}
      onRetry={onRetry}
      loadingFallback={<SkeletonList rows={4} />}
      emptyTitle="No security events"
      emptyDetail="When TrustMesh blocks an action, the alert appears here instantly. Run the demonstration or trigger a scenario."
      className={className}
    >
      <div className="space-y-3">
        {alerts.map((alert) => (
          <AlertCard
            key={alert.id}
            alert={alert}
            onStatusChange={onStatusChange}
            pending={pendingStatus}
          />
        ))}
      </div>
    </StateGate>
  )
}

export default SecurityFeed
