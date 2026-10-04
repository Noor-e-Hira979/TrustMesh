/**
 * REST client for the TrustMesh control plane.
 *
 * Structure
 * ---------
 * ``http`` is the low-level transport (path building, timeouts, error shaping).
 * ``api`` is the single public surface: it carries the raw ``get``/``post`` verbs *and*
 * every typed endpoint.
 *
 * Both import styles resolve to the same fully-populated object:
 *
 *     import { api } from '../lib/api'      // named
 *     import api from '../lib/api'          // default
 *
 * They must stay identical. Previously the endpoints were separate named exports while
 * ``api`` only held ``get``/``post``, so every ``api.getOverview()`` call site resolved
 * to ``undefined`` at runtime. Keeping one object as the single source of truth removes
 * that whole class of mistake — there is no second surface to fall out of sync.
 *
 * The browser never holds a secret: there is no place here to put a private key or an API
 * key, and nothing sensitive is ever read from the frontend (spec §27).
 */

// Explicit extension so this module can also be loaded directly by Node, which is what
// lets `npm run check:api` assert the client surface without a bundler.
import { API_PREFIX, apiUrl } from './constants.js'

const DEFAULT_TIMEOUT_MS = 20000

/** Error carrying the HTTP status, the resolved URL and any structured detail. */
export class ApiError extends Error {
  constructor(message, { status = 0, code = null, path = '', url = '' } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.path = path
    /** Absolute `METHOD url` the failure happened on, so it can be pasted into curl. */
    this.url = url
  }

  /** True for failures that are worth retrying. */
  get retryable() {
    return this.status === 0 || this.status === 408 || this.status === 429 || this.status >= 500
  }
}

/**
 * Runtime override for the control-plane base URL.
 *
 * Normally left null: in the browser the base comes from ``VITE_API_URL`` (build time)
 * and falls back to the page origin. Only the Node-based check scripts set this, since
 * they have no page origin to fall back to.
 */
let configuredBaseUrl = null

/**
 * Point the client at a different control plane.
 *
 * @param {{baseUrl?: string|null}} options  pass null to clear the override
 */
export function configureApi({ baseUrl = null } = {}) {
  configuredBaseUrl = baseUrl ? String(baseUrl).replace(/\/+$/, '') : null
}

function effectiveBaseUrl() {
  if (configuredBaseUrl) return configuredBaseUrl
  if (typeof process !== 'undefined' && process.env?.TRUSTMESH_API_URL) {
    return String(process.env.TRUSTMESH_API_URL).replace(/\/+$/, '')
  }
  return apiUrl('')
}

function buildUrl(path, params) {
  const target = path.startsWith('/api') ? path : `${API_PREFIX}${path}`
  // Strip trailing slashes from the base before concatenating. `target` always starts
  // with "/", so a base ending in "/" would produce `//api/...`, which no reverse proxy
  // or SPA host matches - a 404 on every request that is very hard to trace back here.
  const base = effectiveBaseUrl().replace(/\/+$/, '')
  const url = /^[a-z]+:\/\//i.test(target)
    ? new URL(target)
    : new URL(`${base}${target}`)
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') return
      url.searchParams.set(key, String(value))
    })
  }
  return url.toString()
}

async function request(
  path,
  { method = 'GET', body, params, signal, timeout = DEFAULT_TIMEOUT_MS } = {},
) {
  const url = buildUrl(path, params)
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)

  // Honour a caller-supplied signal alongside our own timeout.
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', () => controller.abort(), { once: true })
  }

  try {
    const response = await fetch(url, {
      method,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    })

    if (response.status === 204) return null

    const text = await response.text()
    let payload = null
    if (text) {
      try {
        payload = JSON.parse(text)
      } catch {
        payload = { detail: text.slice(0, 400) }
      }
    }

    if (!response.ok) {
      // Always name the method and the absolute URL. A bare "status 404" is
      // undiagnosable in the console: the two dominant causes (FastAPI not mounted at this
      // base, vs. a static host swallowing /api) look identical unless the URL is shown.
      const where = `${method} ${url}`
      const detail = payload?.detail ?? payload?.message
      const reason =
        detail === undefined || detail === null || detail === ''
          ? `${where} returned ${response.status} with no error detail. Either the control ` +
            `plane is not mounted at this base URL, or ${API_PREFIX} is being served by a ` +
            `static host instead of FastAPI.`
          : `${typeof detail === 'string' ? detail : JSON.stringify(detail)} (${where} -> ${response.status})`
      throw new ApiError(reason, {
        status: response.status,
        code: payload?.code ?? null,
        path,
        url: where,
      })
    }
    return payload
  } catch (error) {
    if (error instanceof ApiError) throw error
    const where = `${method} ${url}`
    if (error.name === 'AbortError') {
      throw new ApiError(
        controller.signal.reason
          ? `${String(controller.signal.reason)} (${where})`
          : `Request timed out after ${timeout}ms (${where})`,
        { status: 408, path, url: where },
      )
    }
    // Network-level failure: nothing is listening, or CORS/preflight blocked the response.
    throw new ApiError(
      `Cannot reach the TrustMesh control plane: ${where} failed (${error.message}). ` +
        `Start it with "python -m uvicorn app.main:app --port 8000" from backend/.`,
      { status: 0, path, url: where },
    )
  } finally {
    clearTimeout(timer)
  }
}

