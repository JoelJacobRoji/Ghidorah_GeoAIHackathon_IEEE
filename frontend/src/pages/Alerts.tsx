import { AlertOctagon, AlertTriangle, BellRing, Check, Flame, Info, Mail, MapPin, Phone, Webhook } from 'lucide-react'
import { motion } from 'motion/react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { NavBar } from '../components/NavBar'
import { SourceStatus } from '../components/SourceStatus'
import { ErrorState, Skeleton } from '../components/ui'
import { apiPost, invalidate, useApi } from '../lib/api'
import { fmtIst, timeAgo } from '../lib/format'
import { fadeUp, stagger } from '../lib/motion'
import type { AlertItem, AlertPriority, AlertsResponse } from '../lib/types'

// Status colours are reserved for alert priority and always paired with an icon and a label.
const PRIORITY: Record<AlertPriority, { label: string; icon: typeof Info; chip: string; bar: string }> = {
  high: { label: 'High priority', icon: AlertOctagon, chip: 'bg-[#8a1a1a] text-white', bar: '#8a1a1a' },
  medium: { label: 'Medium priority', icon: AlertTriangle, chip: 'bg-[#a86400] text-white', bar: '#a86400' },
  low: { label: 'Low priority', icon: Info, chip: 'bg-graphite-900/12 text-graphite-800', bar: '#7d8a8a' },
}
const WINDOWS = [24, 48, 168]

