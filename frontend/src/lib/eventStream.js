/**
 * Server-Sent Events client.
 *
 * Built on the native `EventSource`, deliberately, because the reconnect semantics are
 * the part most likely to be demoed (a judge refreshing the page mid-run) and they
 * should be ours and visible rather than hidden inside a library.
 *
 * Behaviour:
 * - Subscribes to every known event type individually. `EventSource` cannot listen for
 *   "any event" on the wire, so the type list comes from the backend catalogue and is
 *   refreshed when it changes.
 * - Tracks the highest `seq` seen. On reconnect the browser sends `Last-Event-ID`
 *   automatically, and the server replays exactly the missed frames.
 * - Exponential backoff with jitter, capped, so a backend restart does not produce a
 *   reconnect storm.
 * - A watchdog marks the connection stale if no frame (including a heartbeat-derived
 *   activity tick) arrives within the expected window, so the UI can show a
 *   "disconnected" state rather than silently looking healthy.
 * - Falls back to the REST `/events/recent` endpoint on first connect so the activity
 *   feed is populated immediately rather than after the next real event.
 */

import { SSE_PATH, apiUrl } from './constants'
import { api } from './api'

const INITIAL_BACKOFF_MS = 1000
const MAX_BACKOFF_MS = 15000
const STALE_AFTER_MS = 45000

/** Backoff with jitter, so many open tabs do not reconnect in lockstep. */
function backoffDelay(attempt) {
  const exponential = Math.min(MAX_BACKOFF_MS, INITIAL_BACKOFF_MS * 2 ** Math.max(0, attempt - 1))
  const jitter = Math.random() * exponential * 0.35
  return Math.round(exponential - exponential * 0.175 + jitter)
}

export class EventStream {
  /**
   * @param {object} options
   * @param {(frame: object) => void} options.onEvent   every decoded frame
   * @param {(state: object) => void} options.onState  connection state changes
   * @param {string[]} [options.eventTypes]             types to subscribe to
   */
  constructor({ onEvent, onState, eventTypes = [] }) {
    this.onEvent = onEvent
    this.onState = onState
    this.eventTypes = eventTypes

    this.source = null
    this.attempt = 0
    this.lastSeq = 0
    this.closedByUs = false
    this.reconnectTimer = null
    this.watchdogTimer = null
    this.lastActivityAt = Date.now()
    this.status = 'idle'
  }

  /* ------------------------------------------------------------- lifecycle */

  async connect() {
    this.closedByUs = false

    // Hydrate from the durable log first so the feed is populated on first paint.
    try {
      const recent = await api.get('/events/recent', { params: { since_seq: 0, limit: 100 } })
      const events = Array.isArray(recent?.events) ? recent.events : []
      events
        .slice()
        .sort((a, b) => a.seq - b.seq)
        .forEach((frame) => {
          if (frame.seq > this.lastSeq) {
            this.lastSeq = frame.seq
            this.onEvent(frame)
          }
        })
    } catch {
      // Hydration is best-effort; the live stream is the source of truth.
    }

    this._open()
  }

  _open() {
    if (this.closedByUs) return
    if (this.eventTypes.length === 0) {
      // Nothing to subscribe to yet; the catalogue arrives and calls connect() again.
      this._setState('connecting')
      return
    }

    this._teardownSource()
    this._setState(this.attempt === 0 ? 'connecting' : 'reconnecting')

    // apiUrl() honours VITE_API_URL. Without it a separately-hosted frontend would open
    // the stream against its own origin and silently never receive a single frame.
    const url = new URL(apiUrl(SSE_PATH))
    if (this.lastSeq > 0) url.searchParams.set('since_seq', String(this.lastSeq))

    let source
    try {
      source = new EventSource(url.toString())
    } catch (error) {
      this._scheduleReconnect()
      return
    }
    this.source = source

    source.onopen = () => {
      this.attempt = 0
      this.lastActivityAt = Date.now()
      this._setState('connected')
      this._startWatchdog()
    }

    // A transport-level error fires before `onerror` distinguishes a clean close from
    // a dropped connection, so the message is only used for logging.
    source.onerror = () => {
      if (this.closedByUs) return
      this._stopWatchdog()
      this._teardownSource()
      this._setState('disconnected')
      this._scheduleReconnect()
    }

    this.eventTypes.forEach((type) => this._listen(source, type))
  }

  _listen(source, type) {
    const handler = (message) => {
      this.lastActivityAt = Date.now()
      if (this.status !== 'connected') {
        // A frame proves the link is alive even if `onopen` was missed.
        this.attempt = 0
        this._setState('connected')
        this._startWatchdog()
      }
      let frame
      try {
        frame = JSON.parse(message.data)
      } catch {
        return
      }
      if (typeof frame.seq === 'number' && frame.seq > this.lastSeq) {
        this.lastSeq = frame.seq
      }
      this.onEvent({ ...frame, type: frame.type || type })
    }
    source.addEventListener(type, handler)
  }

  disconnect() {
    this.closedByUs = true
    this._stopWatchdog()
    this._teardownSource()
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this._setState('closed')
  }

  /** Adopt a new event-type list (e.g. after the catalogue loads) and resubscribe. */
  setEventTypes(types) {
    if (!Array.isArray(types) || types.length === 0) return
    const changed =
      types.length !== this.eventTypes.length ||
      types.some((type, index) => this.eventTypes[index] !== type)
    this.eventTypes = types
    if (changed && !this.closedByUs) {
      this.attempt = 0
      this._open()
    }
  }

  /* ---------------------------------------------------------------- helpers */

  _teardownSource() {
    if (this.source) {
      this.eventTypes.forEach((type) => {
        // Listeners are discarded with the source instance itself.
      })
      try {
        this.source.close()
      } catch {
        /* already closed */
      }
      this.source = null
    }
  }

  _scheduleReconnect() {
    if (this.closedByUs) return
    if (this.reconnectTimer) return
    this.attempt += 1
    const delay = backoffDelay(this.attempt)
    this._setState('reconnecting', { retryInMs: delay, attempt: this.attempt })
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this._open()
    }, delay)
  }

  /** Mark the link stale when frames stop arriving, even without a transport error. */
  _startWatchdog() {
    this._stopWatchdog()
    this.watchdogTimer = setInterval(() => {
      if (this.status !== 'connected') return
      if (Date.now() - this.lastActivityAt > STALE_AFTER_MS) {
        this._setState('stale')
        this._teardownSource()
        this._scheduleReconnect()
      }
    }, 5000)
  }

  _stopWatchdog() {
    if (this.watchdogTimer) {
      clearInterval(this.watchdogTimer)
      this.watchdogTimer = null
    }
  }

  _setState(status, extra = {}) {
    this.status = status
    this.onState({
      status,
      lastSeq: this.lastSeq,
      attempt: this.attempt,
      subscribedTypes: this.eventTypes.length,
      ...extra,
    })
  }
}

export default EventStream
