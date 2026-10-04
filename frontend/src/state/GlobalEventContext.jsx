/**
 * Global event state.
 *
 * A single reducer folds every SSE frame into the derived view the pages need: the
 * live activity feed, per-agent activity, per-edge connection state and the running
 * alert/transaction counters. Deriving this once centrally means the graph, the feed
 * and the Security Center can never disagree about what just happened.
 *
 * The reducer is pure and exported, so its behaviour is testable without a browser.
 */

import { createContext, useContext } from 'react'

/** How many frames to retain for the activity feed. */
export const FEED_LIMIT = 300
/** How many alerts to retain for the Security Center preview. */
export const ALERT_LIMIT = 60
/** How many commitments to retain. */
export const COMMITMENT_LIMIT = 40

export const initialEventState = {
  connection: {
    status: 'idle',
    lastSeq: 0,
    attempt: 0,
    retryInMs: null,
    subscribedTypes: 0,
  },
  feed: [],
  alerts: [],
  commitments: [],
  /** agent_id -> { activity, lastEvent, lastTs, verification } */
  agents: {},
  /** "buyerId>counterpartyId" -> { state, ts } for the network graph edges. */
  edges: {},
  lastTransactionId: null,
  lastAgreementHash: null,
  /** Monotonic counter used to key newly-arrived frames for entrance animation. */
  arrivalIndex: 0,
  notice: null,
}

const edgeKey = (a, b) => [a, b].sort().join('>')

/**
 * Derive the graph edge state implied by one event.
 *
 * Returns null when the event says nothing about connectivity, so unrelated frames do
 * not churn the graph.
 */
function edgeStateFor(frame) {
  const p = frame.payload || {}
  switch (frame.type) {
    case 'AGENT_DISCOVERED':
      return ['discovered', p.discovered_agent_id]
    case 'IDENTITY_VERIFIED':
      return ['verified', p.organization_id ? undefined : undefined]
    case 'IDENTITY_REJECTED':
      return ['rejected', frame.agent_id]
    case 'NEGOTIATION_STARTED':
      return ['negotiating', p.supplier_agent_id]
    case 'OFFER_RECEIVED':
    case 'OFFER_UPDATED':
      return ['negotiating', frame.agent_id]
    case 'POLICY_BLOCKED':
    case 'SECURITY_ALERT':
      return ['blocked', frame.agent_id]
    case 'AGREEMENT_CREATED':
    case 'HASH_GENERATED':
    case 'BLOCKCHAIN_CONFIRMED':
      return ['committed', frame.agent_id]
    default:
      return null
  }
}

function reduceFeed(state, frame) {
  const entry = {
    key: `${frame.seq}-${state.arrivalIndex}`,
    seq: frame.seq,
    type: frame.type,
    layer: frame.layer || 'SYSTEM',
    ts: frame.ts,
    agent_id: frame.agent_id ?? null,
    transaction_id: frame.transaction_id ?? null,
    payload: frame.payload || {},
    isNew: true,
  }
  return [entry, ...state.feed].slice(0, FEED_LIMIT)
}

function reduceAlert(state, frame) {
  const p = frame.payload || {}
  if (!p.alert_id) return state.alerts
  if (state.alerts.some((a) => a.id === p.alert_id)) return state.alerts
  return [
    {
      id: p.alert_id,
      transaction_id: frame.transaction_id ?? null,
      agent_id: p.agent_id,
      agent_name: p.agent_name,
      organization_id: p.organization_id,
      organization_name: p.organization_name,
      severity: p.severity,
      alert_type: p.alert_type,
      requested_action: p.requested_action,
      requested_capability: p.requested_capability,
      decision: p.decision,
      reason: p.reason,
      evidence: p.evidence || {},
      policy_rule_id: p.policy_rule_id,
      stage: p.stage,
      status: p.status,
      is_simulation: p.is_simulation,
      ts: frame.ts,
      seq: frame.seq,
      isNew: true,
    },
    ...state.alerts,
  ].slice(0, ALERT_LIMIT)
}

function reduceCommitment(state, frame) {
  const p = frame.payload || {}
  if (!p.commitment_id) return state.commitments
  const existing = state.commitments.find((c) => c.id === p.commitment_id)
  const next = {
    id: p.commitment_id,
    transaction_id: p.transaction_id,
    agreement_hash: p.agreement_hash,
    tx_hash: p.tx_hash,
    block_number: p.block_number,
    confirmations: p.confirmations,
    status: p.status,
    mode: p.mode,
    error: p.error,
    network: p.network,
    chain_id: p.chain_id,
    contract_address: p.contract_address,
    explorer_url: p.explorer_url,
    ts: frame.ts,
    seq: frame.seq,
    isNew: true,
  }
  return existing
    ? state.commitments.map((c) => (c.id === next.id ? { ...next, isNew: true } : c))
    : [next, ...state.commitments].slice(0, COMMITMENT_LIMIT)
}

function reduceAgent(state, frame) {
  const p = frame.payload || {}
  const ids = new Set()
  if (frame.agent_id) ids.add(frame.agent_id)
  if (p.discovered_agent_id) ids.add(p.discovered_agent_id)
  if (p.supplier_agent_id) ids.add(p.supplier_agent_id)
  if (p.agent_id && typeof p.agent_id === 'string') ids.add(p.agent_id)
  if (ids.size === 0) return state.agents

  const next = { ...state.agents }
  let changed = false
  ids.forEach((id) => {
    const current = next[id] || {
      activity: 'idle',
      lastEvent: null,
      lastTs: null,
      verification: null,
      tone: 'neutral',
    }
    next[id] = {
      ...current,
      activity: activityFor(frame, id) || current.activity,
      lastEvent: frame.type,
      lastTs: frame.ts,
      tone: toneFor(frame),
      verification:
        frame.type === 'IDENTITY_VERIFIED'
          ? 'verified'
          : frame.type === 'IDENTITY_REJECTED'
            ? 'rejected'
            : current.verification,
    }
    changed = true
  })
  return changed ? next : state.agents
}

