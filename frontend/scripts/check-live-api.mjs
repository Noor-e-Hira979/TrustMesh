#!/usr/bin/env node
/**
 * Live end-to-end check of the API client against a running control plane.
 *
 * The render smoke test could not catch the `api.*` regression because the failing calls
 * live inside effects. This runs every endpoint the console actually uses, against a live
 * backend, and asserts each one resolves to usable data.
 *
 *   node scripts/check-live-api.mjs [baseUrl]
 *
 * Exits non-zero on the first failure. Assumes the backend is on :8000.
 */

import api, { configureApi } from '../src/lib/api.js'

const BASE = process.argv[2] || 'http://127.0.0.1:8000'
configureApi({ baseUrl: BASE })

const GREEN = '\x1b[92m'
const RED = '\x1b[91m'
const DIM = '\x1b[2m'
const CYAN = '\x1b[96m'
const RESET = '\x1b[0m'

let failures = 0
const timings = []

async function check(name, fn, validate) {
  const started = Date.now()
  try {
    const result = await fn()
    if (validate) {
      const problem = validate(result)
      if (problem) throw new Error(problem)
    }
    const ms = Date.now() - started
    timings.push({ name, ms })
    console.log(`  [${GREEN}PASS${RESET}] ${name.padEnd(38)} ${String(ms).padStart(5)}ms`)
    return result
  } catch (error) {
    failures += 1
    console.log(`  [${RED}FAIL${RESET}] ${name.padEnd(38)} ${error.message}`)
    return null
  }
}

console.log(`\n${CYAN}TrustMesh live API check${RESET}`)
console.log(`${DIM}target: ${BASE}${RESET}\n`)

/* ------------------------------------------------------------------ health */
console.log(`${CYAN}health${RESET}`)
const health = await check('getHealth', () => api.getHealth(), (r) =>
  !r?.components?.length ? 'no component matrix' : null,
)

await check('getReadiness', () => api.getReadiness(), (r) =>
  r?.ready === false ? 'backend not ready' : null,
)

const architecture = await check('getArchitecture', () => api.getArchitecture(), (r) =>
  r?.pipeline?.length !== 7 ? `expected 7 pipeline stages, got ${r?.pipeline?.length}` : null,
)

/* ---------------------------------------------------------------- overview */
console.log(`\n${CYAN}overview${RESET}`)
await check('getOverview', () => api.getOverview(), (r) =>
  !r?.stats?.length ? 'no dashboard stats' : null,
)

/* ------------------------------------------------------------------ agents */
console.log(`\n${CYAN}agents${RESET}`)
const agents = await check('getAgents', () => api.getAgents(), (r) =>
  !Array.isArray(r) || r.length === 0 ? 'registry is empty' : null,
)

await check('getRegistryCatalogue', () => api.getRegistryCatalogue(), (r) =>
  r?.agents?.length ? null : 'catalogue is empty',
)

const firstAgent = agents?.[0]?.id
await check(`getAgent(${firstAgent})`, () => api.getAgent(firstAgent), (r) =>
  r?.id !== firstAgent ? 'agent id mismatch' : null,
)

const trust = await check('getAgentTrust(capability=search)', () =>
  api.getAgentTrust(firstAgent, { capability: 'search' }), (r) =>
    !Array.isArray(r?.factors) ? 'no trust factors returned' : null,
)
if (trust) {
  const scores = JSON.stringify(trust).match(/"(trust_)?score"/i)
  if (scores) {
    failures += 1
    console.log(`  [${RED}FAIL${RESET}] trust response must not contain a numeric score`)
  } else {
    console.log(`  [${GREEN}PASS${RESET}] trust response carries no numeric score`)
  }
}

/* ------------------------------------------------------------ transactions */
console.log(`\n${CYAN}transactions${RESET}`)
await check('runDemo', () => api.runDemo({ step_delay_seconds: 0, use_ai: false }), (r) =>
  !r?.detail ? 'no detail returned' : null,
)

// The run is asynchronous; poll for the terminal state.
let txnId = null
const deadline = Date.now() + 45000
while (Date.now() < deadline) {
  const list = await api.listTransactions({ limit: 1 }).catch(() => null)
  if (list?.items?.length) {
    const candidate = list.items[0]
    if (['completed', 'blocked', 'failed'].includes(candidate.state)) {
      txnId = candidate.id
      break
    }
  }
  await new Promise((r) => setTimeout(r, 500))
}

