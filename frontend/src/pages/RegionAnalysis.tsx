import { ArrowUpRight, Info, RotateCcw } from 'lucide-react'
import { motion } from 'motion/react'
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ClassDistribution, FactorBar, FactorTable, FwiTrend, WeatherMultiples } from '../components/charts'
import { NavBar } from '../components/NavBar'
import { SourceStatus } from '../components/SourceStatus'
import { ClassBadge, ErrorState, Metric, Skeleton, Tag } from '../components/ui'
import { useApi } from '../lib/api'
import { fmtCoord, fmtDate, fmtIst, num, timeAgo } from '../lib/format'
import { fadeUp, stagger } from '../lib/motion'
import { useFwiClasses } from '../lib/fwiClasses'
import { COMPOSITE_CLASSES, compositeClassFor, FACTOR_COLORS, FACTOR_META } from '../lib/risk'
import type { Factor, RegionSummary, RegionsResponse, RiskDetails } from '../lib/types'

export default function RegionAnalysis() {
  const [params, setParams] = useSearchParams()
  const regionId = params.get('region') ?? 'karnataka'
  const date = params.get('date')
  const regions = useApi<RegionsResponse>('/api/regions')
  const summary = useApi<RegionSummary>(`/api/region-summary?region=${regionId}${date ? `&date=${date}` : ''}`)
  const cellId = params.get('cell') ?? summary.data?.top_cells[0]?.id ?? null
  const region = regions.data?.regions.find((r) => r.id === regionId)

  const set = (k: string, v: string | null) => {
    const next = new URLSearchParams(params)
    if (v === null) next.delete(k)
    else next.set(k, v)
    setParams(next, { replace: true })
  }

  return (
    <div className="min-h-dvh bg-[#d9d4c7] text-graphite-900">
      <NavBar right={<SourceStatus dark />} />
      <main className="mx-auto max-w-[1400px] px-4 py-6 md:px-6 md:py-8">
        <motion.header variants={stagger} initial="hidden" animate="show" className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <motion.div variants={fadeUp} className="eyebrow text-graphite-500">Regional risk analysis</motion.div>
            <motion.h1 variants={fadeUp} className="font-display mt-1 text-3xl font-bold md:text-4xl">{region?.name ?? regionId}</motion.h1>
            <motion.p variants={fadeUp} className="mt-1 max-w-2xl text-sm text-graphite-600">
              Fire-weather conditions and contributing factors on a 0.25° grid. Values describe conditions; they do not predict where a fire will start.
            </motion.p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={regionId}
              onChange={(e) => setParams({ region: e.target.value })}
              className="rounded-xl border border-graphite-900/15 bg-ivory px-3 py-2 text-sm font-medium"
              aria-label="Region"
            >
              {(regions.data?.regions ?? []).map((r) => (
                <option key={r.id} value={r.id} disabled={!r.enabled}>
                  {r.name}{r.enabled ? '' : ' (not ingested)'}
                </option>
              ))}
            </select>
            <select
              value={summary.data?.date ?? ''}
              onChange={(e) => set('date', e.target.value)}
              className="rounded-xl border border-graphite-900/15 bg-ivory px-3 py-2 text-sm font-medium"
              aria-label="Date"
            >
              {(summary.data?.available_dates ?? []).map((d) => (
                <option key={d.date} value={d.date}>
                  {fmtDate(d.date)}{d.is_forecast ? ' (forecast)' : ''}
                </option>
              ))}
            </select>
          </div>
        </motion.header>

        {summary.error && !summary.data ? (
          <div className="mt-8 rounded-2xl bg-ivory p-6"><ErrorState error={summary.error} onRetry={summary.refresh} /></div>
        ) : !summary.data ? (
          <div className="mt-8 grid gap-4 md:grid-cols-4">{Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-28" />)}</div>
        ) : (
          <RegionBody s={summary.data} cellId={cellId} onCell={(id) => set('cell', id)} />
        )}
      </main>
    </div>
  )
}

function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <section className={`rounded-2xl border border-graphite-900/10 bg-ivory/80 p-4 shadow-[0_1px_0_rgba(255,255,255,0.6)_inset] md:p-5 ${className}`}>{children}</section>
}

