/**
 * Agent Network graph model + layout (spec §16).
 *
 * "Keep the graph visually impressive but technically reliable" — so the layout is a
 * **deterministic** force simulation: seeded initial positions, a fixed iteration count
 * and no wall-clock animation loop driving positions. The same data always produces the
 * same picture, which means a judge can look at it twice and see the same thing, and a
 * refresh mid-demo does not reshuffle the graph.
 *
 * Interaction affordances (hover, select, zoom) are handled in the component; this hook
 * only owns the topology, the layout and the live edge state.
 */

import { useCallback, useMemo, useState } from 'react'
import { AGENT_TYPE_LABELS, EDGE_STATES } from '../lib/constants'
import { clamp } from '../lib/format'
import { useGlobalEvents } from '../state/GlobalEventContext'

/**
 * Deterministic pseudo-random in [0, 1) from an integer seed.
 * Used only to break perfect symmetry in the initial ring, so the layout converges
 * identically on every run.
 */
function seededFraction(seed) {
  const x = Math.sin(seed * 12.9898) * 43758.5453
  return x - Math.floor(x)
}

/**
 * Build the node set.
 *
 * Registered agents come from `/api/agents`; the registry catalogue additionally
 * contains the unregistered impostor, which must still render as a rejected node for
 * spec §5 SCENARIO 3 to be visible.
 */
export function buildNodes(agents = [], catalogue = []) {
  const catalogueById = new Map(catalogue.map((c) => [c.id, c]))
  const seen = new Set()
  const nodes = []

  agents.forEach((agent) => {
    seen.add(agent.id)
    const meta = catalogueById.get(agent.id)
    nodes.push({
      id: agent.id,
      label: agent.display_name || agent.id,
      orgId: agent.org_id,
      orgName: meta?.org_id || agent.org_id,
      type: agent.agent_type,
      typeLabel: AGENT_TYPE_LABELS[agent.agent_type] || agent.agent_type,
      capabilities: Array.isArray(agent.capabilities) ? agent.capabilities : [],
      constraints: agent.constraints || {},
      verification: agent.verification_status,
      status: agent.status,
      isSimulation: Boolean(agent.is_malicious_simulation),
      registered: true,
      activity: agent.current_activity,
      // Buyer is the hub; suppliers ring it.
      hub: agent.agent_type === 'buyer',
    })
  })

  catalogue.forEach((entry) => {
    if (seen.has(entry.id)) return
    nodes.push({
      id: entry.id,
      label: entry.display_name || entry.id,
      orgId: entry.org_id,
      orgName: entry.org_id,
      type: entry.agent_type,
      typeLabel: AGENT_TYPE_LABELS[entry.agent_type] || entry.agent_type,
      capabilities: Array.isArray(entry.capabilities) ? entry.capabilities : [],
      constraints: entry.constraints || {},
      verification: 'rejected',
      status: 'offline',
      isSimulation: Boolean(entry.is_malicious_simulation),
      registered: Boolean(entry.registered),
      activity: entry.registered ? 'registered' : 'unregistered — claims an identity',
      hub: false,
    })
  })

  return nodes
}

/** Buyer-to-counterparty topology. Everything radiates from the buyer. */
export function buildEdges(nodes) {
  const buyer = nodes.find((n) => n.hub) || nodes[0]
  if (!buyer) return []
  return nodes
    .filter((n) => n.id !== buyer.id)
    .map((n) => ({
      id: `${buyer.id}->${n.id}`,
      source: buyer.id,
      target: n.id,
      state: 'idle',
      ts: null,
    }))
}

/**
 * Deterministic force-directed layout.
 *
 * Repulsion between every pair plus a spring on each edge, a mild pull toward the
 * centre, and the buyer pinned to the middle so the story always reads left-to-right
 * outwards. Fixed iteration count: no convergence loop, no flakiness.
 *
 * @returns {{nodes: Array, width: number, height: number}}
 */
