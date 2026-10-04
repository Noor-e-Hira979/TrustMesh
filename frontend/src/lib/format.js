/**
 * Formatting helpers.
 *
 * Money is integer cents everywhere on the wire. These helpers are for display only —
 * no policy comparison ever happens in the browser.
 */

/** Integer cents → "$1,950.00". Returns an em dash for null/undefined. */
export function formatUsd(cents, { fallback = '—' } = {}) {
  if (cents === null || cents === undefined || Number.isNaN(Number(cents))) return fallback
  return `$${(Number(cents) / 100).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

/** Compact money for tight spaces: "$1.95k". */
export function formatUsdCompact(cents) {
  if (cents === null || cents === undefined) return '—'
  const value = Number(cents) / 100
  if (Math.abs(value) >= 1000) return `$${(value / 1000).toFixed(2)}k`
  return formatUsd(cents)
}

export function formatNumber(value, { fallback = '—' } = {}) {
  if (value === null || value === undefined) return fallback
  return Number(value).toLocaleString('en-US')
}

/** ISO timestamp → "14:32:05" in the viewer's locale. */
export function formatTime(iso, { withDate = false } = {}) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleTimeString('en-US', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    ...(withDate
      ? { year: 'numeric', month: 'short', day: '2-digit' }
      : {}),
  })
}

/** "3s ago" / "2m ago" / "1h ago". */
export function formatRelative(iso, now = Date.now()) {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const seconds = Math.max(0, Math.round((now - then) / 1000))
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

/** Block an id down for display: "TM_a4b361304dc3" → "TM_a4b36…". */
export function truncateId(value, head = 10, tail = 4) {
  if (!value) return '—'
  const text = String(value)
  if (text.length <= head + tail + 1) return text
  return `${text.slice(0, head)}…${text.slice(-tail)}`
}

/**
 * Truncate a hash for display while keeping both ends, which is what makes a hash
 * recognisable. Always shows 0x and 8+8 hex characters.
 */
export function truncateHash(hash, lead = 8, tail = 6) {
  if (!hash) return '—'
  const text = String(hash)
  if (text.length <= lead + tail + 3) return text
  return `${text.slice(0, lead + 2)}…${text.slice(-tail)}`
}

/** Pretty-print canonical JSON, with a hard cap so a huge blob cannot lock the UI. */
export function formatJson(value, { indent = 2, maxLength = 4000 } = {}) {
  if (value === null || value === undefined) return '—'
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, indent)
  if (text === undefined) return '—'
  if (text.length <= maxLength) return text
  return `${text.slice(0, maxLength)}\n… truncated (${text.length} chars total)`
}

/** Sentence-case a snake_case or SCREAMING_CASE identifier. */
export function humanize(value) {
  if (!value) return '—'
  return String(value)
    .replace(/_/g, ' ')
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

/** Split camelCase / SCREAMING_SNAKE into readable words. */
export function splitIdentifier(value) {
  if (!value) return []
  return String(value)
    .replace(/_/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .split(/\s+/)
    .filter(Boolean)
}

export function pluralize(count, singular, plural) {
  const word = count === 1 ? singular : plural || `${singular}s`
  return `${formatNumber(count)} ${word}`
}

/** Clamp a number into a range. Used by the graph layout. */
export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value))
}

/** Shorten a long reason to a single readable line for dense UI. */
export function firstSentence(text, maxLength = 140) {
  if (!text) return '—'
  const clean = String(text).replace(/\s+/g, ' ').trim()
  if (clean.length <= maxLength) return clean
  const cut = clean.slice(0, maxLength)
  const lastStop = Math.max(cut.lastIndexOf('. '), cut.lastIndexOf('; '))
  if (lastStop > 40) return cut.slice(0, lastStop + 1)
  return `${cut}…`
}
