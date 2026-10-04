/**
 * Agent Network graph (spec §16) — the project's visual signature.
 *
 * Rendered as SVG with a deterministic layout from `useNetworkGraph`, so the picture is
 * stable across refreshes while still responding to live events. Node and edge visual
 * states map directly onto the spec:
 *
 *   normal communication → normal connection
 *   verified identity     → teal node ring
 *   negotiation           → cyan animated edge
 *   blocked request       → red edge
 *   identity mismatch     → amber dashed edge
 *
 * No physics library and no animation loop driving positions: reliability over spectacle.
 */

import { useMemo, useState } from 'react'
import { EDGE_STATES } from '../../lib/constants'
// formatUsd is required by AgentInspector: it renders monetary constraints such as
// max_budget_cents. Omitting it threw a ReferenceError on selecting any agent that has
// a cents-denominated constraint, which is every seeded buyer and supplier.
import { formatUsd, humanize } from '../../lib/format'
import { GlassCard, StatusPill } from '../ui/Primitives'

const TONE_NODE = {
  good: { stroke: '#2DD4BF', fill: 'rgba(45,212,191,0.14)', text: '#2DD4BF' },
  bad: { stroke: '#F43F5E', fill: 'rgba(244,63,94,0.14)', text: '#F43F5E' },
  warn: { stroke: '#F59E0B', fill: 'rgba(245,158,11,0.14)', text: '#F59E0B' },
  chain: { stroke: '#22D3EE', fill: 'rgba(34,211,238,0.14)', text: '#22D3EE' },
  neutral: { stroke: '#334155', fill: 'rgba(148,163,184,0.08)', text: '#94A3B8' },
}

const LEGEND = [
  { state: 'verified', label: 'Identity verified', color: EDGE_STATES.verified.color },
  { state: 'negotiating', label: 'Negotiating', color: EDGE_STATES.negotiating.color },
  { state: 'blocked', label: 'Blocked request', color: EDGE_STATES.blocked.color },
  { state: 'rejected', label: 'Identity mismatch', color: EDGE_STATES.rejected.color },
  { state: 'committed', label: 'Committed', color: EDGE_STATES.committed.color },
]

