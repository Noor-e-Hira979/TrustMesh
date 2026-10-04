/**
 * Glassmorphism surface primitives.
 *
 * The visual language from spec §14: deep navy/blue-black base, restrained borders and
 * glows, glassmorphism cards. Deliberately a small set — over-decorating is explicitly
 * called out as a failure mode in the spec.
 */

import { forwardRef } from 'react'

/** The standard glass card. `as` lets it render as a section/article when needed. */
export const GlassCard = forwardRef(function GlassCard(
  { as: Tag = 'div', className = '', children, interactive = false, glow = null, ...rest },
  ref,
) {
  const glowClass = glow ? `shadow-${glow}` : ''
  return (
    <Tag
      ref={ref}
      className={`glass ${glowClass} ${interactive ? 'glass-hover cursor-pointer' : ''} ${className}`}
      {...rest}
    >
      {children}
    </Tag>
  )
})

/** A lighter nested surface, for rows inside a card. */
export function GlassSubtle({ className = '', children, ...rest }) {
  return (
    <div className={`glass-subtle ${className}`} {...rest}>
      {children}
    </div>
  )
}

/** Section header with an eyebrow label, a title and an optional right-hand slot. */
export function CardHeader({ eyebrow, title, subtitle, right, className = '' }) {
  return (
    <div className={`flex items-start justify-between gap-4 ${className}`}>
      <div className="min-w-0">
        {eyebrow ? <div className="eyebrow mb-1.5">{eyebrow}</div> : null}
        <h2 className="truncate text-sm font-semibold text-ink">{title}</h2>
        {subtitle ? <p className="mt-1 text-xs leading-relaxed text-ink-faint">{subtitle}</p> : null}
      </div>
      {right ? <div className="shrink-0">{right}</div> : null}
    </div>
  )
}

/**
 * Status pill.
 *
 * `tone` is one of the tone keys in `TONE_CLASSES`; keeping the mapping in constants.js
 * means the same event always gets the same colour on every page.
 */
export function StatusPill({ tone = 'neutral', children, className = '', dot = true, title }) {
  const styles = {
    good: 'bg-teal/12 text-teal border-teal/30',
    bad: 'bg-red/12 text-red border-red/30',
    warn: 'bg-amber/12 text-amber border-amber/30',
    agent: 'bg-violet/12 text-violet border-violet/30',
    chain: 'bg-teal/12 text-teal border-teal/30',
    neutral: 'bg-white/5 text-ink-muted border-white/10',
    accent: 'bg-cyan/12 text-cyan border-cyan/30',
  }[tone] || 'bg-white/5 text-ink-muted border-white/10'

  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-2xs font-medium ${styles} ${className}`}
    >
      {dot ? <span className="h-1.5 w-1.5 rounded-full bg-current" /> : null}
      {children}
    </span>
  )
}

/** Larger pill for severity, used in the Security Center. */
export function SeverityBadge({ severity, className = '' }) {
  const styles = {
    CRITICAL: 'bg-red/15 text-red border-red/40',
    HIGH: 'bg-amber/15 text-amber border-amber/40',
    MEDIUM: 'bg-cyan/10 text-cyan border-cyan/30',
    LOW: 'bg-white/5 text-ink-muted border-white/10',
  }[severity] || 'bg-white/5 text-ink-muted border-white/10'

  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wider ${styles} ${className}`}
    >
      {severity || 'UNKNOWN'}
    </span>
  )
}

/** Layer chip for the three-layer model (spec §17). */
export function LayerBadge({ layer, label, question, className = '' }) {
  const styles = {
    AGENT: 'bg-violet/12 text-violet border-violet/30',
    TRUSTMESH: 'bg-cyan/12 text-cyan border-cyan/30',
    BLOCKCHAIN: 'bg-teal/12 text-teal border-teal/30',
    SYSTEM: 'bg-white/5 text-ink-muted border-white/10',
  }[layer] || 'bg-white/5 text-ink-muted border-white/10'

  return (
    <span
      title={question}
      className={`inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wider ${styles} ${className}`}
    >
      <span className="h-1 w-1 rounded-full bg-current" />
      {label || layer}
    </span>
  )
}

