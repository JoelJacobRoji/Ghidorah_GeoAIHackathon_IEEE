import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'

export class ApiError extends Error {
  status: number
  code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? ''

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, { signal, headers: { Accept: 'application/json' } })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError(0, 'network_error', 'The Incendium backend could not be reached.')
  }
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    /* non-JSON */
  }
  if (!res.ok) {
    const b = (body ?? {}) as { error?: string; message?: string }
    throw new ApiError(res.status, b.error ?? 'http_error', b.message ?? `Request failed (${res.status}).`)
  }
  return body as T
}

export async function apiPost<T>(path: string, payload?: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    signal,
    ...(payload === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  })
  const body = (await res.json().catch(() => ({}))) as { error?: string; message?: string }
  if (!res.ok) throw new ApiError(res.status, body.error ?? 'http_error', body.message ?? `Request failed (${res.status}).`)
  return body as T
}

// ---------------------------------------------------------------------------
// Tiny request cache: de-duplicates concurrent requests for the same path and
// lets components share results without a global state library.
// ---------------------------------------------------------------------------
interface Entry {
  data?: unknown
  error?: ApiError
  promise?: Promise<unknown>
  fetchedAt?: number
}
const cache = new Map<string, Entry>()
const listeners = new Set<() => void>()
let version = 0
function emit() {
  version++
  listeners.forEach((l) => l())
}

function load(path: string, force = false): Promise<unknown> {
  const e = cache.get(path) ?? {}
  if (e.promise && !force) return e.promise
  const p = apiGet<unknown>(path)
    .then((data) => {
      cache.set(path, { data, fetchedAt: Date.now() })
      emit()
      return data
    })
    .catch((err: unknown) => {
      const error = err instanceof ApiError ? err : new ApiError(0, 'unknown', String(err))
      cache.set(path, { ...cache.get(path), error, promise: undefined, fetchedAt: Date.now() })
      emit()
      throw error
    })
  cache.set(path, { ...e, promise: p, error: undefined })
  emit()
  return p
}

export function invalidate(prefix = '') {
  for (const key of cache.keys()) if (key.startsWith(prefix)) cache.delete(key)
  emit()
}

export interface ApiState<T> {
  data: T | undefined
  error: ApiError | undefined
  loading: boolean
  fetchedAt: number | undefined
  refresh: () => Promise<void>
}

export function useApi<T>(path: string | null, opts: { refreshMs?: number } = {}): ApiState<T> {
  useSyncExternalStore(
    (cb) => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    () => version,
  )
  const entry = path ? cache.get(path) : undefined
  useEffect(() => {
    if (!path) return
    const e = cache.get(path)
    if (!e || (!e.data && !e.promise && !e.error)) load(path).catch(() => undefined)
  }, [path])
  const refreshMs = opts.refreshMs
  useEffect(() => {
    if (!path || !refreshMs) return
    const id = window.setInterval(() => load(path, true).catch(() => undefined), refreshMs)
    return () => window.clearInterval(id)
  }, [path, refreshMs])
  const pathRef = useRef(path)
  useEffect(() => {
    pathRef.current = path
  }, [path])
  const refresh = useCallback(async () => {
    if (pathRef.current) await load(pathRef.current, true).catch(() => undefined)
  }, [])
  return {
    data: entry?.data as T | undefined,
    error: entry?.error,
    loading: !!path && (!entry || (!!entry.promise && !entry.data)),
    fetchedAt: entry?.fetchedAt,
    refresh,
  }
}

export function useApiOnce<T>(path: string | null) {
  // Non-cached variant for large one-off payloads.
  const [state, setState] = useState<{ data?: T; error?: ApiError; loading: boolean }>({ loading: !!path })
  useEffect(() => {
    if (!path) return
    const ctrl = new AbortController()
    setState((s) => ({ ...s, loading: true }))
    apiGet<T>(path, ctrl.signal)
      .then((data) => setState({ data, loading: false }))
      .catch((e: unknown) => {
        if ((e as Error).name !== 'AbortError') setState({ error: e as ApiError, loading: false })
      })
    return () => ctrl.abort()
  }, [path])
  return state
}