/* -------------------------------------------------------------- low-level */
const http = {
  get: (path, options) => request(path, { ...options, method: 'GET' }),
  post: (path, body, options) => request(path, { ...options, method: 'POST', body }),
}

/* --------------------------------------------------------------- endpoints */
/* health */
const getHealth = () => http.get('/health')
const getReadiness = () => http.get('/health/ready')
const getArchitecture = () => http.get('/health/architecture')

/* overview */
const getOverview = (params) => http.get('/overview', { params })

/* agents */
const getAgents = (params) => http.get('/agents', { params })
const getAgent = (agentId) => http.get(`/agents/${encodeURIComponent(agentId)}`)
const getRegistryCatalogue = () => http.get('/agents/registry-catalogue')
const getAgentTrust = (agentId, params) =>
  http.get(`/agents/${encodeURIComponent(agentId)}/trust`, { params })
const getOrganizations = () => http.get('/organizations')
const getOrganization = (orgId) => http.get(`/organizations/${encodeURIComponent(orgId)}`)

/* transactions */
const listTransactions = (params) => http.get('/transactions', { params })
const getTransaction = (id) => http.get(`/transactions/${encodeURIComponent(id)}`)
const createTransaction = (payload) => http.post('/transactions', payload)
const runDemo = (payload) => http.post('/transactions/demo', payload ?? {})
const getOffers = (id, params) =>
  http.get(`/transactions/${encodeURIComponent(id)}/offers`, { params })
const getDecisions = (id, params) =>
  http.get(`/transactions/${encodeURIComponent(id)}/decisions`, { params })
const getTransactionAlerts = (id, params) =>
  http.get(`/transactions/${encodeURIComponent(id)}/alerts`, { params })
const getTimeline = (id) => http.get(`/transactions/${encodeURIComponent(id)}/timeline`)
const verifyTransaction = (id) => http.post(`/transactions/${encodeURIComponent(id)}/verify`)
const getHashingInfo = () => http.get('/transactions/hashing/describe')

/* security */
const listAlerts = (params) => http.get('/security/alerts', { params })
const getAlert = (id) => http.get(`/security/alerts/${encodeURIComponent(id)}`)
const setAlertStatus = (id, nextStatus) =>
  http.post(
    `/security/alerts/${encodeURIComponent(id)}/status?status=${encodeURIComponent(nextStatus)}`,
  )
const getSecurityStats = () => http.get('/security/stats')
const getSecurityRules = () => http.get('/security/rules')
const runScenario = (name) => http.post(`/security/scenarios/${encodeURIComponent(name)}`)

/* blockchain */
const getBlockchainStatus = () => http.get('/blockchain/status')
const listCommitments = (params) => http.get('/blockchain/commitments', { params })
const getCommitment = (id) => http.get(`/blockchain/commitments/${encodeURIComponent(id)}`)
const confirmCommitment = (id) =>
  http.post(`/blockchain/commitments/${encodeURIComponent(id)}/confirm`)
const getAbi = () => http.get('/blockchain/abi')
const getBlockchainHashing = () => http.get('/blockchain/hashing')

/* events */
const getRecentEvents = (params) => http.get('/events/recent', { params })
const getEventTypes = () => http.get('/events/types')
const getBusStats = () => http.get('/events/bus')

/**
 * The single public client surface.
 *
 * Every endpoint above is a property here, so `api.<endpoint>()` always resolves. Named
 * and default imports are the same object.
 */
export const api = {
  // raw verbs, used by the SSE client for hydration
  get: http.get,
  post: http.post,

  // health
  getHealth,
  getReadiness,
  getArchitecture,

  // overview
  getOverview,

  // agents
  getAgents,
  getAgent,
  getRegistryCatalogue,
  getAgentTrust,
  getOrganizations,
  getOrganization,

  // transactions
  listTransactions,
  getTransaction,
  createTransaction,
  runDemo,
  getOffers,
  getDecisions,
  getTransactionAlerts,
  getTimeline,
  verifyTransaction,
  getHashingInfo,

  // security
  listAlerts,
  getAlert,
  setAlertStatus,
  getSecurityStats,
  getSecurityRules,
  runScenario,

  // blockchain
  getBlockchainStatus,
  listCommitments,
  getCommitment,
  confirmCommitment,
  getAbi,
  getBlockchainHashing,

  // events
  getRecentEvents,
  getEventTypes,
  getBusStats,
}

/** Every endpoint name, so a self-check can assert none are missing. */
export const API_ENDPOINTS = Object.keys(api)

export { ApiError as ApiClientError }
export default api
