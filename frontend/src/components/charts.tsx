import { Table2, LineChart as LineIcon } from 'lucide-react'
import { useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { fmtDate, num } from '../lib/format'
import { useReducedMotion } from '../lib/motion'
import { FACTOR_COLORS, FACTOR_META, FWI_CLASSES, textOn } from '../lib/risk'
import type { Factor, FwiRow, WeatherDay } from '../lib/types'

const INK = '#232d30'
const INK_2 = '#475a5f'
const MUTED = '#7b8a8c'
const GRID = 'rgba(35,45,48,0.10)'
const AXIS = 'rgba(35,45,48,0.28)'
const SERIES = '#993b2c'

/** Frame with title, chart/table switch (every chart has a table view). */
export function ChartFrame({
  title,
  subtitle,
  table,
  children,
  right,
}: {
  title: string
  subtitle?: ReactNode
  table: { columns: string[]; rows: (string | number)[][] }
  children: ReactNode
  right?: ReactNode
}) {
  const [asTable, setAsTable] = useState(false)
  const id = useId()
  return (
    <figure className="min-w-0" aria-labelledby={id}>
      <figcaption className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div id={id} className="text-sm font-semibold text-graphite-900">{title}</div>
          {subtitle && <div className="mt-0.5 text-[11.5px] leading-snug text-graphite-600">{subtitle}</div>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {right}
          <button
            type="button"
            onClick={() => setAsTable((t) => !t)}
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-graphite-600 hover:bg-graphite-900/8"
            aria-pressed={asTable}
          >
            {asTable ? <LineIcon className="size-3.5" aria-hidden /> : <Table2 className="size-3.5" aria-hidden />}
            {asTable ? 'Chart' : 'Table'}
          </button>
        </div>
      </figcaption>
      <div className="mt-2">
        {asTable ? (
          <div className="scroll-thin max-h-64 overflow-auto rounded-lg border border-graphite-900/10">
            <table className="w-full text-left text-[11.5px]">
              <thead className="sticky top-0 bg-stone-200 text-graphite-700">
                <tr>{table.columns.map((c) => <th key={c} className="px-2 py-1.5 font-medium">{c}</th>)}</tr>
              </thead>
              <tbody className="font-mono tabular text-graphite-800">
                {table.rows.map((r, i) => (
                  <tr key={i} className="border-t border-graphite-900/8">{r.map((v, j) => <td key={j} className="px-2 py-1">{v}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          children
        )}
      </div>
    </figure>
  )
}

function TipBox({ children }: { children: ReactNode }) {
  return <div className="rounded-lg bg-graphite-900 px-2.5 py-1.5 text-[11.5px] text-ivory shadow-lg">{children}</div>
}

const shortDate = (d: string) => d.slice(5).replace('-', '/')

// --------------------------------------------------------------------------- FWI trend
export function FwiTrend({ series, selectedDate, height = 170, classes = FWI_CLASSES, calibrated = false }: { series: FwiRow[]; selectedDate?: string | null; height?: number; classes?: { name: string; min: number; color: string }[]; calibrated?: boolean }) {
  const reduce = useReducedMotion()
  const rows = series.map((r) => ({
    date: r.date,
    obs: r.is_forecast ? null : r.fwi,
    fc: r.is_forecast ? r.fwi : null,
    fwi: r.fwi,
    cls: r.fwi_class,
    forecast: !!r.is_forecast,
  }))
  // Join the dashed forecast segment to the last analysed point.
  const lastObs = rows.map((r) => r.obs !== null).lastIndexOf(true)
  if (lastObs >= 0 && lastObs < rows.length - 1) rows[lastObs].fc = rows[lastObs].obs
  const max = Math.max(25, ...rows.map((r) => r.fwi ?? 0))
  const bands = classes.filter((c) => c.min > 0 && c.min <= max * 1.15)
  return (
    <ChartFrame
      title="Fire Weather Index"
      subtitle={`Daily, from model weather at local noon. Dashed = forecast. Reference lines = ${calibrated ? 'calibrated' : 'prototype'} class thresholds.`}
      table={{
        columns: ['Date', 'FWI', 'Class', 'Kind'],
        rows: series.map((r) => [r.date, num(r.fwi, 1), r.fwi_class ?? '—', r.is_forecast ? 'forecast' : 'model analysis']),
      }}
    >
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 64, bottom: 0, left: -18 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="date" tickFormatter={shortDate} tick={{ fontSize: 10, fill: MUTED }} axisLine={{ stroke: AXIS }} tickLine={false} minTickGap={18} />
            <YAxis tick={{ fontSize: 10, fill: MUTED }} axisLine={false} tickLine={false} domain={[0, Math.ceil(max * 1.15)]} />
            {bands.map((b) => (
              <ReferenceLine
                key={b.name}
                y={b.min}
                stroke={b.color}
                strokeDasharray="2 3"
                strokeOpacity={0.7}
                label={{ value: b.name, position: 'right', fontSize: 9.5, fill: INK_2 }}
              />
            ))}
            {selectedDate && <ReferenceLine x={selectedDate} stroke={INK} strokeOpacity={0.35} />}
            <Tooltip
              cursor={{ stroke: INK, strokeOpacity: 0.3, strokeWidth: 1 }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                const p = payload[0].payload as (typeof rows)[number]
                return (
                  <TipBox>
                    <div className="font-medium">{fmtDate(p.date)}</div>
                    <div className="font-mono">FWI {num(p.fwi, 1)} · {p.cls ?? 'no data'}</div>
                    <div className="text-ivory/60">{p.forecast ? 'Forecast' : 'Model analysis'}</div>
                  </TipBox>
                )
              }}
            />
            <Line dataKey="obs" stroke={SERIES} strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: '#f3f1ea', strokeWidth: 2 }} isAnimationActive={!reduce} connectNulls={false} />
            <Line dataKey="fc" stroke={SERIES} strokeWidth={2} strokeDasharray="4 4" dot={false} activeDot={{ r: 4 }} isAnimationActive={!reduce} connectNulls={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </ChartFrame>
  )
}

// --------------------------------------------------------------------------- factor contributions
/** One bar segment. Its label is shown only when it fits inside the segment's current
 *  width (measured, so it adapts to the panel size); otherwise the segment stays
 *  unlabelled and the factor remains in the tooltip and the bar's accessible name. */
function FactorSegment({ f }: { f: Factor }) {
  const ref = useRef<HTMLDivElement>(null)
  const [fits, setFits] = useState(true)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const check = () => setFits(el.scrollWidth <= el.clientWidth)
    check()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(check)
    ro.observe(el)
    return () => ro.disconnect()
  }, [f.contribution])
  return (
    <div
      ref={ref}
      className="flex min-w-0 items-center justify-center overflow-hidden whitespace-nowrap px-1 text-[10.5px] font-semibold transition-[width] duration-500"
      style={{ width: `${f.contribution}%`, background: FACTOR_COLORS[f.key], color: textOn(FACTOR_COLORS[f.key]) }}
      title={`${f.label}: ${f.contribution} pts (score ${f.score} × weight ${f.weight})`}
    >
      {/* Kept in the layout while hidden, so the fit can be re-measured when the width changes. */}
      <span className={fits ? '' : 'invisible'}>{FACTOR_META[f.key].short}</span>
    </div>
  )
}

export function FactorBar({ factors, score }: { factors: Factor[]; score: number | null }) {
  const present = factors.filter((f) => f.contribution !== null)
  return (
    <div>
      <div className="flex h-7 w-full gap-[2px] overflow-hidden rounded-[4px] bg-graphite-900/8" role="img" aria-label={`Composite ${score ?? 'not available'}: ${present.map((f) => `${f.label} ${f.contribution}`).join(', ')}`}>
        {present.map((f) => ((f.contribution ?? 0) > 0 ? <FactorSegment key={f.key} f={f} /> : null))}
      </div>
      <div className="mt-1 flex justify-between font-mono text-[10px] text-graphite-500">
        <span>0</span>
        <span>25</span>
        <span>50</span>
        <span>75</span>
        <span>100</span>
      </div>
      {/* Key: identifies every segment, including narrow ones whose label is hidden (no hover on touch screens). */}
      <ul className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-graphite-700" aria-hidden>
        {present.map((f) => (
          <li key={f.key} className="flex items-center gap-1">
            <span className="size-2 rounded-[2px]" style={{ background: FACTOR_COLORS[f.key] }} />
            {FACTOR_META[f.key].short} <span className="font-mono tabular text-graphite-500">{num(f.contribution, 1)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function FactorTable({ factors, onWeight }: { factors: Factor[]; onWeight?: (k: string, w: number) => void }) {
  return (
    <ChartFrame
      title="Factor breakdown"
      subtitle="Each factor normalised to 0–100 by a documented linear rule, then weighted."
      table={{
        columns: ['Factor', 'Input value', 'Score', 'Weight', 'Points'],
        rows: factors.map((f) => [f.label, f.value === null ? 'missing' : `${f.value} ${f.unit}`, num(f.score, 0), f.weight.toFixed(2), num(f.contribution, 1)]),
      }}
    >
      <ul className="space-y-2.5">
        {factors.map((f) => (
          <li key={f.key}>
            <div className="flex items-baseline justify-between gap-2 text-[12.5px]">
              <span className="flex items-center gap-2 font-medium text-graphite-900">
                <span className="inline-block size-2.5 rounded-[3px]" style={{ background: FACTOR_COLORS[f.key] }} aria-hidden />
                {f.label}
              </span>
              <span className="font-mono tabular text-graphite-700">
                {f.value === null ? <span className="text-burnt">missing</span> : `${f.value} ${f.unit}`}
              </span>
            </div>
            <div className="mt-1 flex items-center gap-2">
              <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-graphite-900/10">
                <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${f.score ?? 0}%`, background: FACTOR_COLORS[f.key] }} />
              </div>
              <span className="w-24 text-right font-mono text-[11px] text-graphite-600 tabular">
                {num(f.score, 0)} × {f.weight.toFixed(2)} = <b className="text-graphite-900">{num(f.contribution, 1)}</b>
              </span>
            </div>
            <div className="mt-0.5 text-[10.5px] text-graphite-500">{f.input} · {f.rule}</div>
            {f.note && <div className="mt-0.5 text-[10.5px] text-burnt">{f.note}</div>}
            {onWeight && (
              <label className="mt-1 flex items-center gap-2 text-[10.5px] text-graphite-600">
                weight
                <input
                  type="range"
                  min={0}
                  max={0.6}
                  step={0.05}
                  value={f.weight}
                  onChange={(e) => onWeight(f.key, Number(e.target.value))}
                  className="h-1 flex-1 accent-graphite-700"
                  aria-label={`Weight for ${f.label}`}
                />
              </label>
            )}
          </li>
        ))}
      </ul>
    </ChartFrame>
  )
}

// --------------------------------------------------------------------------- weather small multiples
function Mini({
  data,
  dataKey,
  label,
  unit,
  kind = 'line',
}: {
  data: WeatherDay[]
  dataKey: keyof WeatherDay
  label: string
  unit: string
  kind?: 'line' | 'bar'
}) {
  const reduce = useReducedMotion()
  const last = [...data].reverse().find((d) => !d.is_forecast && d[dataKey] !== null)
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between">
        <span className="text-[11px] font-medium text-graphite-700">{label}</span>
        <span className="font-mono text-[11px] text-graphite-900 tabular">{last ? num(last[dataKey] as number, 1, unit) : '—'}</span>
      </div>
      <div className="h-14">
        <ResponsiveContainer width="100%" height="100%">
          {kind === 'bar' ? (
            <BarChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }} barCategoryGap={1}>
              <XAxis dataKey="date" hide />
              <YAxis hide domain={[0, 'auto']} />
              <Tooltip cursor={{ fill: 'rgba(35,45,48,0.08)' }} content={({ active, payload }) => (active && payload?.length ? <TipBox>{fmtDate((payload[0].payload as WeatherDay).date)} · {num(payload[0].value as number, 1, unit)}{(payload[0].payload as WeatherDay).is_forecast ? ' (forecast)' : ''}</TipBox> : null)} />
              <Bar dataKey={dataKey as string} fill="#3a6db5" radius={[2, 2, 0, 0]} isAnimationActive={!reduce} />
            </BarChart>
          ) : (
            <LineChart data={data} margin={{ top: 4, right: 2, bottom: 0, left: 2 }}>
              <XAxis dataKey="date" hide />
              <YAxis hide domain={['auto', 'auto']} />
              <Tooltip cursor={{ stroke: INK, strokeOpacity: 0.3 }} content={({ active, payload }) => (active && payload?.length ? <TipBox>{fmtDate((payload[0].payload as WeatherDay).date)} · {num(payload[0].value as number, 1, unit)}{(payload[0].payload as WeatherDay).is_forecast ? ' (forecast)' : ''}</TipBox> : null)} />
              <Line dataKey={dataKey as string} stroke={INK_2} strokeWidth={1.6} dot={false} isAnimationActive={!reduce} />
            </LineChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  )
}

export function WeatherMultiples({ series }: { series: WeatherDay[] }) {
  return (
    <ChartFrame
      title="Weather inputs (model)"
      subtitle="Open-Meteo best-match model at the cell centre. Each panel has its own scale."
      table={{
        columns: ['Date', 'Tmax °C', 'RHmin %', 'Wind max km/h', 'Rain 24h→noon mm', 'Kind'],
        rows: series.map((d) => [d.date, num(d.t_max, 1), num(d.rh_min, 0), num(d.ws_max, 1), num(d.rain24_noon, 1), d.is_forecast ? 'forecast' : 'model']),
      }}
    >
      <div className="grid grid-cols-2 gap-x-4 gap-y-2">
        <Mini data={series} dataKey="t_max" label="Max temperature" unit="°C" />
        <Mini data={series} dataKey="rh_min" label="Min humidity" unit="%" />
        <Mini data={series} dataKey="ws_max" label="Max wind" unit="km/h" />
        <Mini data={series} dataKey="rain24_noon" label="Rain, 24 h to noon" unit="mm" kind="bar" />
      </div>
    </ChartFrame>
  )
}

// --------------------------------------------------------------------------- class distribution
export function ClassDistribution({
  counts,
  classes,
  total,
  title,
  subtitle,
}: {
  counts: Record<string, number>
  classes: { name: string; color: string }[]
  total: number
  title: string
  subtitle?: string
}) {
  const items = [...classes.map((c) => ({ ...c, n: counts[c.name] ?? 0 })), { name: 'Insufficient data', color: '#9aa5a5', n: counts['Insufficient data'] ?? 0 }].filter((i) => i.n > 0)
  return (
    <ChartFrame title={title} subtitle={subtitle} table={{ columns: ['Class', 'Cells', 'Share'], rows: items.map((i) => [i.name, i.n, `${((100 * i.n) / Math.max(total, 1)).toFixed(0)}%`]) }}>
      <div className="flex h-8 w-full gap-[2px] overflow-hidden rounded-[4px]" role="img" aria-label={items.map((i) => `${i.name}: ${i.n} cells`).join(', ')}>
        {items.map((i) => (
          <div key={i.name} className="flex items-center justify-center text-[10.5px] font-semibold" style={{ width: `${(100 * i.n) / Math.max(total, 1)}%`, background: i.color, color: textOn(i.color) }} title={`${i.name}: ${i.n}`}>
            {(100 * i.n) / Math.max(total, 1) > 9 ? i.n : ''}
          </div>
        ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-graphite-700">
        {items.map((i) => (
          <li key={i.name} className="flex items-center gap-1.5">
            <span className="inline-block size-2.5 rounded-[3px]" style={{ background: i.color }} aria-hidden />
            {i.name} <span className="font-mono text-graphite-500">{i.n}</span>
          </li>
        ))}
      </ul>
    </ChartFrame>
  )
}

// --------------------------------------------------------------------------- daily detections
export function DailyDetections({ data, dark = false, height = 110 }: { data: { day: string; n: number }[]; dark?: boolean; height?: number }) {
  const reduce = useReducedMotion()
  return (
    <div style={{ height }} role="img" aria-label={`Detections per UTC day: ${data.map((d) => `${d.day} ${d.n}`).join(', ')}`}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }} barCategoryGap={3}>
          <XAxis dataKey="day" tickFormatter={shortDate} tick={{ fontSize: 9.5, fill: dark ? 'rgba(232,229,219,0.5)' : MUTED }} axisLine={false} tickLine={false} minTickGap={12} />
          <YAxis hide />
          <Tooltip
            cursor={{ fill: dark ? 'rgba(232,229,219,0.08)' : 'rgba(35,45,48,0.08)' }}
            content={({ active, payload }) => (active && payload?.length ? <TipBox>{(payload[0].payload as { day: string }).day} (UTC) · {payload[0].value as number} detections</TipBox> : null)}
          />
          <Bar dataKey="n" fill="#e77943" radius={[4, 4, 0, 0]} isAnimationActive={!reduce} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
