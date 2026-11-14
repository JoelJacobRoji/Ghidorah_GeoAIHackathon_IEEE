import { ArrowUpRight, Crosshair, Flame, Satellite, X } from 'lucide-react'
import { motion } from 'motion/react'
import { Link } from 'react-router-dom'
import { useApi } from '../lib/api'
import { compass, fmtCoord, fmtDate, fmtIst, fmtUtc, num, timeAgo } from '../lib/format'
import { MOTION } from '../lib/motion'
import { describeScheme, type FwiScheme, useFwiClasses } from '../lib/fwiClasses'
import { COMPOSITE_CLASSES, DETECTION_COLOR, ML_STOPS, NDMI_RAMP, NDMI_STOPS, NO_DATA_COLOR } from '../lib/risk'
import type { DetectionProps, RiskDetails } from '../lib/types'
import { FactorBar } from './charts'
import type { Metric } from './MapView'
import { ClassBadge, ErrorState, Metric as MetricBox, Skeleton, Tag } from './ui'

// --------------------------------------------------------------------------- legend
export function Legend({ metric, showDetections, showParticles = false, fwi = describeScheme(undefined) }: { metric: Metric; showDetections: boolean; showParticles?: boolean; fwi?: FwiScheme }) {
  const classes = metric === 'fwi' ? fwi.classes : metric === 'composite' ? COMPOSITE_CLASSES : null
  return (
    <div className="panel w-[min(300px,calc(100vw-32px))] p-3 text-graphite-900">
      <div className="flex items-center justify-between">
        <div className="eyebrow text-graphite-500">
          {metric === 'fwi' ? `Fire Weather Index · ${fwi.short}` : metric === 'composite' ? 'Experimental composite' : metric === 'ml' ? 'Detection likelihood · ML, experimental' : 'Vegetation water (NDMI)'}
        </div>
      </div>
      {classes ? (
        <ul className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
          {classes.map((c) => (
            <li key={c.name} className="flex items-center gap-2 text-[11.5px]">
              <span className="inline-block h-3 w-4 rounded-[3px]" style={{ background: c.color }} aria-hidden />
              <span>{c.name}</span>
              <span className="ml-auto font-mono text-[10px] text-graphite-500">≥{c.min}</span>
            </li>
          ))}
          <li className="flex items-center gap-2 text-[11.5px]">
            <span className="inline-block h-3 w-4 rounded-[3px] opacity-50" style={{ background: NO_DATA_COLOR }} aria-hidden />
            No data
          </li>
        </ul>
      ) : metric === 'ml' ? (
        <div className="mt-2">
          <div className="flex h-3 overflow-hidden rounded-[3px]">
            {ML_STOPS.map(([, c]) => <div key={c} className="flex-1" style={{ background: c }} />)}
          </div>
          <div className="mt-1 flex justify-between font-mono text-[10px] text-graphite-500">
            {ML_STOPS.map(([v]) => <span key={v}>{Math.round(v * 100)}%</span>)}
          </div>
          <p className="mt-1 text-[10.5px] leading-snug text-graphite-600">
            Modelled chance of ≥ 1 satellite detection in the cell that day (includes crop burning). Trained on Feb–May 2025, tested on 2026; outside Feb–May it is out of its training range. See Methodology.
          </p>
        </div>
      ) : (
        <div className="mt-2">
          <div className="flex h-3 overflow-hidden rounded-[3px]">
            {NDMI_RAMP.map((c) => <div key={c} className="flex-1" style={{ background: c }} />)}
          </div>
          <div className="mt-1 flex justify-between font-mono text-[10px] text-graphite-500">
            {NDMI_STOPS.map((s) => <span key={s}>{s}</span>)}
          </div>
          <p className="mt-1 text-[10.5px] text-graphite-600">Darker = more vegetation water. A proxy, not fuel moisture.</p>
        </div>
      )}
      {showDetections && (
        <div className="mt-2 flex items-center gap-2 border-t border-graphite-900/10 pt-2 text-[11.5px]">
          <span className="relative inline-flex size-3 items-center justify-center">
            <span className="absolute size-3 rounded-full opacity-40 blur-[2px]" style={{ background: DETECTION_COLOR }} />
            <span className="size-2 rounded-full ring-1 ring-ivory" style={{ background: DETECTION_COLOR }} />
          </span>
          Satellite thermal anomaly (size ∝ √FRP)
        </div>
      )}
      {showParticles && (
        <p className="mt-2 border-t border-graphite-900/10 pt-2 text-[10.5px] leading-snug text-graphite-600">
          <b className="text-graphite-800">Wind flow is illustrative.</b> Streaks follow Open-Meteo model 10 m wind for the current hour, interpolated between 0.25° cell centres. Speed is visually scaled, and it is not a wind simulation.
        </p>
      )}
      {metric === 'fwi' && <p className="mt-1.5 text-[10.5px] leading-snug text-graphite-600">{fwi.note}</p>}
    </div>
  )
}