function RegionBody({ s, cellId, onCell }: { s: RegionSummary; cellId: string | null; onCell: (id: string) => void }) {
  const fwiScheme = useFwiClasses(s.region)
  return (
    <>
      <div className="mt-6 flex flex-wrap items-center gap-2 text-[12px] text-graphite-600">
        {s.is_forecast ? <Tag tone="forecast">Forecast day</Tag> : <Tag tone="model">Model analysis</Tag>}
        <span>{s.cells} cells · {fmtDate(s.date)}</span>
      </div>
      <div className="mt-3 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card>
          <Metric label="Median FWI" value={num(s.fwi.stats?.median, 1)} hint={`range ${num(s.fwi.stats?.min, 1)} – ${num(s.fwi.stats?.max, 1)}`} />
        </Card>
        <Card>
          <Metric label="Max temperature (median)" value={num(s.weather.t_max?.median, 1)} unit="°C" hint={`max ${num(s.weather.t_max?.max, 1)} °C`} />
        </Card>
        <Card>
          <Metric label="Min humidity (median)" value={num(s.weather.rh_min?.median, 0)} unit="%" hint={`lowest ${num(s.weather.rh_min?.min, 0)} %`} />
        </Card>
        <Card>
          <Metric label="Detections in region" value={s.detections.last_24h} unit="24 h" hint={`${s.detections.last_7d} in 7 days · thermal anomalies`} />
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <ClassDistribution counts={s.fwi.class_counts} classes={fwiScheme.classes} total={s.cells} title="FWI class distribution" subtitle={`Cells per class. ${fwiScheme.note}`} />
        </Card>
        <Card>
          <ClassDistribution counts={s.composite.class_counts} classes={COMPOSITE_CLASSES} total={s.cells} title="Experimental composite distribution" subtitle="Cells without all five inputs are counted as insufficient data." />
          {Object.keys(s.composite.mean_contributions).length > 0 && (
            <div className="mt-3 text-[11.5px] text-graphite-600">
              Mean contribution:{' '}
              {Object.entries(s.composite.mean_contributions).map(([k, v]) => (
                <span key={k} className="mr-2 font-mono">{FACTOR_META[k].short} {v}</span>
              ))}
            </div>
          )}
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[360px_1fr]">
        <Card className="lg:max-h-[860px] lg:overflow-auto scroll-thin">
          <div className="text-sm font-semibold">Highest FWI cells</div>
          <p className="mt-0.5 text-[11.5px] text-graphite-600">Select a cell to inspect it.</p>
          <ul className="mt-3 space-y-1">
            {s.top_cells.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => onCell(c.id)}
                  aria-pressed={cellId === c.id}
                  className={`flex w-full items-center justify-between gap-2 rounded-xl px-2.5 py-2 text-left transition-colors ${cellId === c.id ? 'bg-graphite-800 text-ivory' : 'hover:bg-graphite-900/6'}`}
                >
                  <span className="font-mono text-[12px]">{fmtCoord(c.lat, c.lon, 3)}</span>
                  <span className="flex items-center gap-2">
                    <span className="font-mono text-[12px] tabular">{num(c.fwi, 1)}</span>
                    <ClassBadge name={c.fwi_class} size="sm" />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </Card>
        {cellId ? <CellAnalysis cellId={cellId} date={s.date} /> : <Card>No cell selected.</Card>}
      </div>
    </>
  )
}

