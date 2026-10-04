/**
 * Transaction lifecycle timeline with the three-layer attribution of spec §17.
 *
 * Each step states which authority spoke:
 *   AGENT      — what the AI wanted or proposed
 *   TRUSTMESH  — whether it was authorized and policy-compliant
 *   BLOCKCHAIN — what was actually recorded
 *
 * That distinction is the whole product in one component, so it is rendered explicitly
 * rather than inferred from colour alone.
 */

import { LAYER_CLASSES, LAYER_META } from '../../lib/constants'
import { formatTime, formatUsd } from '../../lib/format'
import { GlassCard, JsonBlock, LayerBadge, StatusPill } from '../ui/Primitives'
import { EmptyState } from '../ui/States'

const OUTCOME_STYLES = {
  INFO: { text: 'text-ink-muted', dot: 'bg-ink-faint', ring: '' },
  ALLOW: { text: 'text-teal', dot: 'bg-teal', ring: 'ring-teal/25' },
  BLOCK: { text: 'text-red', dot: 'bg-red', ring: 'ring-red/25' },
  SUCCESS: { text: 'text-teal', dot: 'bg-teal', ring: 'ring-teal/25' },
  WARNING: { text: 'text-amber', dot: 'bg-amber', ring: 'ring-amber/25' },
  ERROR: { text: 'text-red', dot: 'bg-red', ring: 'ring-red/25' },
}

function StepIcon({ outcome }) {
  const style = OUTCOME_STYLES[outcome] || OUTCOME_STYLES.INFO
  const glyph = {
    ALLOW: '✓',
    SUCCESS: '✓',
    BLOCK: '✕',
    ERROR: '✕',
    WARNING: '!',
    INFO: '·',
  }[outcome] || '·'

  return (
    <span
      className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-white/10 bg-base-900 text-[10px] font-bold ${style.text} ${style.ring}`}
      aria-hidden="true"
    >
      {glyph}
    </span>
  )
}

export function TimelineStep({ step, isLast }) {
  const layer = LAYER_CLASSES[step.layer] || LAYER_CLASSES.SYSTEM
  const style = OUTCOME_STYLES[step.outcome] || OUTCOME_STYLES.INFO

  return (
    <li className="relative flex gap-3 pb-4 last:pb-0">
      {/* Connector line */}
      {!isLast ? (
        <span className="absolute left-3 top-7 h-full w-px bg-white/[0.07]" aria-hidden="true" />
      ) : null}

      <StepIcon outcome={step.outcome} />

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className={`text-xs font-semibold ${style.text}`}>{step.title}</span>
          <LayerBadge
            layer={step.layer}
            label={LAYER_META[step.layer]?.label || step.layer}
            question={LAYER_META[step.layer]?.question}
          />
          <time
            className="font-mono text-2xs tabular-nums text-ink-faint/70"
            dateTime={step.ts}
          >
            {formatTime(step.ts)}
          </time>
        </div>

        <p className="mt-1 text-xs leading-relaxed text-ink-faint">{step.detail}</p>

        {/* Offer economics, when this step carries an offer. */}
        {step.meta?.price_cents !== undefined ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <span className="font-mono text-2xs text-ink-muted">
              {formatUsd(step.meta.price_cents)}
            </span>
            {step.meta.delivery_days !== undefined ? (
              <span className="text-2xs text-ink-faint">{step.meta.delivery_days} days</span>
            ) : null}
            {step.meta.round ? (
              <span className="text-2xs text-ink-faint">round {step.meta.round}</span>
            ) : null}
          </div>
        ) : null}

        {/* Why the Policy Engine said no, attributed to the rule. */}
        {step.meta?.policy_reason ? (
          <div className="mt-2 rounded-lg border border-red/20 bg-red/[0.06] px-2.5 py-1.5">
            <div className="flex items-center gap-1.5">
              <StatusPill tone="bad">TrustMesh blocked</StatusPill>
              {step.meta.policy_rule_id ? (
                <span className="font-mono text-2xs text-red/80">{step.meta.policy_rule_id}</span>
              ) : null}
            </div>
            <p className="mt-1 text-2xs leading-relaxed text-red/85">{step.meta.policy_reason}</p>
          </div>
        ) : null}

        {step.meta?.canonicalization ? (
          <div className="mt-2 rounded-lg border border-teal/20 bg-teal/[0.05] px-2.5 py-1.5">
            <p className="text-2xs text-teal/85">
              {step.meta.canonicalization} → {step.meta.algorithm}
            </p>
            <JsonBlock value={step.meta.canonical_json} label="Canonical JSON" />
          </div>
        ) : null}

        {step.meta?.explorer_url ? (
          <a
            href={step.meta.explorer_url}
            target="_blank"
            rel="noreferrer noopener"
            className="mt-1.5 inline-block font-mono text-2xs text-cyan underline-offset-2 hover:underline"
          >
            View on explorer ↗
          </a>
        ) : null}
      </div>
    </li>
  )
}

/** Grouped by layer, so the three authorities are readable as columns of intent. */
export function LayerSummary({ steps = [] }) {
  const groups = ['AGENT', 'TRUSTMESH', 'BLOCKCHAIN']
    .map((layer) => {
      const items = steps.filter((s) => s.layer === layer)
      return {
        layer,
        meta: LAYER_META[layer],
        items,
        blocks: items.filter((s) => s.outcome === 'BLOCK' || s.outcome === 'ERROR').length,
      }
    })
    .filter((group) => group.items.length > 0)

  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {groups.map((group) => {
        const style = LAYER_CLASSES[group.layer] || LAYER_CLASSES.SYSTEM
        return (
          <GlassCard key={group.layer} className={`p-3 ${style.border}`}>
            <div className="flex items-center justify-between gap-2">
              <LayerBadge layer={group.layer} label={group.meta.label} question={group.meta.question} />
              <span className="font-mono text-2xs text-ink-faint">{group.items.length}</span>
            </div>
            <p className="mt-2 text-2xs leading-relaxed text-ink-faint">{group.meta.question}</p>
            {group.blocks > 0 ? (
              <p className="mt-1.5 font-mono text-2xs text-red">
                {group.blocks} block{group.blocks === 1 ? '' : 's'}
              </p>
            ) : null}
          </GlassCard>
        )
      })}
    </div>
  )
}

export function Timeline({ steps = [], className = '' }) {
  if (!steps || steps.length === 0) {
    return (
      <GlassCard className={className}>
        <EmptyState
          title="No lifecycle recorded yet"
          detail="Run the demonstration and the full intent → commitment sequence appears here."
        />
      </GlassCard>
    )
  }

  return (
    <div className={className}>
      <LayerSummary steps={steps} />
      <GlassCard className="mt-3 p-4">
        <ol className="mt-1">
          {steps.map((step, index) => (
            <TimelineStep key={step.seq || index} step={step} isLast={index === steps.length - 1} />
          ))}
        </ol>
      </GlassCard>
    </div>
  )
}

export default Timeline
