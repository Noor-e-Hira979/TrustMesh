/**
 * Shared constants for the TrustMesh console.
 *
 * Single source of truth for anything the backend also declares, so the UI never
 * restates a rule or an event name and drifts from the enforcement path.
 */

/** The mandated base colour (spec §14). */
export const BASE_BG = '#0B0F19'

export const API_PREFIX = '/api'
export const SSE_PATH = '/api/events/stream'

/**
 * Resolve the control-plane base URL.
 *
 * Priority:
 *   1. ``VITE_API_URL`` - set at build time when the backend is hosted separately
 *      (the normal production case: SPA on Vercel, API on Render / Cloud Run).
 *   2. The current origin - correct for local dev, where Vite proxies ``/api`` to the
 *      backend, and for any deployment that serves both from one host.
 *
 * Without step 1 a separately-hosted frontend would call ``<its-own-domain>/api/...``
 * and 404 on every single request, including the event stream.
 *
 * Read through a guarded accessor so this module also imports cleanly under plain Node
 * (the API-surface and live-check scripts), where ``import.meta.env`` is absent.
 */
function readViteEnv(key) {
  try {
    // eslint-disable-next-line no-undef
    const env = import.meta.env
    return env ? env[key] : undefined
  } catch {
    return undefined
  }
}

function resolveApiBaseUrl() {
  const configured = readViteEnv('VITE_API_URL')
  if (configured && String(configured).trim()) {
    return String(configured).trim().replace(/\/+$/, '')
  }
  const loc = globalThis.location
  if (loc?.origin) return loc.origin
  if (typeof process !== 'undefined' && process.env?.TRUSTMESH_API_URL) {
    return String(process.env.TRUSTMESH_API_URL).replace(/\/+$/, '')
  }
  return 'http://127.0.0.1:8000'
}

/** Absolute or origin-relative base URL for the control plane. */
export const API_BASE_URL = resolveApiBaseUrl()

/** True when the API lives on a different origin than the page (CORS applies). */
export const API_IS_CROSS_ORIGIN = (() => {
  const loc = globalThis.location
  if (!loc?.origin) return false
  return API_BASE_URL !== loc.origin
})()

/**
 * Join the base with an API path.
 *
 * The base must be returned WITHOUT a trailing slash. It is used both as an absolute
 * origin (`apiUrl(SSE_PATH)`) and as a concatenation prefix (`buildUrl` does
 * `${base}${target}`), and a trailing slash there yields `//api/...`. That is not a
 * harmless cosmetic difference: the Vite dev proxy matches on the `/api` prefix, so
 * `//api/overview` misses the rule, gets treated as a static file, and 404s - while the
 * event stream, which joins through this same function differently, keeps working. The
 * console then fails in a way that looks like a backend routing bug.
 */
