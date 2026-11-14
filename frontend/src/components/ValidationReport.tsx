import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, ErrorBar, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useApi } from '../lib/api'
import { fmtIst } from '../lib/format'
import { useReducedMotion } from '../lib/motion'
import { COMPOSITE_CLASSES, FWI_CLASSES, NO_DATA_COLOR } from '../lib/risk'
import { ChartFrame } from './charts'
import { ErrorState, Skeleton } from './ui'

/* eslint-disable @typescript-eslint/no-explicit-any */
type Report = Record<string, any>

const pct = (x: number | null | undefined, d = 2) => (x === null || x === undefined ? '—' : `${(x * 100).toFixed(d)} %`)
const f3 = (x: number | null | undefined) => (x === null || x === undefined ? '—' : x.toFixed(3))
// Short axis labels so six classes fit side by side; the table view and tooltip keep full names.
const shortClass = (c: string) => c.replace(/^Very /, 'V. ')

function RateChart({ rows, classes, title, subtitle }: { rows: any[]; classes: { name: string; color: string }[]; title: string; subtitle: string }) {
  const reduce = useReducedMotion()
  const data = rows.map((r) => ({
    ...r,
    pct: r.rate * 100,
    err: [Math.max(0, (r.rate - (r.ci_low ?? r.rate)) * 100), Math.max(0, ((r.ci_high ?? r.rate) - r.rate) * 100)],
    fill: classes.find((c) => c.name === r.class)?.color ?? NO_DATA_COLOR,
  }))
  return (
    <ChartFrame
      title={title}
      subtitle={subtitle}
      table={{
        columns: ['Class', 'Cell-days', 'With detection', 'Rate', '95 % CI', 'Lift'],
        rows: rows.map((r) => [r.class, r.cell_days.toLocaleString(), r.with_detection.toLocaleString(), pct(r.rate), `${pct(r.ci_low)} – ${pct(r.ci_high)}`, r.lift === null ? '—' : r.lift.toFixed(2)]),
      }}
    >
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 10, right: 8, bottom: 0, left: -10 }} barCategoryGap="22%">
            <CartesianGrid stroke="rgba(35,45,48,0.10)" vertical={false} />
            <XAxis dataKey="class" tickFormatter={shortClass} tick={{ fontSize: 10.5, fill: '#5c6f73' }} axisLine={{ stroke: 'rgba(35,45,48,0.28)' }} tickLine={false} interval={0} />
            <YAxis tick={{ fontSize: 10, fill: '#7b8a8c' }} axisLine={false} tickLine={false} unit="%" />
            <Tooltip
              cursor={{ fill: 'rgba(35,45,48,0.06)' }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                const r = payload[0].payload as any
                return (
                  <div className="rounded-lg bg-graphite-900 px-2.5 py-1.5 text-[11.5px] text-ivory shadow-lg">
                    <div className="font-medium">{r.class}</div>
                    <div className="font-mono">{pct(r.rate)} of {r.cell_days.toLocaleString()} cell-days</div>
                    <div className="text-ivory/65">95 % CI {pct(r.ci_low)} – {pct(r.ci_high)}</div>
                  </div>
                )
              }}
            />
            <Bar dataKey="pct" radius={[4, 4, 0, 0]} isAnimationActive={!reduce} shape={(props: any) => <rect x={props.x} y={props.y} width={props.width} height={props.height} rx={3} fill={props.payload.fill} />}>
              <ErrorBar dataKey="err" width={6} strokeWidth={1.3} stroke="#232d30" direction="y" />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartFrame>
  )
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl bg-ivory/70 p-3">
      <div className="eyebrow text-[9.5px] text-graphite-500">{label}</div>
      <div className="mt-1 font-mono text-lg tabular text-graphite-950">{value}</div>
      {hint && <div className="mt-0.5 text-[11px] text-graphite-600">{hint}</div>}
    </div>
  )
}

