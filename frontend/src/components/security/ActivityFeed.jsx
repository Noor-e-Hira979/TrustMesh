/**
 * Live activity feed driven by the SSE stream.
 *
 * Coloured by the `layer` the backend attaches to every frame, which is what makes the
 * three-authority split visible while the demo runs: violet for what the AI proposes,
 * cyan for what TrustMesh decided, teal for what the chain recorded.
 */

import { useState } from 'react'
import { EVENT_META, LAYER_CLASSES, LAYER_META, TONE_CLASSES } from '../../lib/constants'
import { firstSentence, formatRelative, formatTime } from '../../lib/format'
import { GlassCard, LayerBadge, StatusPill } from '../ui/Primitives'
import { EmptyState } from '../ui/States'

function frameSummary(frame) {
  const p = frame.payload || {}
  switch (frame.type) {
    case 'INTENT_RECEIVED':
      return p.intent
        ? `${p.intent.quantity?.toLocaleString?.() ?? p.intent.quantity} × ${p.intent.product} · budget ≤ $${((p.intent.max_budget_cents || 0) / 100).toLocaleString()}`
        : 'Procurement intent accepted'
    case 'AGENT_DISCOVERED':
      return `Discovered ${p.discovered_agent_name || p.discovered_agent_id || 'counterparty'}`
    case 'IDENTITY_VERIFIED':
      return `${p.organization_name || p.organization_id || 'organization'} binding confirmed`
    case 'IDENTITY_REJECTED':
      return p.reason || 'Identity claim rejected'
    case 'CAPABILITY_CHECKED':
      return `${p.allowed ? 'Authorized' : 'Not authorized'}: ${p.requested_capability || 'capability'}`
    case 'OFFER_RECEIVED':
    case 'OFFER_UPDATED':
      return p.offer
        ? `$${(p.offer.price_cents / 100).toLocaleString()} · ${p.offer.delivery_days}d · ${p.offer.certification}`
        : 'Offer recorded'
    case 'NEGOTIATION_STARTED':
      return p.feasible_band_cents
        ? `Feasible band $${(p.feasible_band_cents[0] / 100).toLocaleString()} – $${(p.feasible_band_cents[1] / 100).toLocaleString()}`
        : 'Negotiation opened'
    case 'POLICY_CHECK_STARTED':
      return p.stage ? `Stage: ${p.stage}` : 'Policy evaluation started'
    case 'POLICY_ALLOWED':
      return p.reason || 'Action allowed'
    case 'POLICY_BLOCKED':
      return p.reason || `Blocked by ${p.rule_id}`
    case 'SECURITY_ALERT':
      return p.reason || `${p.alert_type} on ${p.requested_capability || 'request'}`
    case 'AGREEMENT_CREATED':
      return 'Final agreement frozen'
    case 'HASH_GENERATED':
      return p.agreement_hash || 'SHA-256 digest produced'
    case 'BLOCKCHAIN_SUBMITTED':
      return `Submitted · ${p.status}`
    case 'BLOCKCHAIN_CONFIRMED':
      return `Confirmed in block ${p.block_number ?? '—'}`
    case 'BLOCKCHAIN_FAILED':
      return p.error || 'Commitment failed'
    case 'AGENT_ACTION_EXECUTED':
      return `${p.action || 'action'} — ${p.result ? 'executed' : p.why || 'done'}`
    case 'TRANSACTION_STATE_CHANGED':
      return `${p.state}${p.note ? ` · ${p.note}` : ''}`
    case 'AI_REASONING':
      return firstSentence(p.rationale, 110)
    case 'TRUST_EVALUATED':
      return `Tier ${p.tier}`
    case 'SYSTEM_READY':
      return p.version ? `Control plane v${p.version} ready` : 'Control plane ready'
    case 'BUFFER_OVERFLOW':
      return p.message || 'Subscriber fell behind'
    default:
      return p.message || p.reason || ''
  }
}