function CellAnalysis({ cellId, date }: { cellId: string; date: string }) {
  const { data, error, refresh } = useApi<RiskDetails>(`/api/risk-details?cell_id=${encodeURIComponent(cellId)}&date=${date}`)
  const [weights, setWeights] = useState<Record<string, number> | null>(null)
  const effectiveWeights = weights ?? data?.weights ?? null
  const fwiScheme = useFwiClasses(data?.cell.region)

  const recomputed = useMemo(() => {
    if (!data?.composite || !effectiveWeights) return null
    const factors: Factor[] = data.composite.factors.map((f) => ({
      ...f,
      weight: effectiveWeights[f.key],
      contribution: f.score === null ? null : Math.round(f.score * effectiveWeights[f.key] * 10) / 10,
    }))
    const total = Object.values(effectiveWeights).reduce((a, b) => a + b, 0)
    const complete = factors.every((f) => f.score !== null)
    const raw = complete ? factors.reduce((a, f) => a + (f.score ?? 0) * f.weight, 0) : null
    const score = raw === null || total === 0 ? null : Math.round((raw / total) * 10) / 10
    return { factors, score, total }
  }, [data, effectiveWeights])

  if (error && !data) return <Card><ErrorState error={error} onRetry={refresh} /></Card>
  if (!data) return <Card><Skeleton className="h-96" /></Card>
  const modified = weights !== null

  return (
    <div className="grid min-w-0 gap-4">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="eyebrow text-graphite-500">Cell {data.cell.id}</div>
            <div className="font-display mt-1 text-2xl font-semibold">{fmtCoord(data.cell.lat, data.cell.lon, 3)}</div>
            <div className="mt-1 text-[12px] text-graphite-600">{fmtDate(data.date)} · {data.fwi?.is_forecast ? 'forecast' : 'model analysis'} · {Math.round(data.cell.overlap * 100)}% of cell inside region</div>
          </div>
          <div className="flex items-center gap-6">
            <div>
              <div className="eyebrow text-graphite-500">FWI</div>
              <div className="font-display text-4xl font-semibold tabular">{num(data.fwi?.fwi, 1)}</div>
              <ClassBadge name={data.fwi?.fwi_class} size="sm" />
            </div>
            <div>
              <div className="eyebrow text-graphite-500">Composite{modified ? ' (re-weighted)' : ''}</div>
              <div className="font-display text-4xl font-semibold tabular">{num(recomputed?.score ?? null, 0)}</div>
              <ClassBadge name={compositeClassFor(recomputed?.score ?? null)} kind="composite" size="sm" />
            </div>
            <Link to={`/dashboard?region=${data.cell.region}&cell=${encodeURIComponent(data.cell.id)}&date=${data.date}`} className="inline-flex items-center gap-1 rounded-xl bg-graphite-800 px-3 py-2 text-sm font-medium text-ivory hover:bg-graphite-700">
              On map <ArrowUpRight className="size-4" aria-hidden />
            </Link>
          </div>
        </div>
        <ul className="mt-4 space-y-1 border-t border-graphite-900/10 pt-3 text-[13px] leading-snug">
          {data.explanation.map((l, i) => <li key={i} className="flex gap-2"><Info className="mt-0.5 size-3.5 shrink-0 text-graphite-500" aria-hidden />{l}</li>)}
        </ul>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <FwiTrend series={data.fwi_series} selectedDate={data.date} classes={fwiScheme.classes} />
          <div className="mt-3 grid grid-cols-5 gap-2 border-t border-graphite-900/10 pt-3">
            {(['ffmc', 'dmc', 'dc', 'isi', 'bui'] as const).map((k) => (
              <Metric key={k} label={k.toUpperCase()} value={num(data.fwi?.[k] as number | null, k === 'isi' ? 1 : 0)} hint={data.percentiles?.[k] !== undefined ? `p${Math.round(data.percentiles[k])} in region` : undefined} />
            ))}
          </div>
        </Card>
        <Card>
          <WeatherMultiples series={data.weather_series} />
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          {data.composite && recomputed ? (
            <>
              <div className="mb-3">
                <div className="flex items-center justify-between">
                  <div className="text-sm font-semibold">Contributions to the experimental composite</div>
                  {modified && (
                    <button type="button" onClick={() => setWeights(null)} className="inline-flex items-center gap-1 text-[11.5px] text-graphite-600 hover:text-graphite-900">
                      <RotateCcw className="size-3" aria-hidden /> reset weights
                    </button>
                  )}
                </div>
                <div className="mt-2"><FactorBar factors={recomputed.factors} score={recomputed.score} /></div>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-graphite-700">
                  {recomputed.factors.map((f) => (
                    <span key={f.key} className="flex items-center gap-1.5">
                      <span className="inline-block size-2.5 rounded-[3px]" style={{ background: FACTOR_COLORS[f.key] }} aria-hidden />
                      {f.label} <span className="font-mono text-graphite-500">{num(f.contribution, 1)}</span>
                    </span>
                  ))}
                </div>
                {modified && (
                  <p className="mt-2 rounded-lg bg-[#3d5a6a]/12 px-2 py-1.5 text-[11.5px] text-[#2f4a58]">
                    Sensitivity view: weights changed locally (sum {recomputed.total.toFixed(2)}, score rescaled to 100). This explores how assumptions move the result. It does not change Incendium’s published values.
                  </p>
                )}
              </div>
              <FactorTable factors={recomputed.factors} onWeight={(k, w) => setWeights({ ...(effectiveWeights ?? {}), [k]: w })} />
            </>
          ) : (
            <p className="text-sm text-graphite-600">No composite available for this date.</p>
          )}
        </Card>
        <Card>
          <div className="text-sm font-semibold">Vegetation (Sentinel-2 L2A)</div>
          <p className="mt-0.5 text-[11.5px] text-graphite-600">10-day cloud-masked intervals. NDVI measures greenness; NDMI is a water-content proxy. Neither measures dead-fuel moisture.</p>
          <div className="scroll-thin mt-3 overflow-x-auto">
            <table className="w-full text-left text-[12px]">
              <thead className="text-graphite-600">
                <tr><th className="py-1 pr-3 font-medium">Interval</th><th className="py-1 pr-3 font-medium">NDVI</th><th className="py-1 pr-3 font-medium">NDMI</th><th className="py-1 font-medium">Valid pixels</th></tr>
              </thead>
              <tbody className="font-mono tabular">
                {data.vegetation_series.length === 0 && <tr><td colSpan={4} className="py-2 text-graphite-500">No vegetation statistics retrieved yet.</td></tr>}
                {data.vegetation_series.map((v) => (
                  <tr key={v.interval_from} className={`border-t border-graphite-900/8 ${(v.valid_fraction ?? 0) < 0.1 ? 'text-graphite-400' : ''}`}>
                    <td className="py-1 pr-3">{v.interval_from} → {v.interval_to}</td>
                    <td className="py-1 pr-3">{num(v.ndvi_mean, 3)}</td>
                    <td className="py-1 pr-3">{num(v.ndmi_mean, 3)}</td>
                    <td className="py-1">{v.valid_fraction === null ? '—' : `${Math.round(v.valid_fraction * 100)}%`}{(v.valid_fraction ?? 0) < 0.1 ? ' · unusable (cloud)' : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-5 text-sm font-semibold">Nearby satellite detections</div>
          <p className="mt-0.5 text-[11.5px] text-graphite-600">Within 30 km of the cell centre, last 7 days. Shown for context; they are not used in the risk scores.</p>
          <div className="scroll-thin mt-2 max-h-56 overflow-auto">
            <table className="w-full text-left text-[12px]">
              <thead className="sticky top-0 bg-ivory text-graphite-600">
                <tr><th className="py-1 pr-3 font-medium">Acquired</th><th className="py-1 pr-3 font-medium">Sat.</th><th className="py-1 pr-3 font-medium">Conf.</th><th className="py-1 pr-3 font-medium">FRP</th><th className="py-1 font-medium">Dist.</th></tr>
              </thead>
              <tbody className="font-mono tabular">
                {data.nearby_detections.length === 0 && <tr><td colSpan={5} className="py-2 text-graphite-500">None detected.</td></tr>}
                {data.nearby_detections.map((n) => (
                  <tr key={n.id} className="border-t border-graphite-900/8">
                    <td className="py-1 pr-3">{fmtIst(n.acq_datetime)}</td>
                    <td className="py-1 pr-3">{n.satellite}</td>
                    <td className="py-1 pr-3">{n.confidence}</td>
                    <td className="py-1 pr-3">{n.frp ?? '—'} MW</td>
                    <td className="py-1">{n.distance_km} km</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <Card>
        <div className="text-sm font-semibold">Uncertainty & provenance</div>
        <ul className="mt-2 grid gap-2 text-[12.5px] leading-snug text-graphite-700 md:grid-cols-2">
          <li>Weather values are model estimates for a ~9–25 km grid point{data.provenance.weather.model_grid_point ? ` (${data.provenance.weather.model_grid_point.lat.toFixed(2)}, ${data.provenance.weather.model_grid_point.lon.toFixed(2)}, ${Math.round(data.provenance.weather.model_grid_point.elevation_m)} m elevation)` : ''}. Local terrain effects are not resolved. Retrieved {timeAgo(data.provenance.weather.retrieved_at)}.</li>
          <li>FWI uses a 45-day spin-up from standard start values. {fwiScheme.note} Computed {timeAgo(data.provenance.fwi.computed_at)}.</li>
          <li>Vegetation intervals need ≥10% cloud-free pixels and may be up to 45 days old. Latest used: {data.vegetation ? `${data.vegetation.interval_from} → ${data.vegetation.interval_to}` : 'none'}.</li>
          <li>Composite weights and breakpoints are assumptions for interpretability, not calibrated values. Risk computed {timeAgo(data.provenance.risk_computed_at)}.</li>
        </ul>
      </Card>
    </div>
  )
}