export function ValidationReport({ region = 'karnataka' }: { region?: string }) {
  const [season, setSeason] = useState<string | null>(null)
  const { data: r, error, refresh, loading } = useApi<Report>(`/api/validation?region=${region}${season ? `&season=${season}` : ''}`)
  if (loading && !r) return <Skeleton className="h-64" />
  if (error && !r)
    return error.status === 404 ? (
      <p className="rounded-xl bg-ivory/60 p-4 text-[14px]">No retrospective comparison has been run yet. Operators can run <code className="font-mono">python -m incendium validate</code>.</p>
    ) : (
      <ErrorState error={error} onRetry={refresh} />
    )
  if (!r) return null
  const m = r.meta
  const fwiAuc = r.auc.fwi
  const compAuc = r.auc.composite
  const compV01 = r.auc.composite_v01
  const sens = r.weight_sensitivity
  const cal = r.fwi_classes_calibrated
  const seasons: string[] = m.available_seasons ?? []
  const current = m.season ?? m.start.slice(0, 4)
  return (
    <div className="space-y-5">
      {seasons.length > 1 && (
        <div role="tablist" aria-label="Season" className="inline-flex rounded-xl bg-graphite-900/6 p-1">
          {seasons.map((s) => (
            <button
              key={s}
              role="tab"
              aria-selected={s === current}
              onClick={() => setSeason(s)}
              className={`rounded-lg px-3 py-1.5 text-[13px] font-medium ${s === current ? 'bg-ivory text-graphite-950 shadow-sm' : 'text-graphite-600 hover:text-graphite-900'}`}
            >
              Feb–May {s}
            </button>
          ))}
        </div>
      )}
      <p className="rounded-lg bg-[#3d5a6a]/12 p-3 text-[14px] text-[#24404d]">
        <b>A descriptive association study, not a test of predictive skill.</b> For every grid cell and local day from {m.start} to {m.end}, it asks whether higher indicator values coincided with VIIRS detecting a thermal anomaly in that cell.
        Detections include agricultural burning, and fire-weather indices do not model ignitions. Computed {fmtIst(m.generated_at)} from real archived data. Composite weights were not tuned on any season.
      </p>

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Cell-days analysed" value={m.cell_days.toLocaleString()} hint={`${m.cells} cells × ${Math.round(m.cell_days / m.cells)} days`} />
        <Stat label="Cell-days with a detection" value={m.cell_days_with_detection.toLocaleString()} hint={`base rate ${pct(m.base_rate)}`} />
        <Stat label="FWI AUC (pooled)" value={f3(fwiAuc.auc)} hint={`95 % CI ${f3(fwiAuc.ci_low)}–${f3(fwiAuc.ci_high)}; within month ${f3(fwiAuc.auc_within_month)}`} />
        <Stat label="Composite AUC (pooled)" value={f3(compAuc.auc)} hint={`95 % CI ${f3(compAuc.ci_low)}–${f3(compAuc.ci_high)}; within month ${f3(compAuc.auc_within_month)}${compV01 ? `; v0.1 (Drought Code) ${f3(compV01.auc)}` : ''}`} />
      </div>

      {cal && (
        <div className={`rounded-lg p-3 text-[13.5px] ${cal.in_sample ? 'bg-[#8a6a1f]/12 text-[#4d3b10]' : 'bg-[#2f6b4f]/12 text-[#1d4532]'}`}>
          <b>{cal.in_sample ? 'In-sample:' : 'Independent test:'}</b>{' '}
          {cal.in_sample
            ? `the calibrated FWI thresholds were derived from this season's FWI values (detections were not used), so the calibrated chart below shows the reference season, not a test.`
            : `the calibrated FWI thresholds were derived from the Feb–May ${cal.source.start.slice(0, 4)} season's FWI values and never saw this season's data.`}{' '}
          Thresholds {cal.thresholds.map((t: number) => t.toFixed(1)).join(' / ')} (P{cal.percentiles.join(' / P')}). Detection rate rises with every class (≥ 200 cell-days): <b>{cal.monotonic ? 'yes' : 'no'}</b>.
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {cal && (
          <div className="rounded-xl border border-graphite-900/10 bg-ivory/60 p-4">
            <RateChart
              rows={cal.classes}
              classes={FWI_CLASSES}
              title="Detection rate by calibrated FWI class"
              subtitle={`Karnataka percentile classes (${cal.source.start.slice(0, 4)} reference season). Whiskers: 95 % Wilson interval.`}
            />
          </div>
        )}
        <div className="rounded-xl border border-graphite-900/10 bg-ivory/60 p-4">
          <RateChart rows={r.fwi_classes} classes={FWI_CLASSES} title={cal ? 'Detection rate by EFFIS FWI class (reference)' : 'Detection rate by FWI class'} subtitle="Share of cell-days with ≥ 1 detection. Whiskers: 95 % Wilson interval." />
        </div>
        <div className="rounded-xl border border-graphite-900/10 bg-ivory/60 p-4">
          <RateChart rows={r.composite_classes} classes={COMPOSITE_CLASSES} title="Detection rate by composite class" subtitle={`Same events, experimental composite${m.composite_version ? ` v${m.composite_version}` : ''} classes.`} />
        </div>
      </div>

      <div className="overflow-x-auto rounded-xl border border-graphite-900/10 bg-ivory/60 p-4">
        <div className="text-sm font-semibold">Discrimination by indicator</div>
        <p className="mt-0.5 text-[12px] text-graphite-600">{r.definitions.auc} {r.definitions.auc_ci} {r.definitions.auc_within_month}</p>
        <table className="mt-3 w-full min-w-[560px] text-left text-[12.5px]">
          <thead className="text-graphite-600"><tr><th className="py-1 pr-3 font-medium">Indicator</th><th className="py-1 pr-3 font-medium">AUC pooled</th><th className="py-1 pr-3 font-medium">95 % CI (cell bootstrap)</th><th className="py-1 font-medium">AUC within month</th></tr></thead>
          <tbody className="font-mono tabular">
            {Object.entries(r.auc).map(([k, a]: [string, any]) => (
              <tr key={k} className="border-t border-graphite-900/8"><td className="py-1 pr-3 font-sans">{a.label}</td><td className="py-1 pr-3">{f3(a.auc)}</td><td className="py-1 pr-3">{f3(a.ci_low)} – {f3(a.ci_high)}</td><td className="py-1">{f3(a.auc_within_month)}</td></tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="overflow-x-auto rounded-xl border border-graphite-900/10 bg-ivory/60 p-4">
          <div className="text-sm font-semibold">Monthly context</div>
          <p className="mt-0.5 text-[12px] text-graphite-600">Fire weather and burning both follow the season, so pooled numbers mix both effects.</p>
          <table className="mt-2 w-full text-left text-[12.5px]">
            <thead className="text-graphite-600"><tr><th className="py-1 pr-2 font-medium">Month</th><th className="py-1 pr-2 font-medium">Rate</th><th className="py-1 pr-2 font-medium">Mean FWI</th><th className="py-1 font-medium">Mean composite</th></tr></thead>
            <tbody className="font-mono tabular">{r.monthly.map((x: any) => <tr key={x.month} className="border-t border-graphite-900/8"><td className="py-1 pr-2">{x.month}</td><td className="py-1 pr-2">{pct(x.rate)}</td><td className="py-1 pr-2">{x.mean_fwi.toFixed(1)}</td><td className="py-1">{x.mean_composite.toFixed(1)}</td></tr>)}</tbody>
          </table>
        </div>
        <div className="overflow-x-auto rounded-xl border border-graphite-900/10 bg-ivory/60 p-4">
          <div className="text-sm font-semibold">Hits, misses and false alarms</div>
          <p className="mt-0.5 text-[12px] text-graphite-600">Treating “High or above” as a flag, purely to describe the overlap.</p>
          <table className="mt-2 w-full text-left text-[12.5px]">
            <thead className="text-graphite-600"><tr><th className="py-1 pr-2 font-medium">Rule</th><th className="py-1 pr-2 font-medium">Hit rate</th><th className="py-1 pr-2 font-medium">False-alarm ratio</th><th className="py-1 font-medium">Flagged</th></tr></thead>
            <tbody className="tabular">
              {([...Object.entries(r.contingency), ...(cal ? [["fwi_cal", cal.contingency]] : [])] as [string, any][]).map(([k, c]: [string, any]) => (
                <tr key={k} className="border-t border-graphite-900/8"><td className="py-1 pr-2">{c.threshold}</td><td className="py-1 pr-2 font-mono">{pct(c.hit_rate, 1)}</td><td className="py-1 pr-2 font-mono">{pct(c.false_alarm_ratio, 1)}</td><td className="py-1 font-mono">{pct(c.flagged_share, 1)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="rounded-xl border border-graphite-900/10 bg-ivory/60 p-4 text-[13.5px]">
        <div className="text-sm font-semibold">Composite weight sensitivity</div>
        <p className="mt-1">
          Default weights give AUC <b className="font-mono">{f3(sens.default_auc)}</b>. Across {sens.random_weights.n} random weight vectors the AUC ranged from{' '}
          <span className="font-mono">{f3(sens.random_weights.min)}</span> to <span className="font-mono">{f3(sens.random_weights.max)}</span> (median {f3(sens.random_weights.median)}). The defaults rank at the {Math.round(sens.random_weights.default_percentile)}th percentile.
        </p>
        <p className="mt-1 text-[12px] text-graphite-600">{sens.caveat}</p>
      </div>

      <div className="text-[13.5px]">
        <div className="text-sm font-semibold">Limitations of this comparison</div>
        <ul className="mt-1 list-disc space-y-1 pl-5">{r.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>
        <p className="mt-2 text-[12px] text-graphite-600">
          Data: {r.data.weather.provider} ({r.data.weather.model}); {r.data.detections.provider} {r.data.detections.products?.join(', ')}; {r.data.vegetation.provider}.
        </p>
      </div>
    </div>
  )
}
