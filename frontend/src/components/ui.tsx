import { AlertTriangle, CircleDashed, RefreshCw, WifiOff } from 'lucide-react'
import type { ReactNode } from 'react'
import { ApiError } from '../lib/api'
import { compositeClassInfo, fwiClassInfo, NO_DATA_COLOR, textOn } from '../lib/risk'

/** Class badge: colour + level bars + text, so meaning never depends on colour alone. */
export function ClassBadge({
  name,
  kind = 'fwi',
  size = 'md',
}: {
  name: string | null | undefined
  kind?: 'fwi' | 'composite'
  size?: 'sm' | 'md'
}) {
  const info = kind === 'fwi' ? fwiClassInfo(name) : compositeClassInfo(name)
  const max = kind === 'fwi' ? 6 : 4
  const color = info?.color ?? NO_DATA_COLOR
  const fg = textOn(color)
  const label = info?.name ?? 'No data'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full font-medium whitespace-nowrap ${size === 'sm' ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-1 text-xs'}`}
      style={{ background: color, color: fg }}
      aria-label={`${kind === 'fwi' ? 'FWI class' : 'Composite class'}: ${label}${info ? `, level ${info.level} of ${max}` : ''}`}
    >
      <span className="flex items-end gap-[2px]" aria-hidden>
        {Array.from({ length: max }, (_, i) => (
          <span
            key={i}
            className="w-[3px] rounded-[1px]"
            style={{ height: 4 + i * 1.6, background: fg, opacity: info && i < info.level ? 0.95 : 0.25 }}
          />
        ))}
      </span>
      {label}
    </span>
  )
}

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden />
}

export function LoadingBlock({ label = 'Loading data…', dark = false }: { label?: string; dark?: boolean }) {
  return (
    <div role="status" aria-live="polite" className={`flex items-center gap-3 text-sm ${dark ? 'text-ivory/70' : 'text-graphite-600'}`}>
      <CircleDashed className="size-4 animate-spin [animation-duration:2.4s]" aria-hidden />
      {label}
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
  title = 'Data unavailable',
  dark = false,
  compact = false,
}: {
  error: ApiError | Error | undefined
  onRetry?: () => void
  title?: string
  dark?: boolean
  compact?: boolean
}) {
  const network = error instanceof ApiError && error.status === 0
  const Icon = network ? WifiOff : AlertTriangle
  return (
    <div role="alert" className={`flex ${compact ? 'items-center gap-2' : 'flex-col items-start gap-2'} text-sm ${dark ? 'text-ivory/85' : 'text-graphite-800'}`}>
      <div className="flex items-center gap-2 font-medium">
        <Icon className="size-4 text-burnt" aria-hidden />
        {title}
      </div>
      {!compact && <p className={`max-w-prose ${dark ? 'text-ivory/65' : 'text-graphite-600'}`}>{error?.message ?? 'Unknown error.'} No substitute values are shown.</p>}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${dark ? 'bg-ivory/10 hover:bg-ivory/20' : 'bg-graphite-900/8 hover:bg-graphite-900/15'}`}
        >
          <RefreshCw className="size-3.5" aria-hidden /> Retry
        </button>
      )}
    </div>
  )
}

export function Eyebrow({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`eyebrow ${className}`}>{children}</div>
}

export function Metric({
  label,
  value,
  unit,
  hint,
  dark = false,
}: {
  label: string
  value: ReactNode
  unit?: string
  hint?: ReactNode
  dark?: boolean
}) {
  return (
    <div className="min-w-0">
      <div className={`eyebrow ${dark ? 'text-ivory/55' : 'text-graphite-500'}`}>{label}</div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className="font-mono text-lg tabular leading-none">{value}</span>
        {unit && <span className={`text-xs ${dark ? 'text-ivory/55' : 'text-graphite-500'}`}>{unit}</span>}
      </div>
      {hint && <div className={`mt-1 text-[11px] leading-snug ${dark ? 'text-ivory/50' : 'text-graphite-500'}`}>{hint}</div>}
    </div>
  )
}

export function Tag({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'forecast' | 'warn' | 'model' }) {
  const styles = {
    neutral: 'bg-graphite-900/8 text-graphite-700',
    forecast: 'bg-[#3d5a6a]/15 text-[#2f4a58] ring-1 ring-[#3d5a6a]/30',
    warn: 'bg-burnt/12 text-burnt ring-1 ring-burnt/30',
    model: 'bg-forest-500/12 text-forest-700 ring-1 ring-forest-500/25',
  }[tone]
  return <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[10.5px] font-medium tracking-wide ${styles}`}>{children}</span>
}
