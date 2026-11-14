import { AnimatePresence, motion } from 'motion/react'
import { Activity, RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { apiPost, invalidate, useApi } from '../lib/api'
import { fmtIst, timeAgo } from '../lib/format'
import { MOTION } from '../lib/motion'
import type { ProviderState, ProviderStatus } from '../lib/types'

const STATE_STYLE: Record<ProviderState, { dot: string; label: string }> = {
  fresh: { dot: '#7fae86', label: 'Current' },
  partial: { dot: '#d3bd6f', label: 'Partial' },
  degraded: { dot: '#d4934f', label: 'Last attempt failed' },
  stale: { dot: '#c54f42', label: 'Stale' },
  unavailable: { dot: '#c54f42', label: 'Unavailable' },
  never_run: { dot: '#8a9696', label: 'Not yet ingested' },
}

export function StatusDot({ state, pulse = false }: { state: ProviderState; pulse?: boolean }) {
  return (
    <span className={`relative inline-block size-2 rounded-full ${pulse ? 'pulse-dot' : ''}`} style={{ background: STATE_STYLE[state].dot, color: STATE_STYLE[state].dot }} aria-hidden />
  )
}

function worst(providers: ProviderStatus[]): ProviderState {
  const order: ProviderState[] = ['unavailable', 'stale', 'degraded', 'never_run', 'partial', 'fresh']
  for (const s of order) if (providers.some((p) => p.state === s)) return s
  return 'fresh'
}

export function SourceStatus({ dark = false, allowRefresh = false }: { dark?: boolean; allowRefresh?: boolean }) {
  const { data, error, refresh } = useApi<{ providers: ProviderStatus[] }>('/api/providers/status', { refreshMs: 60_000 })
  const [open, setOpen] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false)
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const providers = data?.providers ?? []
  const overall = error ? 'unavailable' : providers.length ? worst(providers) : 'never_run'
  const running = providers.some((p) => p.running)

  async function triggerRefresh() {
    setMsg(null)
    try {
      await apiPost('/api/admin/refresh/firms')
      setMsg('Satellite refresh started. New detections appear when the job finishes.')
      window.setTimeout(() => {
        invalidate('/api/')
        refresh()
      }, 8000)
    } catch (e) {
      setMsg((e as Error).message)
    }
    invalidate('/api/')
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((o) => !o)}
        className={`flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${dark ? 'bg-ivory/8 text-ivory/85 hover:bg-ivory/14' : 'panel !rounded-full hover:bg-stone-200'}`}
      >
        <StatusDot state={overall} pulse={overall === 'fresh'} />
        <span className="hidden sm:inline">
          {error
            ? 'Backend unreachable'
            : overall === 'never_run' && running
              ? 'Data sources · first ingestion running'
              : `Data sources · ${STATE_STYLE[overall].label}`}
        </span>
        <span className="sm:hidden">Sources</span>
        {running && <RefreshCw className="size-3 animate-spin" aria-label="ingestion running" />}
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            role="dialog"
            aria-label="Data source status"
            initial={{ opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.98 }}
            transition={{ duration: MOTION.fast, ease: MOTION.ease }}
            className="panel absolute right-0 z-50 mt-2 w-[min(360px,calc(100vw-32px))] p-3"
          >
            <div className="flex items-center justify-between">
              <div className="eyebrow text-graphite-500">Provenance & freshness</div>
              <Activity className="size-3.5 text-graphite-500" aria-hidden />
            </div>
            {error && <p className="mt-2 text-sm text-burnt">{error.message}</p>}
            <ul className="mt-2 divide-y divide-graphite-900/10">
              {providers.map((p) => (
                <li key={p.id} className="py-2">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2 text-sm font-medium text-graphite-900">
                      <StatusDot state={p.state} />
                      {p.label}
                    </div>
                    <span className="text-[11px] text-graphite-600">{STATE_STYLE[p.state].label}</span>
                  </div>
                  <div className="mt-0.5 pl-4 font-mono text-[11px] text-graphite-600">
                    {p.last_success ? `updated ${timeAgo(p.last_success)} · ${fmtIst(p.last_success)}` : 'no successful run yet'}
                  </div>
                  {p.last_message && <div className="mt-0.5 pl-4 text-[11px] text-burnt/90">{p.last_message}</div>}
                </li>
              ))}
            </ul>
            {allowRefresh && (
              <div className="mt-2 border-t border-graphite-900/10 pt-2">
                <button
                  type="button"
                  onClick={triggerRefresh}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-graphite-800 px-2.5 py-1.5 text-xs font-medium text-ivory hover:bg-graphite-700"
                >
                  <RefreshCw className="size-3.5" aria-hidden /> Fetch latest satellite detections
                </button>
                <p className="mt-1.5 text-[11px] text-graphite-600">Only available when the backend runs locally. Rate-limited.</p>
                {msg && <p className="mt-1 text-[11px] text-graphite-800" role="status">{msg}</p>}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