export function apiUrl(path) {
  if (/^https?:\/\//i.test(path)) return path
  const base = String(API_BASE_URL).replace(/\/+$/, '')
  if (!path) return base
  return `${base}${path.startsWith('/') ? path : `/${path}`}`
}

/** Authoritative principle, shown in the shell and on the About page. */
export const PRINCIPLE = 'REASON → ENFORCE → VERIFY'

export const APP_NAME = 'TrustMesh'
export const TAGLINE = 'Secure Trust Infrastructure for Autonomous AI Agents.'
export const FOOTER_NOTE = '© 2026 TrustMesh — Hackathon Prototype'

/**
 * The three authorities from spec §17. `layer` on every event frame is one of these,
 * which is what lets the feed be grouped by authority without re-deriving anything.
 */
export const LAYERS = {
  AGENT: 'AGENT',
  TRUSTMESH: 'TRUSTMESH',
  BLOCKCHAIN: 'BLOCKCHAIN',
  SYSTEM: 'SYSTEM',
}

export const LAYER_META = {
  AGENT: {
    label: 'AI / Agent',
    question: 'What the AI wants or proposes to do.',
    accent: 'violet',
  },
  TRUSTMESH: {
    label: 'TrustMesh',
    question: 'Whether the action is authorized and policy-compliant.',
    accent: 'cyan',
  },
  BLOCKCHAIN: {
    label: 'Blockchain',
    question: 'What was actually recorded for later verification.',
    accent: 'teal',
  },
  SYSTEM: {
    label: 'System',
    question: 'Control-plane state.',
    accent: 'slate',
  },
}

/** Tailwind class fragments per layer. Kept here so pages stay declarative. */
export const LAYER_CLASSES = {
  AGENT: {
    text: 'text-violet',
    border: 'border-violet/30',
    bg: 'bg-violet/10',
    dot: 'bg-violet',
    ring: 'ring-violet/30',
  },
  TRUSTMESH: {
    text: 'text-cyan',
    border: 'border-cyan/30',
    bg: 'bg-cyan/10',
    dot: 'bg-cyan',
    ring: 'ring-cyan/30',
  },
  BLOCKCHAIN: {
    text: 'text-teal',
    border: 'border-teal/30',
    bg: 'bg-teal/10',
    dot: 'bg-teal',
    ring: 'ring-teal/30',
  },
  SYSTEM: {
    text: 'text-ink-muted',
    border: 'border-white/10',
    bg: 'bg-white/5',
    dot: 'bg-ink-faint',
    ring: 'ring-white/10',
  },
}

/** The 16 event types spec §9 mandates. */
export const MANDATED_EVENT_TYPES = [
  'AGENT_DISCOVERED',
  'IDENTITY_VERIFIED',
  'IDENTITY_REJECTED',
  'CAPABILITY_CHECKED',
  'OFFER_RECEIVED',
  'NEGOTIATION_STARTED',
  'OFFER_UPDATED',
  'POLICY_CHECK_STARTED',
  'POLICY_ALLOWED',
  'POLICY_BLOCKED',
  'SECURITY_ALERT',
  'AGREEMENT_CREATED',
  'HASH_GENERATED',
  'BLOCKCHAIN_SUBMITTED',
  'BLOCKCHAIN_CONFIRMED',
  'BLOCKCHAIN_FAILED',
]

/** Events that mean an action was denied. Drives the Security Center highlight. */
export const BLOCKING_EVENT_TYPES = [
  'POLICY_BLOCKED',
  'SECURITY_ALERT',
  'IDENTITY_REJECTED',
  'BLOCKCHAIN_FAILED',
]

/** Human labels + tone for every event type the bus emits. */
export const EVENT_META = {
  INTENT_RECEIVED: { label: 'Intent received', tone: 'agent' },
  AGENT_DISCOVERED: { label: 'Agent discovered', tone: 'agent' },
  IDENTITY_VERIFIED: { label: 'Identity verified', tone: 'good' },
  IDENTITY_REJECTED: { label: 'Identity rejected', tone: 'bad' },
  CAPABILITY_CHECKED: { label: 'Capability checked', tone: 'neutral' },
  OFFER_RECEIVED: { label: 'Offer received', tone: 'agent' },
  NEGOTIATION_STARTED: { label: 'Negotiation started', tone: 'agent' },
  OFFER_UPDATED: { label: 'Offer updated', tone: 'agent' },
  POLICY_CHECK_STARTED: { label: 'Policy check started', tone: 'neutral' },
  POLICY_ALLOWED: { label: 'Policy allowed', tone: 'good' },
  POLICY_BLOCKED: { label: 'Policy blocked', tone: 'bad' },
  SECURITY_ALERT: { label: 'Security alert', tone: 'bad' },
  AGREEMENT_CREATED: { label: 'Agreement created', tone: 'chain' },
  HASH_GENERATED: { label: 'Hash generated', tone: 'chain' },
  BLOCKCHAIN_SUBMITTED: { label: 'Commitment submitted', tone: 'warn' },
  BLOCKCHAIN_CONFIRMED: { label: 'Commitment confirmed', tone: 'good' },
  BLOCKCHAIN_FAILED: { label: 'Commitment failed', tone: 'bad' },
  AGENT_ACTION_EXECUTED: { label: 'Action executed', tone: 'good' },
  TRANSACTION_STATE_CHANGED: { label: 'State changed', tone: 'neutral' },
  AI_REASONING: { label: 'AI reasoning', tone: 'agent' },
  TRUST_EVALUATED: { label: 'Trust evaluated', tone: 'neutral' },
  SYSTEM_READY: { label: 'System ready', tone: 'good' },
  BUFFER_OVERFLOW: { label: 'Buffer overflow', tone: 'warn' },
}

/** Tailwind text/border/bg classes per event tone. */
export const TONE_CLASSES = {
  good: { text: 'text-teal', bg: 'bg-teal/10', border: 'border-teal/30', dot: 'bg-teal' },
  bad: { text: 'text-red', bg: 'bg-red/10', border: 'border-red/30', dot: 'bg-red' },
  warn: { text: 'text-amber', bg: 'bg-amber/10', border: 'border-amber/30', dot: 'bg-amber' },
  agent: { text: 'text-violet', bg: 'bg-violet/10', border: 'border-violet/30', dot: 'bg-violet' },
  chain: { text: 'text-teal', bg: 'bg-teal/10', border: 'border-teal/30', dot: 'bg-teal' },
  neutral: {
    text: 'text-ink-muted',
    bg: 'bg-white/5',
    border: 'border-white/10',
    dot: 'bg-ink-faint',
  },
}

export const SEVERITY_META = {
  CRITICAL: { text: 'text-red', bg: 'bg-red/15', border: 'border-red/40', rank: 4 },
  HIGH: { text: 'text-amber', bg: 'bg-amber/15', border: 'border-amber/40', rank: 3 },
  MEDIUM: { text: 'text-cyan', bg: 'bg-cyan/10', border: 'border-cyan/30', rank: 2 },
  LOW: { text: 'text-ink-muted', bg: 'bg-white/5', border: 'border-white/10', rank: 1 },
}

export const COMMITMENT_STATUS_META = {
  CONFIRMED: { label: 'Confirmed', tone: 'good' },
  SIMULATED: { label: 'Simulated', tone: 'warn' },
  PENDING: { label: 'Pending', tone: 'warn' },
  FAILED: { label: 'Failed', tone: 'bad' },
}

export const CONNECTION_META = {
  connected: { label: 'Connected', tone: 'good' },
  simulated: { label: 'Simulated', tone: 'warn' },
  degraded: { label: 'Degraded', tone: 'warn' },
  unavailable: { label: 'Unavailable', tone: 'bad' },
}

export const TRANSACTION_STATE_LABELS = {
  created: 'Intent received',
  discovering: 'Discovering suppliers',
  verifying_identity: 'Verifying identities',
  offers_received: 'Offers received',
  evaluating: 'Evaluating offers',
  negotiating: 'Negotiating',
  agreed: 'Terms locked',
  hashed: 'Agreement hashed',
  committed: 'Committed to chain',
  completed: 'Completed',
  blocked: 'Blocked',
  failed: 'Failed',
}

/** Tone per transaction state, for the status pill. */
export const TRANSACTION_STATE_TONES = {
  created: 'neutral',
  discovering: 'neutral',
  verifying_identity: 'neutral',
  offers_received: 'neutral',
  evaluating: 'neutral',
  negotiating: 'agent',
  agreed: 'good',
  hashed: 'chain',
  committed: 'chain',
  completed: 'good',
  blocked: 'bad',
  failed: 'bad',
}

export const AGENT_TYPE_LABELS = {
  buyer: 'Buyer',
  supplier: 'Supplier',
  unknown: 'Unregistered',
}

export const VERIFICATION_LABELS = {
  verified: 'Verified',
  unverified: 'Unverified',
  rejected: 'Rejected',
}

export const TRUST_TIER_META = {
  TRUSTED: { tone: 'good', label: 'Trusted' },
  CONDITIONAL: { tone: 'warn', label: 'Conditional' },
  UNTRUSTED: { tone: 'bad', label: 'Untrusted' },
  UNASSESSED: { tone: 'neutral', label: 'Unassessed' },
}

/** Node visual states for the Agent Network graph (spec §16). */
export const EDGE_STATES = {
  idle: { color: '#334155', width: 1.25, dash: null, label: 'idle' },
  negotiating: { color: '#22D3EE', width: 2.25, dash: '6 4', label: 'negotiating' },
  verified: { color: '#2DD4BF', width: 1.75, dash: null, label: 'verified' },
  blocked: { color: '#F43F5E', width: 2.5, dash: '3 3', label: 'blocked' },
  rejected: { color: '#F59E0B', width: 2.5, dash: '2 5', label: 'identity mismatch' },
  committed: { color: '#2DD4BF', width: 2, dash: '8 4', label: 'committed' },
}

export const SCENARIOS = [
  {
    key: 'unauthorized_capability',
    title: 'Unauthorized capability request',
    spec: '§5 Scenario 1',
    detail: 'A compromised agent asks for the buyer’s API credentials.',
    expect: 'BLOCKED at the capability stage.',
  },
  {
    key: 'policy_violation',
    title: 'Policy violation',
    spec: '§5 Scenario 2',
    detail: 'An agent proposes $2,500 against a $2,000 authorized ceiling.',
    expect: 'BLOCKED by the deterministic BUDGET_CEILING rule.',
  },
  {
    key: 'identity_mismatch',
    title: 'Identity mismatch / impersonation',
    spec: '§5 Scenario 3',
    detail: 'An unregistered agent claims to represent Supplier A.',
    expect: 'BLOCKED at the identity stage.',
  },
]

/**
 * Navbar routes (spec §15).
 *
 * The root path is the Hero landing page and is reached via the brand mark rather than a
 * nav item, so this list stays exactly the six sections the specification names.
 */
export const NAV_ROUTES = [
  { to: '/overview', label: 'Overview' },
  { to: '/network', label: 'Agent Network' },
  { to: '/transactions', label: 'Transactions' },
  { to: '/security', label: 'Security' },
  { to: '/blockchain', label: 'Blockchain' },
  { to: '/about', label: 'About' },
]