if (!txnId) {
  failures += 1
  console.log(`  [${RED}FAIL${RESET}] demo run never reached a terminal state`)
} else {
  console.log(`  [${GREEN}PASS${RESET}] demo run completed              ${txnId}`)

  const detail = await check('getTransaction', () => api.getTransaction(txnId), (r) =>
    r?.id !== txnId ? 'transaction id mismatch' : null,
  )

  if (detail) {
    console.log(
      `         ${DIM}state=${detail.state} offers=${detail.offers?.length} alerts=${detail.alerts?.length} commitment=${detail.commitment?.status}${RESET}`,
    )
  }

  await check('getTimeline', () => api.getTimeline(txnId), (r) =>
    r?.length ? null : 'timeline is empty',
  )
  await check('getOffers', () => api.getOffers(txnId, { limit: 100 }), (r) =>
    r?.total > 0 ? null : 'no offers recorded',
  )
  await check('getDecisions', () => api.getDecisions(txnId, { limit: 100 }), (r) =>
    r?.total > 0 ? null : 'no policy decisions recorded',
  )
  await check('getTransactionAlerts', () => api.getTransactionAlerts(txnId, { limit: 100 }), (r) =>
    r?.total > 0 ? null : 'no security alerts recorded',
  )

  const verified = await check('verifyTransaction', () => api.verifyTransaction(txnId), (r) =>
    r?.hash_matches !== true ? `digest did not reproduce (hash_matches=${r?.hash_matches})` : null,
  )
  if (verified) {
    console.log(`         ${DIM}${verified.canonicalization} → ${verified.algorithm}: ${verified.recomputed_hash.slice(0, 18)}…${RESET}`)
  }
}

await check('listTransactions', () => api.listTransactions({ limit: 5 }), (r) =>
  r?.items ? null : 'no page envelope',
)
await check('getHashingInfo', () => api.getHashingInfo(), (r) =>
  r?.selftest_passed === true ? null : 'RFC 8785 self-test failed',
)

/* ---------------------------------------------------------------- security */
console.log(`\n${CYAN}security${RESET}`)
await check('listAlerts', () => api.listAlerts({ limit: 20 }), (r) =>
  r?.items ? null : 'no page envelope',
)
await check('getSecurityStats', () => api.getSecurityStats(), (r) =>
  typeof r?.total === 'number' ? null : 'no totals',
)
await check('getSecurityRules', () => api.getSecurityRules(), (r) =>
  r?.policy_rules?.length ? null : 'no policy rules',
)

for (const scenario of ['unauthorized_capability', 'policy_violation', 'identity_mismatch']) {
  await check(`runScenario(${scenario})`, () => api.runScenario(scenario), (r) =>
    r?.decision !== 'BLOCK' ? `expected BLOCK, got ${r?.decision} (${r?.rule_id})` : null,
  )
}

/* -------------------------------------------------------------- blockchain */
console.log(`\n${CYAN}blockchain${RESET}`)
const chain = await check('getBlockchainStatus', () => api.getBlockchainStatus(), (r) =>
  r?.connection ? null : 'no connection state',
)
if (chain) {
  console.log(
    `         ${DIM}network=${chain.network} chainId=${chain.chain_id} connection=${chain.connection} mode=${chain.mode}${RESET}`,
  )
}

const commitments = await check('listCommitments', () => api.listCommitments({ limit: 10 }), (r) =>
  r?.items ? null : 'no page envelope',
)

if (commitments?.items?.length) {
  const first = commitments.items[0]
  await check('getCommitment', () => api.getCommitment(first.id), (r) =>
    r?.id !== first.id ? 'commitment id mismatch' : null,
  )
  await check('confirmCommitment', () => api.confirmCommitment(first.id), (r) =>
    r?.status ? null : 'no status returned',
  )
}

await check('getBlockchainHashing', () => api.getBlockchainHashing(), (r) =>
  r?.canonicalization === 'RFC8785' ? null : `unexpected scheme ${r?.canonicalization}`,
)
await check('getAbi', () => api.getAbi(), (r) =>
  Array.isArray(r?.abi) && r.abi.length ? null : 'ABI empty',
)

/* ------------------------------------------------------------------ events */
console.log(`\n${CYAN}events${RESET}`)
await check('getEventTypes', () => api.getEventTypes(), (r) =>
  r?.mandated_by_specification?.length === 16
    ? null
    : `expected 16 mandated events, got ${r?.mandated_by_specification?.length}`,
)
await check('getRecentEvents', () => api.getRecentEvents({ since_seq: 0, limit: 50 }), (r) =>
  Array.isArray(r?.events) ? null : 'no events array',
)
await check('getBusStats', () => api.getBusStats(), (r) =>
  typeof r?.seq === 'number' ? null : 'no seq',
)

/* ------------------------------------------------------------------ report */
console.log(`\n${CYAN}${'='.repeat(62)}${RESET}`)
if (failures === 0) {
  const total = timings.reduce((sum, t) => sum + t.ms, 0)
  console.log(`${GREEN}All ${timings.length} live endpoint checks passed${RESET}`)
  console.log(`${DIM}total round-trip time: ${total}ms${RESET}`)
  console.log(`${DIM}architecture pipeline: ${architecture?.pipeline?.join(' → ')}${RESET}`)
  console.log(`${GREEN}\nHot reload is ready: npm run dev${RESET}\n`)
  process.exit(0)
} else {
  console.log(`${RED}${failures} live endpoint check(s) failed${RESET}\n`)
  process.exit(1)
}