export function layout(nodes, edges, { width = 900, height = 560, iterations = 320 } = {}) {
  if (nodes.length === 0) return { nodes: [], width, height }

  const cx = width / 2
  const cy = height / 2
  const radius = Math.min(width, height) * 0.36

  const positions = nodes.map((node, index) => {
    if (node.hub) return { id: node.id, x: cx, y: cy, vx: 0, vy: 0, pinned: true }
    // Deterministic ring placement, angle offset by seed for organic spacing.
    const others = Math.max(1, nodes.length - 1)
    const angle = (index / others) * Math.PI * 2 + seededFraction(index + 1) * 0.45
    return {
      id: node.id,
      x: cx + Math.cos(angle) * radius,
      y: cy + Math.sin(angle) * radius * 0.82,
      vx: 0,
      vy: 0,
      pinned: false,
    }
  })

  const byId = new Map(positions.map((p) => [p.id, p]))
  const area = width * height
  const k = Math.sqrt(area / positions.length) * 0.62

  for (let step = 0; step < iterations; step += 1) {
    const cooling = 1 - step / iterations

    // Repulsion: every node pushes every other node apart.
    for (let i = 0; i < positions.length; i += 1) {
      for (let j = i + 1; j < positions.length; j += 1) {
        const a = positions[i]
        const b = positions[j]
        let dx = a.x - b.x
        let dy = a.y - b.y
        let distSq = dx * dx + dy * dy
        if (distSq < 0.01) {
          // Coincident nodes: nudge deterministically instead of dividing by zero.
          dx = (seededFraction(i * 7 + j) - 0.5) * 2
          dy = (seededFraction(j * 7 + i) - 0.5) * 2
          distSq = dx * dx + dy * dy
        }
        const dist = Math.sqrt(distSq)
        const force = (k * k) / dist
        const fx = (dx / dist) * force
        const fy = (dy / dist) * force
        if (!a.pinned) {
          a.vx += fx
          a.vy += fy
        }
        if (!b.pinned) {
          b.vx -= fx
          b.vy -= fy
        }
      }
    }

    // Attraction along edges, with the buyer acting as the hub.
    edges.forEach((edge) => {
      const a = byId.get(edge.source)
      const b = byId.get(edge.target)
      if (!a || !b) return
      const dx = b.x - a.x
      const dy = b.y - a.y
      const dist = Math.max(0.01, Math.sqrt(dx * dx + dy * dy))
      const force = (dist * dist) / k
      const fx = (dx / dist) * force
      const fy = (dy / dist) * force
      if (!a.pinned) {
        a.vx += fx
        a.vy += fy
      }
      if (!b.pinned) {
        b.vx -= fx
        b.vy -= fy
      }
    })

    // Centring + integrate, then clamp inside the canvas with room for the labels.
    positions.forEach((p) => {
      if (p.pinned) return
      p.vx += (cx - p.x) * 0.012
      p.vy += (cy - p.y) * 0.012
      const speed = Math.sqrt(p.vx * p.vx + p.vy * p.vy) || 1
      const limit = 14 * cooling + 0.6
      const scale = Math.min(1, limit / speed)
      p.x = clamp(p.x + p.vx * scale, 96, width - 96)
      p.y = clamp(p.y + p.vy * scale, 64, height - 64)
      p.vx *= 0.72
      p.vy *= 0.72
    })
  }

  return {
    width,
    height,
    nodes: nodes.map((node) => {
      const p = byId.get(node.id)
      return { ...node, x: p?.x ?? cx, y: p?.y ?? cy }
    }),
  }
}

/** Merge live edge state from the event stream onto the static topology. */
function applyLiveEdges(edges, liveEdges, agentActivity) {
  return edges.map((edge) => {
    const key = [edge.source, edge.target].sort().join('>')
    const live = liveEdges[key]
    // A live block/rejection outranks the resting state.
    let state = live?.state || 'idle'
    if (state === 'discovered') state = 'idle'
    if (state === 'blocked' && edge.target !== 'unknown_agent_1') {
      const targetActivity = agentActivity[edge.target]?.lastEvent
      if (targetActivity === 'POLICY_ALLOWED' || targetActivity === 'AGENT_ACTION_EXECUTED') {
        // The agent recovered and continued legitimately; show that.
        state = 'negotiating'
      }
    }
    return {
      ...edge,
      state,
      ts: live?.ts || null,
      style: EDGE_STATES[state] || EDGE_STATES.idle,
    }
  })
}

/**
 * The graph hook.
 *
 * @returns {{
 *   nodes, edges, width, height,
 *   selected, setSelected, hovered, setHovered,
 *   edgeStyleFor, nodeToneFor, stats
 * }}
 */
export function useNetworkGraph({ agents = [], catalogue = [], width = 900, height = 560 } = {}) {
  const { state } = useGlobalEvents()
  const [selectedId, setSelectedId] = useState(null)
  const [hoveredId, setHoveredId] = useState(null)

  const baseNodes = useMemo(() => buildNodes(agents, catalogue), [agents, catalogue])
  const baseEdges = useMemo(() => buildEdges(baseNodes), [baseNodes])

  // Layout runs only when the topology changes, never on every event frame.
  const laidOut = useMemo(
    () => layout(baseNodes, baseEdges, { width, height }),
    [baseNodes, baseEdges, width, height],
  )

  // Merge live per-agent activity into the node model.
  const nodes = useMemo(
    () =>
      laidOut.nodes.map((node) => {
        const live = state.agents[node.id]
        return {
          ...node,
          activity: live?.activity || node.activity,
          lastEvent: live?.lastEvent || null,
          lastTs: live?.lastTs || null,
          verification:
            live?.verification && live.verification !== 'unverified'
              ? live.verification
              : node.verification,
        }
      }),
    [laidOut.nodes, state.agents],
  )

  const edges = useMemo(
    () => applyLiveEdges(baseEdges, state.edges, state.agents),
    [baseEdges, state.edges, state.agents],
  )

  const selected = useMemo(
    () => nodes.find((n) => n.id === selectedId) || null,
    [nodes, selectedId],
  )

  const stats = useMemo(() => {
    const byState = edges.reduce((acc, edge) => {
      acc[edge.state] = (acc[edge.state] || 0) + 1
      return acc
    }, {})
    return {
      nodeCount: nodes.length,
      edgeCount: edges.length,
      registered: nodes.filter((n) => n.registered).length,
      simulations: nodes.filter((n) => n.isSimulation).length,
      byState,
    }
  }, [nodes, edges])

  const nodeToneFor = useCallback(
    (node) => {
      if (!node) return 'neutral'
      if (!node.registered) return 'bad'
      if (node.verification === 'rejected') return 'bad'
      if (node.isSimulation) return 'bad'
      if (node.verification === 'verified') return 'good'
      if (node.hub) return 'chain'
      return 'neutral'
    },
    [],
  )

  return {
    nodes,
    edges,
    width: laidOut.width,
    height: laidOut.height,
    selected,
    selectedId,
    setSelectedId,
    hoveredId,
    setHoveredId,
    nodeToneFor,
    stats,
  }
}

export default useNetworkGraph
