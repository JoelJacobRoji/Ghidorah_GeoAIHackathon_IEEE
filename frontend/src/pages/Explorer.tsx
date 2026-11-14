import { ArrowDownUp, Download, Filter } from 'lucide-react'
import { lazy, Suspense, useMemo, useState } from 'react'
import type { FlyTarget } from '../components/MapView'
import { NavBar } from '../components/NavBar'
import { SourceStatus } from '../components/SourceStatus'
import { ErrorState, Skeleton, Tag } from '../components/ui'
import { useApi } from '../lib/api'
import { fmtIst, fmtUtc, timeAgo } from '../lib/format'
import { hasWebGL } from '../lib/motion'
import type { DetectionProps, HotspotsResponse, PointFeature, RegionsResponse } from '../lib/types'

const MapView = lazy(() => import('../components/MapView'))
const PAGE = 50

type SortKey = 'time' | 'frp'

export default function Explorer() {
  const [hours, setHours] = useState(48)
  const [conf, setConf] = useState<'' | 'nominal' | 'high'>('')
  const [sat, setSat] = useState<'' | 'NOAA-20' | 'NOAA-21'>('')
  const [state, setState] = useState('')
  const [sort, setSort] = useState<SortKey>('time')
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)
  const [fly, setFly] = useState<FlyTarget | null>(null)

  const qs = new URLSearchParams({ hours: String(hours) })
  if (conf) qs.set('min_confidence', conf)
  if (sat) qs.set('satellite', sat)
  if (state) qs.set('state', state)
  const { data, error, loading, refresh } = useApi<HotspotsResponse>(`/api/hotspots?${qs}`)
  const regions = useApi<RegionsResponse>('/api/regions')
  const summary = useApi<{ detections: { by_state_24h: { state: string; n: number }[] } }>('/api/summary')

  const rows = useMemo(() => {
    const fs = [...(data?.features ?? [])]
    if (sort === 'frp') fs.sort((a, b) => (b.properties.frp ?? -1) - (a.properties.frp ?? -1))
    return fs
  }, [data, sort])
  const pages = Math.max(1, Math.ceil(rows.length / PAGE))
  const view = rows.slice(page * PAGE, page * PAGE + PAGE)
  const states = summary.data?.detections.by_state_24h.map((s) => s.state).filter((s) => !s.startsWith('Outside')) ?? []

  function pick(f: PointFeature<DetectionProps>) {
    setSelected(f.properties.id)
    setFly({ center: f.geometry.coordinates, zoom: 10, key: f.properties.id })
  }

  function exportCsv() {
    const cols = ['id', 'acq_datetime', 'latitude', 'longitude', 'satellite', 'instrument', 'confidence', 'frp', 'bright_ti4', 'bright_ti5', 'daynight', 'state', 'source', 'version']
    const lines = [cols.join(',')].concat(
      rows.map((f) => {
        const p = f.properties as unknown as Record<string, unknown>
        const vals = cols.map((c) => (c === 'latitude' ? f.geometry.coordinates[1] : c === 'longitude' ? f.geometry.coordinates[0] : p[c] ?? ''))
        return vals.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')
      }),
    )
    const blob = new Blob([lines.join('\n') + '\n# Source: NASA FIRMS VIIRS NRT (NOAA-20/21). Thermal anomalies, not confirmed fires.\n'], { type: 'text/csv' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `incendium-detections-${hours}h.csv`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <div className="min-h-dvh bg-[#d9d4c7] text-graphite-900">
      <NavBar right={<SourceStatus dark />} />
      <main className="mx-auto max-w-[1400px] px-4 py-6 md:px-6 md:py-8">
        <div className="eyebrow text-graphite-500">Fire detection explorer</div>
        <h1 className="font-display mt-1 text-3xl font-bold md:text-4xl">Satellite thermal anomalies</h1>
        <p className="mt-1 max-w-3xl text-sm text-graphite-600">
          Each row is one VIIRS 375 m detection from NASA FIRMS (NOAA-20 / NOAA-21, near-real-time). Rows are shown individually and never merged into “incidents”. A detection is a heat signal, not a confirmed wildfire.
        </p>

        <div className="mt-5 flex flex-wrap items-end gap-3 rounded-2xl border border-graphite-900/10 bg-ivory/80 p-3">
          <Filter className="mb-2 size-4 text-graphite-500" aria-hidden />
          <Select label="Window" value={String(hours)} onChange={(v) => { setHours(Number(v)); setPage(0) }} options={[['6', '6 hours'], ['24', '24 hours'], ['48', '48 hours'], ['168', '7 days'], ['336', '14 days']]} />
          <Select label="Confidence" value={conf} onChange={(v) => { setConf(v as typeof conf); setPage(0) }} options={[['', 'All'], ['nominal', 'Nominal + high'], ['high', 'High only']]} />
          <Select label="Satellite" value={sat} onChange={(v) => { setSat(v as typeof sat); setPage(0) }} options={[['', 'Both'], ['NOAA-20', 'NOAA-20'], ['NOAA-21', 'NOAA-21']]} />
          <Select label="State (top, last 24 h)" value={state} onChange={(v) => { setState(v); setPage(0) }} options={[['', 'All of window'], ...states.map((s) => [s, s] as [string, string])]} />
          <button type="button" onClick={() => setSort((s) => (s === 'time' ? 'frp' : 'time'))} className="inline-flex items-center gap-1.5 rounded-lg bg-graphite-900/8 px-3 py-2 text-[12.5px] font-medium hover:bg-graphite-900/12">
            <ArrowDownUp className="size-3.5" aria-hidden /> Sort: {sort === 'time' ? 'newest' : 'highest FRP'}
          </button>
          <button type="button" onClick={exportCsv} disabled={!rows.length} className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-graphite-800 px-3 py-2 text-[12.5px] font-medium text-ivory hover:bg-graphite-700 disabled:opacity-40">
            <Download className="size-3.5" aria-hidden /> CSV
          </button>
        </div>

        <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_440px]">
          <section className="min-w-0 rounded-2xl border border-graphite-900/10 bg-ivory/80" aria-live="polite">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-graphite-900/10 px-4 py-2.5 text-[12px] text-graphite-600">
              <span>
                {data ? <><b className="font-mono text-graphite-900">{data.meta.count.toLocaleString()}</b> detections · {fmtUtc(data.meta.since)} → {fmtUtc(data.meta.until)}</> : 'Loading…'}
                {data?.meta.truncated && <Tag tone="warn">truncated at limit</Tag>}
              </span>
              {data && <span>FIRMS last updated {timeAgo(data.meta.provider_status.last_success)}</span>}
            </div>
            {error && !data ? (
              <div className="p-4"><ErrorState error={error} onRetry={refresh} /></div>
            ) : loading && !data ? (
              <div className="space-y-2 p-4">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-7" />)}</div>
            ) : rows.length === 0 ? (
              <p className="p-6 text-sm text-graphite-600">No detections match these filters in this window.</p>
            ) : (
              <div className="scroll-thin overflow-x-auto">
                <table className="w-full min-w-[760px] text-left text-[12.5px]">
                  <thead className="bg-stone-200/60 text-graphite-600">
                    <tr>
                      {['Acquired (IST)', 'UTC', 'Lat, Lon', 'Satellite', 'Conf.', 'FRP (MW)', 'D/N', 'State'].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}
                    </tr>
                  </thead>
                  <tbody className="font-mono tabular">
                    {view.map((f) => {
                      const p = f.properties
                      return (
                        <tr
                          key={p.id}
                          tabIndex={0}
                          onClick={() => pick(f)}
                          onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && pick(f)}
                          aria-selected={selected === p.id}
                          className={`cursor-pointer border-t border-graphite-900/8 transition-colors ${selected === p.id ? 'bg-incendium/15' : 'hover:bg-graphite-900/5'}`}
                        >
                          <td className="px-3 py-1.5">{fmtIst(p.acq_datetime)}</td>
                          <td className="px-3 py-1.5 text-graphite-600">{p.acq_datetime.slice(11, 16)}</td>
                          <td className="px-3 py-1.5">{f.geometry.coordinates[1].toFixed(4)}, {f.geometry.coordinates[0].toFixed(4)}</td>
                          <td className="px-3 py-1.5">{p.satellite}</td>
                          <td className="px-3 py-1.5">{p.confidence ?? '—'}</td>
                          <td className="px-3 py-1.5">{p.frp ?? '—'}</td>
                          <td className="px-3 py-1.5">{p.daynight ?? '—'}</td>
                          <td className="px-3 py-1.5 font-sans">{p.state ?? <span className="text-graphite-400">outside states</span>}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {rows.length > PAGE && (
              <div className="flex items-center justify-between border-t border-graphite-900/10 px-4 py-2 text-[12px]">
                <button type="button" disabled={page === 0} onClick={() => setPage((p) => p - 1)} className="rounded-lg px-2 py-1 hover:bg-graphite-900/8 disabled:opacity-40">← Previous</button>
                <span className="text-graphite-600">Page {page + 1} of {pages}</span>
                <button type="button" disabled={page >= pages - 1} onClick={() => setPage((p) => p + 1)} className="rounded-lg px-2 py-1 hover:bg-graphite-900/8 disabled:opacity-40">Next →</button>
              </div>
            )}
          </section>
          <section className="relative h-[420px] overflow-hidden rounded-2xl border border-graphite-900/10 lg:sticky lg:top-20 lg:h-[calc(100dvh-140px)]" aria-label="Map of filtered detections">
            {hasWebGL() ? (
              <Suspense fallback={<div className="grid h-full place-items-center text-sm text-graphite-600">Loading map…</div>}>
                <MapView
                  regions={regions.data?.regions ?? []}
                  hotspots={data}
                  layers={{ detections: true, risk: false, wind: false, outline: true, extrude: false }}
                  metric="fwi"
                  mode3d={false}
                  selectedDetectionId={selected}
                  onSelectDetection={(d) => d && setSelected(d.id)}
                  flyTo={fly}
                />
              </Suspense>
            ) : (
              <div className="grid h-full place-items-center p-6 text-center text-sm text-graphite-600">Map unavailable (WebGL not supported). The table remains fully usable.</div>
            )}
          </section>
        </div>
      </main>
    </div>
  )
}

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="eyebrow text-[9.5px] text-graphite-500">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} className="rounded-lg border border-graphite-900/15 bg-ivory px-2.5 py-1.5 text-[12.5px]">
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  )
}
