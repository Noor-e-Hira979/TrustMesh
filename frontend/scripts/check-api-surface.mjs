#!/usr/bin/env node
/**
 * API client surface check.
 *
 * Regression guard for a real bug: the endpoints were separate named exports while the
 * `api` object only carried `get`/`post`, so every `api.getOverview()` call site resolved
 * to `undefined` at runtime and the console rendered nothing but empty states.
 *
 * A render-only smoke test cannot catch that, because the failing calls happen inside
 * effects that never run during server rendering. This check loads the real module and
 * asserts, statically, that every `api.<member>` reference in the source exists on the
 * object and is callable.
 *
 *   node scripts/check-api-surface.mjs
 *
 * Exits non-zero on any missing member.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import api, { API_ENDPOINTS } from '../src/lib/api.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = resolve(HERE, '..', 'src')

function walk(dir) {
  const out = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) out.push(...walk(full))
    else if (/\.(js|jsx)$/.test(entry)) out.push(full)
  }
  return out
}

const files = walk(SRC)
const apiFile = resolve(SRC, 'lib', 'api.js')

// Every `api.<member>` reference in the source, excluding the client module itself.
const references = new Map()
for (const file of files) {
  if (resolve(file) === apiFile) continue
  const source = readFileSync(file, 'utf8')
  for (const match of source.matchAll(/\bapi\.(\w+)/g)) {
    const member = match[1]
    if (!references.has(member)) references.set(member, new Set())
    references.get(member).add(relative(SRC, file).replace(/\\/g, '/'))
  }
}

const GREEN = '\x1b[92m'
const RED = '\x1b[91m'
const DIM = '\x1b[2m'
const CYAN = '\x1b[96m'
const RESET = '\x1b[0m'

const failures = []

console.log(`\n${CYAN}TrustMesh API client surface${RESET}`)
console.log(`${DIM}src files scanned : ${files.length}`)
console.log(`members referenced : ${references.size}`)
console.log(`members exported   : ${API_ENDPOINTS.length}${RESET}\n`)

for (const [member, locations] of [...references.entries()].sort()) {
  const value = api[member]
  const ok = typeof value === 'function'
  if (!ok) {
    failures.push({ member, locations: [...locations] })
  }
  const mark = ok ? `${GREEN}PASS${RESET}` : `${RED}FAIL${RESET}`
  const type = ok ? 'function' : `${typeof value} (not callable)`
  console.log(
    `  [${mark}] api.${member.padEnd(22)} ${type.padEnd(22)} ${DIM}${[...locations].join(', ')}${RESET}`,
  )
}

// The named and default exports must be the same fully-populated object.
console.log('')
const sameObject = api === (await import('../src/lib/api.js')).default
console.log(
  `  [${sameObject ? GREEN : RED}${sameObject ? 'PASS' : 'FAIL'}${RESET}] default export is the same object as the named export`,
)
if (!sameObject) failures.push({ member: '<default export>', locations: ['src/lib/api.js'] })

// Any endpoint exported but never referenced is dead surface worth knowing about.
const unreferenced = API_ENDPOINTS.filter(
  (name) => !references.has(name) && name !== 'get' && name !== 'post',
)
if (unreferenced.length > 0) {
  console.log(
    `\n  ${DIM}exported but not referenced by the UI: ${unreferenced.join(', ')}${RESET}`,
  )
}

/* ------------------------------------------------------- request URL shape */
/**
 * Every request URL must be `<origin>/api/...` with exactly one slash after the origin.
 *
 * This guards a bug that broke the entire console while looking like a backend routing
 * fault. `apiUrl('')` used to return the base *with* a trailing slash, and `buildUrl`
 * concatenates `${base}${target}`, so every REST call went out as
 * `http://localhost:5173//api/overview`. The Vite dev proxy matches on the `/api`
 * prefix, so `//api/...` missed the rule, was served the SPA fallback HTML, and every
 * request failed - while SSE, which joins differently, kept working and made it look
 * intermittent. Only the browser took this path: the Node check scripts call
 * `configureApi()`, which trims the slash, so no existing test could see it.
 */
const captured = []
const realFetch = globalThis.fetch
globalThis.fetch = async (url) => {
  captured.push(String(url))
  return { ok: true, status: 200, text: async () => '{}' }
}

const URL_SAMPLES = [
  ['getHealth', () => api.getHealth()],
  ['getReadiness', () => api.getReadiness()],
  ['getOverview', () => api.getOverview()],
  ['getAgents', () => api.getAgents()],
  ['listTransactions', () => api.listTransactions({ limit: 5 })],
  ['getSecurityStats', () => api.getSecurityStats()],
  ['getBlockchainStatus', () => api.getBlockchainStatus()],
  ['getEventTypes', () => api.getEventTypes()],
  ['getRecentEvents', () => api.getRecentEvents({ limit: 5 })],
  ['runDemo', () => api.runDemo({ step_delay_seconds: 0 })],
]