export default function Alerts() {
  const [minP, setMinP] = useState<AlertPriority>('medium')
  const [hours, setHours] = useState(48)
  const [openOnly, setOpenOnly] = useState(false)
  const path = `/api/alerts?hours=${hours}&min_priority=${minP}&include_acknowledged=${!openOnly}`
  const { data, error, loading, refresh } = useApi<AlertsResponse>(path, { refreshMs: 60_000 })

  return (
    <div className="min-h-dvh bg-[#d9d4c7] text-graphite-900">
      <NavBar right={<SourceStatus dark />} />
      <motion.main variants={stagger} initial="hidden" animate="show" className="mx-auto max-w-[1100px] px-4 py-8 md:px-6">
        <motion.div variants={fadeUp} className="eyebrow text-graphite-500">Module 5 · emergency alert system</motion.div>
        <motion.h1 variants={fadeUp} className="font-display mt-1 text-3xl font-bold text-graphite-950 md:text-4xl">Alerts</motion.h1>
        <motion.p variants={fadeUp} className="mt-2 max-w-3xl text-[15px] text-graphite-700">
          Rules flag satellite detections near settlements, in severe fire weather or with high radiative power, plus a daily fire-weather outlook. Each alert lists the nearest fire stations from OpenStreetMap.{' '}
          <b>Incendium never contacts authorities on its own.</b> Notifications go only to recipients the operator configures.
        </motion.p>

        {data && (
          <motion.div variants={fadeUp} className="mt-5 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {(['high', 'medium', 'low'] as const).map((p) => {
              const P = PRIORITY[p]
              const s = data.summary[p]
              return (
                <div key={p} className="rounded-xl bg-ivory/70 p-3">
                  <div className="flex items-center gap-1.5 text-[12px] font-medium"><P.icon className="size-4" style={{ color: P.bar }} aria-hidden /> {P.label}</div>
                  <div className="mt-1 font-mono text-2xl tabular">{s?.open ?? 0}<span className="text-sm text-graphite-500"> open / {s?.total ?? 0}</span></div>
                  <div className="text-[11px] text-graphite-500">last {hours} h</div>
                </div>
              )
            })}
            <div className="rounded-xl bg-ivory/70 p-3 text-[12px]">
              <div className="font-medium">Notification channels</div>
              <div className="mt-1.5 flex items-center gap-1.5"><Mail className="size-3.5" aria-hidden /> Email: {data.channels.email ? 'configured' : 'not configured'}</div>
              <div className="mt-0.5 flex items-center gap-1.5"><Webhook className="size-3.5" aria-hidden /> Webhook: {data.channels.webhook ? 'configured' : 'not configured'}</div>
              <div className="mt-0.5 text-graphite-600">Minimum priority sent: {data.channels.min_priority}</div>
              <div className="mt-0.5 text-graphite-600">Automatic contact with authorities: never</div>
            </div>
          </motion.div>
        )}

        <motion.div variants={fadeUp} className="mt-5 flex flex-wrap items-center gap-3 text-[13px]">
          <div className="flex rounded-xl bg-graphite-900/8 p-0.5" role="radiogroup" aria-label="Minimum priority">
            {(['low', 'medium', 'high'] as const).map((p) => (
              <button key={p} type="button" role="radio" aria-checked={minP === p} onClick={() => setMinP(p)} className={`rounded-[10px] px-3 py-1.5 font-medium ${minP === p ? 'bg-graphite-800 text-ivory' : 'text-graphite-700'}`}>
                {p === 'low' ? 'All' : p === 'medium' ? 'Medium +' : 'High only'}
              </button>
            ))}
          </div>
          <div className="flex rounded-xl bg-graphite-900/8 p-0.5" role="radiogroup" aria-label="Time window">
            {WINDOWS.map((h) => (
              <button key={h} type="button" role="radio" aria-checked={hours === h} onClick={() => setHours(h)} className={`rounded-[10px] px-3 py-1.5 font-medium ${hours === h ? 'bg-graphite-800 text-ivory' : 'text-graphite-700'}`}>
                {h < 168 ? `${h} h` : '7 days'}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-2"><input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} className="accent-graphite-800" /> Open only</label>
          {data?.last_evaluation && <span className="ml-auto text-[12px] text-graphite-600">Rules last evaluated {timeAgo(data.last_evaluation.finished_at)}</span>}
        </motion.div>

        <section className="mt-4 space-y-3" aria-live="polite">
          {loading && !data && <Skeleton className="h-40" />}
          {error && !data && <ErrorState error={error} onRetry={refresh} />}
          {data && data.alerts.length === 0 && (
            <p className="rounded-xl bg-ivory/60 p-4 text-[14px]">No alerts match these filters. That does not mean there is no fire activity: check the Detections page.</p>
          )}
          {data?.alerts.map((a) => <AlertCard key={a.id} a={a} onAck={async () => { await apiPost(`/api/alerts/${encodeURIComponent(a.id)}/ack`); invalidate('/api/alerts'); }} />)}
        </section>

        {data && (
          <details className="mt-8 rounded-xl bg-ivory/60 p-4 text-[13.5px]">
            <summary className="cursor-pointer font-semibold">Alert rules</summary>
            <pre className="mt-2 whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-graphite-800">{data.rules}</pre>
            <p className="mt-2 text-graphite-700">{data.disclaimer}</p>
          </details>
        )}
      </motion.main>
    </div>
  )
}

function AlertCard({ a, onAck }: { a: AlertItem; onAck: () => Promise<void> }) {
  const P = PRIORITY[a.priority]
  const [acking, setAcking] = useState(false)
  const [ackError, setAckError] = useState<string | null>(null)
  return (
    <article className={`relative overflow-hidden rounded-xl border border-graphite-900/10 bg-ivory/70 p-4 pl-5 ${a.acknowledged_at ? 'opacity-70' : ''}`}>
      <span className="absolute inset-y-0 left-0 w-1.5" style={{ background: P.bar }} aria-hidden />
      <div className="flex flex-wrap items-center gap-2">
        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${P.chip}`}><P.icon className="size-3.5" aria-hidden /> {P.label}</span>
        <span className="rounded-full bg-graphite-900/8 px-2 py-0.5 text-[11px]">{a.kind === 'outlook' ? 'Fire-weather outlook' : 'Detection'}</span>
        {a.persistent_source && <span className="rounded-full bg-graphite-900/8 px-2 py-0.5 text-[11px]">Likely persistent heat source</span>}
        {a.acknowledged_at && <span className="inline-flex items-center gap-1 rounded-full bg-graphite-900/8 px-2 py-0.5 text-[11px]"><Check className="size-3" aria-hidden /> Acknowledged {timeAgo(a.acknowledged_at)}</span>}
        <span className="ml-auto text-[12px] text-graphite-600">{fmtIst(a.created_at)}</span>
      </div>
      <h2 className="mt-2 text-[16px] font-semibold text-graphite-950">{a.title}</h2>
      <ul className="mt-1 space-y-0.5 text-[13px] text-graphite-800">{a.rules.map((r) => <li key={r.id}><span className="font-mono text-[11px] text-graphite-500">{r.id}</span> {r.text}</li>)}</ul>
      {a.fire_stations && a.fire_stations.length > 0 && (
        <div className="mt-2 text-[12.5px]">
          <div className="font-medium">Nearest fire stations (OpenStreetMap, straight-line)</div>
          <ul className="mt-0.5 flex flex-wrap gap-x-4 gap-y-0.5">
            {a.fire_stations.map((s) => (
              <li key={s.id} className="flex items-center gap-1">
                <a href={s.osm_url} target="_blank" rel="noopener noreferrer" className="underline decoration-graphite-900/25 hover:decoration-graphite-900">{s.name ?? 'Fire station'}</a>
                <span className="font-mono text-graphite-600">{Math.round(s.distance_km ?? 0)} km</span>
                {s.phone && <span className="inline-flex items-center gap-0.5 font-mono"><Phone className="size-3" aria-hidden />{s.phone}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {a.lat !== null && a.lon !== null && (
          <>
            <Link to={`/dashboard?region=karnataka&lat=${a.lat.toFixed(4)}&lon=${a.lon.toFixed(4)}`} className="inline-flex items-center gap-1 rounded-lg bg-graphite-800 px-2.5 py-1.5 text-[12.5px] font-medium text-ivory hover:bg-graphite-700"><MapPin className="size-3.5" aria-hidden /> View on map</Link>
            {a.kind === 'detection' && a.cell_id && (
              <Link to={`/dashboard?region=karnataka&tool=spread&lat=${a.lat.toFixed(4)}&lon=${a.lon.toFixed(4)}`} className="inline-flex items-center gap-1 rounded-lg border border-graphite-900/20 px-2.5 py-1.5 text-[12.5px] font-medium hover:bg-graphite-900/6"><Flame className="size-3.5" aria-hidden /> Simulate spread</Link>
            )}
          </>
        )}
        {!a.acknowledged_at && (
          <button type="button" disabled={acking} onClick={async () => { setAcking(true); setAckError(null); try { await onAck() } catch (e) { setAckError((e as Error).message) } finally { setAcking(false) } }} className="inline-flex items-center gap-1 rounded-lg border border-graphite-900/20 px-2.5 py-1.5 text-[12.5px] font-medium hover:bg-graphite-900/6 disabled:opacity-50">
            <BellRing className="size-3.5" aria-hidden /> Acknowledge
          </button>
        )}
        {a.notify_status && <span className="text-[11.5px] text-graphite-600">Notification: {a.notify_status}</span>}
        {ackError && <span className="text-[11.5px] text-[#8a1a1a]" role="alert">{ackError}</span>}
      </div>
      <details className="mt-2 text-[12px] text-graphite-700">
        <summary className="cursor-pointer">Full message (as sent to configured recipients)</summary>
        <pre className="mt-1 whitespace-pre-wrap font-sans">{a.message}</pre>
      </details>
    </article>
  )
}