export function AgentNetworkGraph({ graph, className = '' }) {
  const { nodes, edges, width, height, selected, setSelectedId, hoveredId, setHoveredId, nodeToneFor } =
    graph
  const [zoom, setZoom] = useState(1)

  const nodeById = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes])

  if (nodes.length === 0) return null

  return (
    <div className={`relative ${className}`}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-auto w-full select-none"
        role="img"
        aria-label="Agent network graph showing agent identity, verification status and interaction state"
        style={{ transform: `scale(${zoom})`, transformOrigin: 'center' }}
      >
        <defs>
          <radialGradient id="hubGlow">
            <stop offset="0%" stopColor="#22D3EE" stopOpacity="0.28" />
            <stop offset="100%" stopColor="#22D3EE" stopOpacity="0" />
          </radialGradient>
          <marker
            id="arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="5"
            markerHeight="5"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#334155" />
          </marker>
        </defs>

        {/* Edges first so nodes always sit on top. */}
        {edges.map((edge) => {
          const source = nodeById.get(edge.source)
          const target = nodeById.get(edge.target)
          if (!source || !target) return null

          const style = EDGE_STATES[edge.state] || EDGE_STATES.idle
          const isActive = ['negotiating', 'blocked', 'rejected'].includes(edge.state)
          const isHovered = hoveredId === edge.source || hoveredId === edge.target

          // Trim the line so it stops at the node border rather than the centre.
          const dx = target.x - source.x
          const dy = target.y - source.y
          const dist = Math.max(1, Math.hypot(dx, dy))
          const pad = 30
          const x1 = source.x + (dx / dist) * pad
          const y1 = source.y + (dy / dist) * pad
          const x2 = target.x - (dx / dist) * pad
          const y2 = target.y - (dy / dist) * pad

          return (
            <g key={edge.id}>
              <line
                x1={x1}
                y1={y1}
                x2={x2}
                y2={y2}
                stroke={style.color}
                strokeWidth={isHovered ? style.width + 1.25 : style.width}
                strokeOpacity={isActive ? 0.95 : 0.45}
                strokeDasharray={style.dash || undefined}
                strokeLinecap="round"
                markerEnd="url(#arrow)"
              >
                {isActive ? (
                  <animate
                    attributeName="stroke-dashoffset"
                    from="32"
                    to="0"
                    dur="0.9s"
                    repeatCount="indefinite"
                  />
                ) : null}
              </line>
              <title>{`${source.label} ↔ ${target.label} — ${style.label}`}</title>
            </g>
          )
        })}

        {/* Nodes */}
        {nodes.map((node) => {
          const tone = TONE_NODE[nodeToneFor(node)] || TONE_NODE.neutral
          const isHub = node.hub
          const radius = isHub ? 30 : 24
          const isSelected = selected?.id === node.id
          const isHovered = hoveredId === node.id

          return (
            <g
              key={node.id}
              transform={`translate(${node.x} ${node.y})`}
              className="cursor-pointer"
              onClick={() => setSelectedId(isSelected ? null : node.id)}
              onMouseEnter={() => setHoveredId(node.id)}
              onMouseLeave={() => setHoveredId(null)}
              role="button"
              tabIndex={0}
              aria-label={`${node.label}, ${node.typeLabel}, ${node.verification}`}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  setSelectedId(isSelected ? null : node.id)
                }
              }}
            >
              {isHub ? <circle r="56" fill="url(#hubGlow)" /> : null}

              {/* Verification ring */}
              <circle
                r={radius + (node.verification === 'verified' ? 5 : 0)}
                fill="none"
                stroke={tone.stroke}
                strokeWidth={node.verification === 'verified' ? 1.5 : 0}
                strokeOpacity={0.5}
                strokeDasharray="3 3"
              />

              <circle
                r={radius}
                fill={tone.fill}
                stroke={tone.stroke}
                strokeWidth={isSelected || isHovered ? 2 : 1.25}
                strokeOpacity={isSelected ? 1 : 0.7}
              />

              {/* Unregistered impostor gets a hard dashed outline. */}
              {!node.registered ? (
                <circle
                  r={radius + 8}
                  fill="none"
                  stroke="#F59E0B"
                  strokeWidth="1.25"
                  strokeDasharray="2 4"
                  strokeOpacity="0.8"
                />
              ) : null}

              <text
                textAnchor="middle"
                dy="4"
                className="pointer-events-none"
                style={{ fontSize: 10, fontWeight: 700, fill: tone.text }}
              >
                {node.type === 'buyer' ? 'BUYER' : node.type === 'supplier' ? 'SUP' : '???'}
              </text>

              <text
                textAnchor="middle"
                y={radius + 15}
                className="pointer-events-none"
                style={{ fontSize: 10, fill: '#94A3B8' }}
              >
                {node.label.length > 20 ? `${node.label.slice(0, 19)}…` : node.label}
              </text>
              <text
                textAnchor="middle"
                y={radius + 27}
                className="pointer-events-none"
                style={{ fontSize: 9, fill: '#64748B' }}
              >
                {node.orgId}
              </text>

              <title>
                {`${node.label}\norg: ${node.orgId}\ntype: ${node.typeLabel}\nverification: ${node.verification}\nstatus: ${node.status}\ncapabilities: ${node.capabilities.join(', ') || 'none'}\nactivity: ${node.activity || 'idle'}`}
              </title>
            </g>
          )
        })}
      </svg>

      {/* Legend */}
      <div className="pointer-events-none absolute left-3 top-3 flex flex-col gap-1 rounded-lg border border-white/[0.06] bg-base-900/70 px-2.5 py-2 backdrop-blur-md">
        {LEGEND.map((item) => (
          <div key={item.state} className="flex items-center gap-2">
            <span className="h-0.5 w-5 rounded-full" style={{ background: item.color }} />
            <span className="text-2xs text-ink-faint">{item.label}</span>
          </div>
        ))}
      </div>

      {/* Zoom control */}
      <div className="absolute right-3 top-3 flex flex-col gap-1">
        {[
          { label: '+', value: Math.min(1.6, zoom + 0.15) },
          { label: '−', value: Math.max(0.7, zoom - 0.15) },
        ].map((control) => (
          <button
            key={control.label}
            type="button"
            onClick={() => setZoom(control.value)}
            className="h-7 w-7 rounded-lg border border-white/[0.08] bg-base-900/70 text-ink-faint backdrop-blur-md transition-colors hover:border-cyan/30 hover:text-cyan"
            aria-label={control.label === '+' ? 'Zoom in' : 'Zoom out'}
          >
            {control.label}
          </button>
        ))}
      </div>
    </div>
  )
}