let shapeOk = true
let shapeDetail = ''
try {
  for (const [, call] of URL_SAMPLES) {
    // Errors are irrelevant here; only the resolved URL is under test.
    await call().catch(() => {})
  }
} finally {
  globalThis.fetch = realFetch
}

const badUrls = []
for (const url of captured) {
  const afterScheme = url.replace(/^https?:\/\//i, '')
  const slash = afterScheme.indexOf('/')
  const origin = slash === -1 ? afterScheme : afterScheme.slice(0, slash)
  const pathname = slash === -1 ? '' : afterScheme.slice(slash)
  const hasDoubleSlashInPath = pathname.startsWith('//')
  const missingPrefix = !pathname.startsWith('/api/')
  if (hasDoubleSlashInPath || missingPrefix) {
    badUrls.push(`${url} (${hasDoubleSlashInPath ? 'double slash' : 'missing /api prefix'})`)
  }
  if (!origin) badUrls.push(`${url} (no origin)`)
}

if (badUrls.length > 0) {
  shapeOk = false
  shapeDetail = badUrls.join('\n      ')
}
if (captured.length !== URL_SAMPLES.length) {
  shapeOk = false
  shapeDetail = `expected ${URL_SAMPLES.length} requests, captured ${captured.length}`
}

console.log('')
console.log(
  `  [${shapeOk ? GREEN : RED}${shapeOk ? 'PASS' : 'FAIL'}${RESET}] request URL shape    ` +
    `${DIM}${captured.length} URLs, all <origin>/api/...${RESET}`,
)
if (!shapeOk) {
  console.log(`      ${RED}${shapeDetail}${RESET}`)
  failures.push({ member: '<request URL shape>', locations: ['src/lib/api.js', 'src/lib/constants.js'] })
}

/* ------------------------------------------------- deployment base-URL guard */
/**
 * A production bundle must know where the control plane lives.
 *
 * Vite inlines VITE_* at BUILD time. With VITE_API_URL empty, src/lib/constants.js falls
 * back to `location.origin`, so a separately-hosted SPA calls <its-own-domain>/api/... and
 * gets a 404 from the static host on every single request, including the SSE stream. It
 * builds cleanly, deploys cleanly, and is broken at runtime - the worst possible failure
 * mode right before a demo.
 *
 * So this is a hard build failure, not a warning. It is bypassable only by asserting the
 * deployment genuinely serves the API from the same origin.
 */
console.log('')
const configuredApiUrl = (process.env.VITE_API_URL || '').trim()
const sameOrigin = ['1', 'true', 'yes'].includes(
  (process.env.TRUSTMESH_SAME_ORIGIN || '').trim().toLowerCase(),
)

let baseUrlOk = true
let baseUrlMessage = ''

if (configuredApiUrl) {
  baseUrlMessage = `VITE_API_URL=${configuredApiUrl}`
} else if (sameOrigin) {
  baseUrlMessage = 'VITE_API_URL unset, TRUSTMESH_SAME_ORIGIN=1 (API served from this origin)'
} else {
  baseUrlOk = false
  baseUrlMessage = [
    'VITE_API_URL is not set, so this bundle would resolve the control plane from',
    'location.origin and 404 on every /api request when the API is hosted separately.',
    '',
    'Either set VITE_API_URL in the build environment and redeploy:',
    '  VITE_API_URL=https://your-api.example.com npm run build',
    '',
    'Or, if a reverse proxy serves the API from the same origin as this page, assert it:',
    '  TRUSTMESH_SAME_ORIGIN=1 npm run build',
  ].join('\n      ')
}

console.log(
  `  [${baseUrlOk ? GREEN : RED}${baseUrlOk ? 'PASS' : 'FAIL'}${RESET}] deployment base URL  ${DIM}${baseUrlMessage}${RESET}`,
)
if (!baseUrlOk) failures.push({ member: '<VITE_API_URL>', locations: ['build environment'] })

console.log('')
if (failures.length > 0) {
  console.log(`${RED}${failures.length} check(s) failed:${RESET}`)
  failures.forEach(({ member, locations }) =>
    console.log(`  ${RED}${member}${RESET} — referenced in ${locations.join(', ')}`),
  )
  console.log(`${RED}\nSee the messages above.${RESET}\n`)
  process.exit(1)
}

console.log(`${GREEN}Every api.* reference resolves to a callable endpoint.${RESET}\n`)