const panelMotion = {
  initial: { opacity: 0, x: 24 },
  animate: { opacity: 1, x: 0, transition: { duration: MOTION.base, ease: MOTION.ease } },
  exit: { opacity: 0, x: 16, transition: { duration: MOTION.fast } },
}

function PanelHeader({ eyebrow, title, onClose, icon }: { eyebrow: string; title: string; onClose: () => void; icon: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="eyebrow flex items-center gap-1.5 text-graphite-500">{icon}{eyebrow}</div>
        <h2 className="font-display mt-1 text-lg font-semibold leading-tight text-graphite-900">{title}</h2>
      </div>
      <button type="button" onClick={onClose} className="rounded-lg p-1 text-graphite-600 hover:bg-graphite-900/10" aria-label="Close panel">
        <X className="size-4" />
      </button>
    </div>
  )
}

// --------------------------------------------------------------------------- cell detail
export function CellPanel({ cellId, date, onClose, onFlyTo }: { cellId: string; date: string | null; onClose: () => void; onFlyTo?: (lon: number, lat: number) => void }) {
  const { data, error, loading, refresh } = useApi<RiskDetails>(`/api/risk-details?cell_id=${encodeURIComponent(cellId)}${date ? `&date=${date}` : ''}`)
  return (
    <motion.aside {...panelMotion} className="panel scroll-thin flex max-h-full flex-col overflow-y-auto p-4" aria-label="Risk cell details">
      {loading && !data ? (
        <div className="space-y-3" aria-busy>
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : error && !data ? (
        <ErrorState error={error} onRetry={refresh} />
      ) : data ? (
        <CellBody d={data} onClose={onClose} onFlyTo={onFlyTo} />
      ) : null}
    </motion.aside>
  )
}

function CellBody({ d, onClose, onFlyTo }: { d: RiskDetails; onClose: () => void; onFlyTo?: (lon: number, lat: number) => void }) {
  const fwiScheme = useFwiClasses(d.cell.region)
  const f = d.fwi
  const w = d.weather
  const c = d.composite
  return (
    <>
      <PanelHeader eyebrow={`Grid cell · 0.25° · ${d.date ? fmtDate(d.date) : 'no date'}`} title={fmtCoord(d.cell.lat, d.cell.lon, 3)} onClose={onClose} icon={<Crosshair className="size-3" />} />
      <div className="mt-1 flex flex-wrap gap-1.5">
        {f?.is_forecast ? <Tag tone="forecast">Forecast day</Tag> : <Tag tone="model">Model analysis</Tag>}
        {d.cell.overlap < 0.999 && <Tag>{Math.round(d.cell.overlap * 100)}% inside region</Tag>}
      </div>

      <section className="mt-4 grid grid-cols-[auto_1fr] items-end gap-4 border-b border-graphite-900/10 pb-4">
        <div>
          <div className="eyebrow text-graphite-500">Fire Weather Index</div>
          <div className="font-display text-5xl font-semibold leading-none tabular text-graphite-900">{num(f?.fwi, 1)}</div>
        </div>
        <div className="pb-1">
          <ClassBadge name={f?.fwi_class} />
          <div className="mt-1 text-[10.5px] text-graphite-500">{fwiScheme.calibrated ? 'calibrated class' : 'prototype class'}</div>
        </div>
      </section>

      <section className="mt-3 grid grid-cols-5 gap-2">
        {(['ffmc', 'dmc', 'dc', 'isi', 'bui'] as const).map((k) => (
          <div key={k} className="rounded-lg bg-graphite-900/5 px-2 py-1.5">
            <div className="eyebrow text-[9.5px] text-graphite-500">{k.toUpperCase()}</div>
            <div className="font-mono text-sm tabular">{num(f?.[k] as number | null, k === 'isi' ? 1 : 0)}</div>
            {d.percentiles?.[k] !== undefined && <div className="text-[9.5px] text-graphite-500">p{Math.round(d.percentiles[k])}</div>}
          </div>
        ))}
      </section>
      <p className="mt-1 text-[10.5px] text-graphite-500">p = percentile among this region’s cells on the same day.</p>

      <section className="mt-4">
        <div className="flex items-baseline justify-between">
          <div className="eyebrow text-graphite-500">Experimental composite</div>
          {c?.score !== null && c?.score !== undefined ? (
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-semibold tabular">{num(c.score, 0)}/100</span>
              <ClassBadge name={c.class} kind="composite" size="sm" />
            </div>
          ) : (
            <Tag tone="warn">Insufficient data</Tag>
          )}
        </div>
        {c && (
          <div className="mt-2">
            <FactorBar factors={c.factors} score={c.score} />
            {c.missing.length > 0 && <p className="mt-1 text-[11px] text-burnt">Missing: {c.missing.map((k) => c.factors.find((x) => x.key === k)?.label).join(', ')}. No score is calculated.</p>}
          </div>
        )}
      </section>

      <section className="mt-4 grid grid-cols-2 gap-3 border-t border-graphite-900/10 pt-3">
        <MetricBox label="Max temp" value={num(w?.t_max, 1)} unit="°C" />
        <MetricBox label="Min humidity" value={num(w?.rh_min, 0)} unit="%" />
        <MetricBox label="Noon wind" value={num(w?.ws_noon, 0)} unit={`km/h ${compass(w?.wd_noon ?? null)}`} />
        <MetricBox label="Rain 24 h→noon" value={num(w?.rain24_noon, 1)} unit="mm" />
        <MetricBox label="NDVI" value={num(d.vegetation?.ndvi, 2)} hint={d.vegetation ? `${d.vegetation.interval_from} → ${d.vegetation.interval_to}` : 'no cloud-free interval'} />
        <MetricBox label="NDMI" value={num(d.vegetation?.ndmi, 2)} hint={d.vegetation ? `${Math.round((d.vegetation.valid_fraction ?? 0) * 100)}% valid pixels` : undefined} />
      </section>

      <section className="mt-4 border-t border-graphite-900/10 pt-3">
        <div className="eyebrow text-graphite-500">Why</div>
        <ul className="mt-1.5 space-y-1 text-[12.5px] leading-snug text-graphite-800">
          {d.explanation.map((line, i) => <li key={i} className="flex gap-2"><span className="mt-[7px] size-1 shrink-0 rounded-full bg-graphite-500" aria-hidden />{line}</li>)}
        </ul>
      </section>

      <section className="mt-4 border-t border-graphite-900/10 pt-3">
        <div className="flex items-center justify-between">
          <div className="eyebrow text-graphite-500">Detections within 30 km · 7 days</div>
          <span className="font-mono text-sm">{d.nearby_detections.length}</span>
        </div>
        {d.nearby_detections.slice(0, 4).map((n) => (
          <button
            key={n.id}
            type="button"
            onClick={() => onFlyTo?.(n.longitude, n.latitude)}
            className="mt-1.5 flex w-full items-center justify-between rounded-lg px-2 py-1 text-left text-[11.5px] hover:bg-graphite-900/6"
          >
            <span className="flex items-center gap-1.5"><Flame className="size-3 text-incendium" aria-hidden />{fmtIst(n.acq_datetime)}</span>
            <span className="font-mono text-graphite-600">{n.distance_km} km · {n.frp ?? '—'} MW</span>
          </button>
        ))}
      </section>

      <section className="mt-4 border-t border-graphite-900/10 pt-3 text-[10.5px] leading-relaxed text-graphite-500">
        <div className="eyebrow mb-1 text-graphite-500">Provenance</div>
        Weather: Open-Meteo model{d.provenance.weather.model_grid_point ? ` (grid point ${d.provenance.weather.model_grid_point.lat.toFixed(2)}, ${d.provenance.weather.model_grid_point.lon.toFixed(2)}, ${Math.round(d.provenance.weather.model_grid_point.elevation_m)} m)` : ''}, retrieved {timeAgo(d.provenance.weather.retrieved_at)}.<br />
        FWI computed {timeAgo(d.provenance.fwi.computed_at)}. Vegetation: Sentinel-2 L2A, retrieved {timeAgo(d.provenance.vegetation.retrieved_at)}.
      </section>

      <Link
        to={`/analysis?cell=${encodeURIComponent(d.cell.id)}${d.date ? `&date=${d.date}` : ''}`}
        className="mt-4 inline-flex items-center justify-center gap-1.5 rounded-xl bg-graphite-800 px-3 py-2 text-sm font-medium text-ivory transition-colors hover:bg-graphite-700"
      >
        Full analysis <ArrowUpRight className="size-4" aria-hidden />
      </Link>
    </>
  )
}

// --------------------------------------------------------------------------- detection detail
export function DetectionPanel({ d, onClose, onInspectCell, onSimulate }: { d: DetectionProps & { lon: number; lat: number }; onClose: () => void; onInspectCell?: (id: string) => void; onSimulate?: (lon: number, lat: number) => void }) {
  return (
    <motion.aside {...panelMotion} className="panel scroll-thin max-h-full overflow-y-auto p-4" aria-label="Detection details">
      <PanelHeader eyebrow="Satellite thermal anomaly" title={fmtCoord(d.lat, d.lon, 4)} onClose={onClose} icon={<Satellite className="size-3" />} />
      <p className="mt-2 text-[11.5px] leading-snug text-graphite-600">
        A VIIRS 375 m pixel flagged as containing a heat source. Not a confirmed wildfire: it may be agricultural burning, industry or another hot surface.
      </p>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2.5 text-[12.5px]">
        <Field k="Acquired (UTC)" v={fmtUtc(d.acq_datetime)} />
        <Field k="Acquired (IST)" v={fmtIst(d.acq_datetime)} />
        <Field k="Satellite / sensor" v={`${d.satellite} · ${d.instrument}`} />
        <Field k="Confidence" v={d.confidence ?? '—'} />
        <Field k="Fire radiative power" v={d.frp !== null ? `${d.frp} MW` : '—'} />
        <Field k="Day / night" v={d.daynight === 'D' ? 'Day' : d.daynight === 'N' ? 'Night' : '—'} />
        <Field k="Brightness I4 / I5" v={`${num(d.bright_ti4, 1)} / ${num(d.bright_ti5, 1)} K`} />
        <Field k="Pixel scan × track" v={`${num(d.scan, 2)} × ${num(d.track, 2)} km`} />
        <Field k="State" v={d.state ?? 'outside Indian state polygons'} />
        <Field k="Product" v={`${d.source} v${d.version ?? '?'}`} />
      </dl>
      <p className="mt-3 text-[10.5px] text-graphite-500">Retrieved from NASA FIRMS {timeAgo(d.retrieved_at)}.</p>
      {d.cell_id && onInspectCell && (
        <button type="button" onClick={() => onInspectCell(d.cell_id!)} className="mt-3 inline-flex items-center gap-1.5 rounded-xl bg-graphite-800 px-3 py-2 text-sm font-medium text-ivory hover:bg-graphite-700">
          Inspect conditions in this cell <ArrowUpRight className="size-4" aria-hidden />
        </button>
      )}
      {d.cell_id && onSimulate && (
        <button type="button" onClick={() => onSimulate(d.lon, d.lat)} className="ml-2 mt-3 inline-flex items-center gap-1.5 rounded-xl border border-graphite-900/20 px-3 py-2 text-sm font-medium text-graphite-900 hover:bg-graphite-900/6">
          <Flame className="size-4" aria-hidden /> Simulate spread
        </button>
      )}
    </motion.aside>
  )
}

function Field({ k, v }: { k: string; v: string }) {
  return (
    <div className="min-w-0">
      <dt className="eyebrow text-[9.5px] text-graphite-500">{k}</dt>
      <dd className="mt-0.5 truncate font-mono text-[12px] text-graphite-900" title={v}>{v}</dd>
    </div>
  )
}
