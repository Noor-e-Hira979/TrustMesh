/**
 * Owns the SSE lifecycle and feeds every frame into the global reducer.
 *
 * Mounted once by `App`. It also fetches the event-type catalogue, because
 * `EventSource` can only subscribe to named events and the backend owns that list.
 */

import { useEffect, useMemo, useReducer, useRef, useState } from 'react'
import EventStream from '../lib/eventStream'
import { api } from '../lib/api'
import { GlobalEventContext, eventReducer, initialEventState } from '../state/GlobalEventContext'

export function GlobalEventProvider({ children }) {
  const [state, dispatch] = useReducer(eventReducer, initialEventState)
  const streamRef = useRef(null)
  const [catalogueError, setCatalogueError] = useState(null)

  // Fetch the event catalogue, then (re)connect with the authoritative type list.
  useEffect(() => {
    let cancelled = false

    async function bootstrap() {
      let types = []
      try {
        const catalogue = await api.get('/events/types')
        if (cancelled) return
        types = Array.isArray(catalogue?.all) ? catalogue.all : []
        setCatalogueError(null)
      } catch (error) {
        if (cancelled) return
        // A fallback list keeps the UI live even if the catalogue call fails.
        setCatalogueError(error.message)
        types = FALLBACK_EVENT_TYPES
      }

      if (cancelled) return
      const stream = new EventStream({
        eventTypes: types,
        onEvent: (frame) => dispatch({ kind: 'frame', frame }),
        onState: (connection) => dispatch({ kind: 'connection', connection }),
      })
      streamRef.current = stream
      stream.connect()
    }

    bootstrap()

    return () => {
      cancelled = true
      streamRef.current?.disconnect()
      streamRef.current = null
    }
  }, [])

  // Mark older feed entries as no longer new so entrance animations stop replaying.
  useEffect(() => {
    const timer = setTimeout(() => {
      dispatch({ kind: 'frame', frame: null })
    }, 0)
    return () => clearTimeout(timer)
  }, [state.arrivalIndex])

  const value = useMemo(() => {
    const latest = state.feed[0] || null
    return { state, dispatch, latest, catalogueError }
  }, [state, catalogueError])

  return <GlobalEventContext.Provider value={value}>{children}</GlobalEventContext.Provider>
}

/**
 * Used only if `/events/types` is unreachable. Mirrors the backend enum so the feed
 * still works; the catalogue wins as soon as it becomes reachable again.
 */
const FALLBACK_EVENT_TYPES = [
  'INTENT_RECEIVED',
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
  'AGENT_ACTION_EXECUTED',
  'TRANSACTION_STATE_CHANGED',
  'AI_REASONING',
  'TRUST_EVALUATED',
  'SYSTEM_READY',
  'BUFFER_OVERFLOW',
]

export default GlobalEventProvider