export function EventRow({ frame, dense = false }) {
  const meta = EVENT_META[frame.type] || { label: frame.type, tone: 'neutral' }
  const tone = TONE_CLASSES[meta.tone] || TONE_CLASSES.neutral
  const layer = LAYER_CLASSES[frame.layer] || LAYER_CLASSES.SYSTEM
  const summary = frameSummary(frame)

  return (
    <div
      className={`group flex items-start gap-3 border-l-2 px-3 py-2 transition-colors hover:bg-white/[0.03] ${layer.border} ${
        dense ? 'text-2xs' : ''
      } ${frame.isNew ? 'animate-fade-in' : ''}`}
    >
      {/* Layer dot — the fastest read of "who spoke". */}
      <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${layer.dot}`} />

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className={`text-xs font-medium ${tone.text}`}>{meta.label}</span>
          {frame.agent_id ? (
            <span className="font-mono text-2xs text-ink-faint">{frame.agent_id}</span>
          ) : null}
          {frame.transaction_id ? (
            <span className="font-mono text-2xs text-ink-faint/70">
              {frame.transaction_id.slice(0, 12)}
            </span>
          ) : null}
        </div>
        {summary ? (
          <p className="mt-0.5 line-clamp-2 text-2xs leading-relaxed text-ink-faint">{summary}</p>
        ) : null}
      </div>

      <div className="flex shrink-0 flex-col items-end gap-1">
        <time
          className="font-mono text-2xs tabular-nums text-ink-faint/70"
          dateTime={frame.ts}
          title={formatTime(frame.ts, { withDate: true })}
        >
          {formatTime(frame.ts)}
        </time>
        <span className="font-mono text-2xs text-ink-faint/50">#{frame.seq}</span>
      </div>
    </div>
  )
}

/**
 * The feed, with a layer filter. Renders its own empty state so a page does not have to.
 */
export function ActivityFeed({ frames = [], limit = 60, showFilters = true, className = '' }) {
  const [filter, setFilter] = useState('ALL')
  const visible = frames.slice(0, limit)

  return (
    <GlassCard className={`flex min-h-0 flex-col ${className}`}>
      <div className="flex items-center justify-between gap-3 border-b border-white/[0.06] px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="eyebrow">Live activity</span>
          <span className="rounded-full bg-white/[0.06] px-1.5 py-0.5 font-mono text-2xs text-ink-faint">
            {frames.length}
          </span>
        </div>
        {showFilters ? (
          <div className="flex shrink-0 gap-1">
            {['ALL', 'AGENT', 'TRUSTMESH', 'BLOCKCHAIN'].map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setFilter(option)}
                className={`rounded px-1.5 py-0.5 text-2xs font-medium transition-colors ${
                  filter === option
                    ? 'bg-cyan/15 text-cyan'
                    : 'text-ink-faint hover:bg-white/[0.05] hover:text-ink-muted'
                }`}
                title={option === 'ALL' ? 'All layers' : LAYER_META[option]?.question}
              >
                {option === 'ALL' ? 'all' : option.slice(0, 4).toLowerCase()}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {visible.length === 0 ? (
          <EmptyState
            title="No events yet"
            detail="Run the demonstration and every step will stream in here as it happens."
          />
        ) : (
          visible
            .filter((frame) => filter === 'ALL' || frame.layer === filter)
            .map((frame) => <EventRow key={frame.key || frame.seq} frame={frame} />)
        )}
      </div>

      <div className="flex items-center justify-between gap-2 border-t border-white/[0.06] px-4 py-2">
        <div className="flex items-center gap-2">
          {['AGENT', 'TRUSTMESH', 'BLOCKCHAIN'].map((layer) => (
            <LayerBadge key={layer} layer={layer} label={LAYER_META[layer].label} question={LAYER_META[layer].question} />
          ))}
        </div>
        <span className="font-mono text-2xs text-ink-faint/60">
          {frames.length > limit ? `showing ${limit}/${frames.length}` : `${frames.length} frames`}
        </span>
      </div>
    </GlassCard>
  )
}

export default ActivityFeed