/** Headline number for the overview dashboard. */
export function StatCard({ label, value, detail, tone = 'neutral', className = '' }) {
  const valueTone = {
    good: 'text-teal',
    bad: 'text-red',
    warn: 'text-amber',
    accent: 'text-cyan',
    neutral: 'text-ink',
  }[tone] || 'text-ink'

  return (
    <GlassCard className={`p-4 ${className}`}>
      <div className="eyebrow truncate">{label}</div>
      <div className={`mt-2 font-mono text-2xl font-semibold tabular-nums ${valueTone}`}>{value}</div>
      {detail ? <div className="mt-1 text-2xs leading-relaxed text-ink-faint">{detail}</div> : null}
    </GlassCard>
  )
}

/** Component status row, as shown on the Overview status cards (§18). */
export function ComponentCard({ name, status, detail, mode, className = '' }) {
  const tone = status === 'up' ? 'good' : status === 'degraded' ? 'warn' : 'bad'
  const dot = {
    good: 'bg-teal',
    warn: 'bg-amber',
    bad: 'bg-red',
  }[tone]
  const glow = status === 'down' ? 'glow-red' : null

  return (
    <GlassCard className={`p-4 ${className}`} glow={glow}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className={`h-2 w-2 shrink-0 rounded-full ${dot}`} />
          <span className="truncate text-xs font-semibold text-ink">{name}</span>
        </div>
        <StatusPill tone={tone}>{status}</StatusPill>
      </div>
      <p className="mt-2 text-2xs leading-relaxed text-ink-faint">{detail}</p>
      {mode ? <div className="mt-1.5 font-mono text-2xs text-ink-faint/80">mode: {mode}</div> : null}
    </GlassCard>
  )
}

/** Monospace hash with a copy button. Copy failures degrade silently. */
export function HashChip({ value, label, lead = 8, tail = 6, className = '', onCopy }) {
  if (!value) return null
  const display =
    value.length <= lead + tail + 3 ? value : `${value.slice(0, lead + 2)}…${value.slice(-tail)}`

  return (
    <span
      className={`group inline-flex items-center gap-1.5 rounded border border-white/10 bg-base-900/60 px-1.5 py-0.5 ${className}`}
      title={value}
    >
      {label ? <span className="text-2xs uppercase tracking-wider text-ink-faint">{label}</span> : null}
      <code className="font-mono text-2xs text-ink-muted">{display}</code>
      <button
        type="button"
        onClick={() => {
          const text = value
          if (navigator?.clipboard?.writeText) {
            navigator.clipboard.writeText(text).catch(() => {})
          }
          onCopy?.(text)
        }}
        className="text-2xs text-ink-faint opacity-0 transition-opacity hover:text-cyan group-hover:opacity-100 focus-visible:opacity-100"
        aria-label="Copy value"
        title="Copy"
      >
        copy
      </button>
    </span>
  )
}

/** Key/value row used across the detail panels. */
export function DataRow({ label, value, mono = false, className = '' }) {
  return (
    <div className={`flex items-baseline justify-between gap-4 py-1.5 ${className}`}>
      <span className="shrink-0 text-2xs uppercase tracking-wider text-ink-faint">{label}</span>
      <span
        className={`min-w-0 truncate text-right text-xs text-ink-muted ${mono ? 'font-mono' : ''}`}
        title={typeof value === 'string' ? value : undefined}
      >
        {value ?? '—'}
      </span>
    </div>
  )
}

/** Collapsible JSON viewer for evidence and canonical agreements. */
export function JsonBlock({ value, label, defaultOpen = false, className = '' }) {
  if (!value) return null
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
  return (
    <details open={defaultOpen} className={`group ${className}`}>
      <summary className="cursor-pointer list-none text-2xs uppercase tracking-wider text-ink-faint transition-colors hover:text-cyan">
        <span className="mr-1.5 inline-block transition-transform group-open:rotate-90">▸</span>
        {label}
      </summary>
      <pre className="mt-2 max-h-72 overflow-auto rounded-lg border border-white/[0.06] bg-base-900/70 p-3 font-mono text-2xs leading-relaxed text-ink-muted">
        {text}
      </pre>
    </details>
  )
}

export default GlassCard