/** Detail panel for the selected node. */
export function AgentInspector({ node, className = '' }) {
  if (!node) {
    return (
      <GlassCard className={`p-5 ${className}`}>
        <p className="text-xs text-ink-faint">
          Select a node to inspect its identity, capabilities and current activity.
        </p>
      </GlassCard>
    )
  }

  return (
    <GlassCard className={`p-4 ${className}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-ink">{node.label}</h3>
          <p className="mt-0.5 font-mono text-2xs text-ink-faint">{node.id}</p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <StatusPill tone={node.verification === 'verified' ? 'good' : node.verification === 'rejected' ? 'bad' : 'neutral'}>
            {humanize(node.verification)}
          </StatusPill>
          {node.isSimulation ? <StatusPill tone="warn">simulation</StatusPill> : null}
          {!node.registered ? <StatusPill tone="bad">unregistered</StatusPill> : null}
        </div>
      </div>

      <dl className="mt-3 space-y-1.5 text-xs">
        <div className="flex justify-between gap-3">
          <dt className="text-ink-faint">Organization</dt>
          <dd className="font-mono text-ink-muted">{node.orgId}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-ink-faint">Type</dt>
          <dd className="text-ink-muted">{node.typeLabel}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-ink-faint">Status</dt>
          <dd className="text-ink-muted">{humanize(node.status)}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-ink-faint">Last event</dt>
          <dd className="font-mono text-ink-muted">{node.lastEvent || '—'}</dd>
        </div>
      </dl>

      <div className="mt-3">
        <div className="eyebrow mb-1.5">Declared capabilities</div>
        {node.capabilities.length === 0 ? (
          <p className="text-2xs text-red">none — this agent holds no capability grants</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {node.capabilities.map((capability) => (
              <span
                key={capability}
                className="rounded border border-cyan/25 bg-cyan/[0.07] px-1.5 py-0.5 font-mono text-2xs text-cyan"
              >
                {capability}
              </span>
            ))}
          </div>
        )}
      </div>

      {Object.keys(node.constraints || {}).length > 0 ? (
        <div className="mt-3">
          <div className="eyebrow mb-1.5">Authority constraints</div>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(node.constraints).map(([key, value]) => (
              <span
                key={key}
                className="rounded border border-white/10 bg-white/[0.04] px-1.5 py-0.5 font-mono text-2xs text-ink-muted"
              >
                {key.replace(/_/g, ' ')}:{' '}
                {key.toLowerCase().includes('cents') ? formatUsd(value) : String(value)}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      <div className="mt-3">
        <div className="eyebrow mb-1.5">Current activity</div>
        <p className="text-xs leading-relaxed text-ink-muted">{node.activity || 'idle'}</p>
      </div>
    </GlassCard>
  )
}

export default AgentNetworkGraph