function toneFor(frame) {
  if (frame.type === 'POLICY_BLOCKED' || frame.type === 'SECURITY_ALERT') return 'bad'
  if (frame.type === 'IDENTITY_REJECTED') return 'bad'
  if (frame.type === 'IDENTITY_VERIFIED' || frame.type === 'POLICY_ALLOWED') return 'good'
  if (frame.type === 'BLOCKCHAIN_CONFIRMED') return 'chain'
  if (frame.type === 'BLOCKCHAIN_SUBMITTED' || frame.type === 'BLOCKCHAIN_FAILED') return 'warn'
  if (frame.layer === 'AGENT') return 'agent'
  return 'neutral'
}

function activityFor(frame, agentId) {
  const p = frame.payload || {}
  switch (frame.type) {
    case 'AGENT_DISCOVERED':
      return p.discovered_agent_id === agentId ? 'discovered on the network' : null
    case 'IDENTITY_VERIFIED':
      return 'identity verified'
    case 'IDENTITY_REJECTED':
      return 'identity rejected'
    case 'CAPABILITY_CHECKED':
      return `capability checked: ${p.requested_capability || 'unknown'}`
    case 'OFFER_RECEIVED':
      return `sent an offer ($${((p.offer?.price_cents || 0) / 100).toLocaleString()})`
    case 'OFFER_UPDATED':
      return `counter-offer round ${p.round ?? '?'}`
    case 'NEGOTIATION_STARTED':
      return 'entered negotiation'
    case 'POLICY_BLOCKED':
      return `BLOCKED · ${p.rule_id || 'rule'}`
    case 'POLICY_ALLOWED':
      return `allowed · ${p.capability || 'action'}`
    case 'AGENT_ACTION_EXECUTED':
      return p.action ? `executed ${p.action}` : 'action executed'
    case 'AGREEMENT_CREATED':
      return 'agreement created'
    case 'HASH_GENERATED':
      return 'agreement hashed'
    case 'BLOCKCHAIN_SUBMITTED':
      return 'commitment submitted'
    case 'BLOCKCHAIN_CONFIRMED':
      return 'commitment confirmed'
    case 'BLOCKCHAIN_FAILED':
      return 'commitment failed'
    case 'TRANSACTION_STATE_CHANGED':
      return `state: ${p.state || 'changed'}`
    default:
      return null
  }
}

function reduceEdges(state, frame) {
  const hint = edgeStateFor(frame)
  if (!hint) return state.edges
  const [stateName, otherId] = hint
  const buyer = frame.transaction_id ? 'buyer_agent_1' : frame.agent_id
  if (!buyer) return state.edges

  const target =
    otherId ||
    frame.payload?.counterparty_agent_id ||
    frame.payload?.buyer_agent_id ||
    frame.payload?.supplier_agent_id
  if (!target || target === buyer) return state.edges

  const key = edgeKey(buyer, target)
  const current = state.edges[key]
  // A block is sticky: do not let a later unrelated frame clear a rejected edge.
  if (current?.state === 'rejected' && stateName !== 'rejected') return state.edges
  return { ...state.edges, [key]: { state: stateName, ts: frame.ts, seq: frame.seq } }
}

/**
 * The reducer. Pure: same state + frame always yields the same next state.
 *
 * @param {object} state
 * @param {{kind: string, frame?: object, connection?: object, notice?: string|null}} action
 */
export function eventReducer(state, action) {
  switch (action.kind) {
    case 'connection':
      return { ...state, connection: { ...state.connection, ...action.connection } }

    case 'notice':
      return { ...state, notice: action.notice }

    case 'hydrate':
      // Bulk replay: only the newest frame per collection is kept meaningful.
      return action.frames.reduce(eventReducer, state)

    case 'reset':
      return { ...initialEventState, connection: state.connection }

    case 'frame': {
      const frame = action.frame
      if (!frame || typeof frame.seq !== 'number') return state
      // Ignore duplicates the reconnect replay may legitimately resend.
      if (frame.seq <= state.connection.lastSeq && state.feed.length > 0) {
        const known = state.feed.some((f) => f.seq === frame.seq)
        if (known) return state
      }
      return {
        ...state,
        arrivalIndex: state.arrivalIndex + 1,
        connection: { ...state.connection, lastSeq: Math.max(state.connection.lastSeq, frame.seq) },
        feed: reduceFeed(state, frame),
        alerts: reduceAlert(state, frame),
        commitments: reduceCommitment(state, frame),
        agents: reduceAgent(state, frame),
        edges: reduceEdges(state, frame),
        lastTransactionId: frame.transaction_id || state.lastTransactionId,
        lastAgreementHash:
          frame.type === 'HASH_GENERATED'
            ? frame.payload?.agreement_hash || state.lastAgreementHash
            : state.lastAgreementHash,
      }
    }

    default:
      return state
  }
}

/** Context carrying the reducer state and the actions that dispatch into it. */
export const GlobalEventContext = createContext({
  state: initialEventState,
  dispatch: () => {},
  /** Convenience: the newest frame, used by pages that only need "what changed". */
  latest: null,
})

export function useGlobalEvents() {
  return useContext(GlobalEventContext)
}
