/**
 * Data-fetching hooks with explicit loading / empty / error state.
 *
 * Every major section of the UI must render a loading, empty, error and disconnected
 * state (spec §22). Returning those four things together — rather than just `data` —
 * makes it hard for a page to forget one of them.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'

/**
 * Fetch a JSON endpoint, tracking the full request lifecycle.
 *
 * @param {() => Promise<any>} fetcher  must be stable (wrap in useCallback)
 * @param {any[]} deps                  re-fetch when these change
 * @param {{enabled?: boolean, pollMs?: number, initialData?: any}} options
 *
 * @returns {{data, error, loading, refreshing, empty, reload, lastUpdated}}
 */
export function useApi(fetcher, deps = [], options = {}) {
  const { enabled = true, pollMs = 0, initialData = null } = options

  const [data, setData] = useState(initialData)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(enabled)
  const [refreshing, setRefreshing] = useState(false)
  const [lastUpdated, setLastUpdated] = useState(null)

  const mounted = useRef(true)
  // Guards against a slow earlier request overwriting a newer one (out-of-order results).
  const requestId = useRef(0)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!enabled) {
      setLoading(false)
      return
    }
    const id = ++requestId.current
    if (silent) setRefreshing(true)
    else setLoading((prev) => (data === null ? true : prev))

    try {
      const result = await fetcherRef.current()
      if (!mounted.current || id !== requestId.current) return
      setData(result)
      setError(null)
      setLastUpdated(new Date().toISOString())
    } catch (err) {
      if (!mounted.current || id !== requestId.current) return
      setError(err)
    } finally {
      if (mounted.current && id === requestId.current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
    // `data` is intentionally not a dependency: including it would reload on every
    // successful fetch and create an infinite loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled])

  useEffect(() => {
    if (!enabled) return
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled])

  useEffect(() => {
    if (!enabled || !pollMs) return undefined
    const timer = setInterval(() => load({ silent: true }), pollMs)
    return () => clearInterval(timer)
  }, [enabled, pollMs, load])

  const reload = useCallback(() => load({ silent: data !== null }), [load, data])

  const empty = useMemo(() => {
    if (data === null || data === undefined) return false
    if (Array.isArray(data)) return data.length === 0
    if (typeof data === 'object' && 'items' in data) {
      return Array.isArray(data.items) && data.items.length === 0
    }
    if (typeof data === 'object' && Object.keys(data).length === 0) return true
    return false
  }, [data])

  return { data, error, loading, refreshing, empty, reload, lastUpdated }
}

/** Convenience wrappers for the endpoints each page needs. */
export function useOverview(options) {
  return useApi(() => api.getOverview(), [], { pollMs: 4000, ...options })
}

export function useHealth() {
  return useApi(() => api.getHealth(), [], { pollMs: 15000 })
}

export function useAgents() {
  return useApi(() => Promise.all([api.getAgents(), api.getRegistryCatalogue()]), [], {
    pollMs: 10000,
  })
}

export function useBlockchainStatus(pollMs = 8000) {
  return useApi(() => api.getBlockchainStatus(), [], { pollMs })
}

export function useSecurityStats(pollMs = 8000) {
  return useApi(() => Promise.all([api.getSecurityStats(), api.getSecurityRules()]), [], {
    pollMs,
  })
}

export function useTransactions(params = {}) {
  const { limit = 50, state } = params
  return useApi(() => api.listTransactions({ limit, state }), [limit, state])
}

export function useTransaction(id) {
  return useApi(() => api.getTransaction(id), [id], { enabled: Boolean(id) })
}

export function useCommitments(params = {}) {
  const { limit = 50, status } = params
  return useApi(() => api.listCommitments({ limit, status }), [limit, status])
}

export function useTimeline(id) {
  return useApi(() => api.getTimeline(id), [id], { enabled: Boolean(id) })
}

/**
 * Wrap a mutating call with pending state and a readable error, so buttons can disable
 * themselves and surface failures without every page re-implementing it.
 */
export function useMutation(mutator) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const mutate = useCallback(
    async (...args) => {
      setPending(true)
      setError(null)
      try {
        const value = await mutator(...args)
        if (mounted.current) setResult(value)
        return value
      } catch (err) {
        if (mounted.current) setError(err)
        return null
      } finally {
        if (mounted.current) setPending(false)
      }
    },
    [mutator],
  )

  const reset = useCallback(() => {
    setError(null)
    setResult(null)
  }, [])

  return { mutate, pending, error, result, reset }
}

/** Debounce a rapidly-changing value, e.g. a search box. */
export function useDebounced(value, delayMs = 250) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}
