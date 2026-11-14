import { motion } from 'motion/react'
import { NavBar } from '../components/NavBar'
import { SourceStatus } from '../components/SourceStatus'
import { ErrorState, Skeleton } from '../components/ui'
import { MlReport } from '../components/MlReport'
import { ValidationReport } from '../components/ValidationReport'
import { useApi } from '../lib/api'
import { describeScheme, type RegionalFwiClasses } from '../lib/fwiClasses'
import { fadeUp, stagger } from '../lib/motion'
import { COMPOSITE_CLASSES, FACTOR_COLORS, FWI_CLASSES } from '../lib/risk'
import type { Methodology as M } from '../lib/types'

const TOC = [
  ['overview', 'Overview'],
  ['modules', 'The five modules'],
  ['sources', 'Data sources'],
  ['detections', 'Satellite detections'],
  ['fwi', 'Fire Weather Index'],
  ['composite', 'Experimental composite'],
  ['grid', 'Spatial grid'],
  ['quality', 'Data quality & freshness'],
  ['limitations', 'Limitations'],
  ['validation', 'Validation'],
  ['retrospective', 'Retrospective comparison'],
  ['ml', 'ML model test'],
] as const

export default function Methodology() {
  const { data, error, refresh } = useApi<M>('/api/methodology')
  return (
    <div className="min-h-dvh bg-[#d9d4c7] text-graphite-900">
      <NavBar right={<SourceStatus dark />} />
      <div className="mx-auto grid max-w-[1200px] gap-10 px-4 py-8 md:px-6 lg:grid-cols-[200px_1fr]">
        <nav aria-label="Contents" className="hidden lg:block">
          <div className="sticky top-24">
            <div className="eyebrow text-graphite-500">Contents</div>
            <ul className="mt-3 space-y-1.5 text-[13px]">
              {TOC.map(([id, l]) => <li key={id}><a href={`#${id}`} className="text-graphite-700 hover:text-graphite-950">{l}</a></li>)}
            </ul>
          </div>
        </nav>
        <motion.article variants={stagger} initial="hidden" animate="show" className="min-w-0 max-w-[760px] text-[15px] leading-relaxed text-graphite-800">
          <motion.div variants={fadeUp} className="eyebrow text-graphite-500">Methodology & data transparency</motion.div>
          <motion.h1 variants={fadeUp} className="font-display mt-2 text-4xl font-bold leading-tight text-graphite-950">How Incendium works, and what it cannot tell you</motion.h1>

          <Section id="overview" title="Overview">
            <p>
              Incendium connects five modules: wildfire prediction, geospatial monitoring, firebreak recommendation, fire spread simulation and emergency alerts. Underneath, it answers two different questions and keeps them apart. <b>Where have satellites detected heat?</b> comes from NASA FIRMS thermal-anomaly detections. <b>How favourable is the weather for fire?</b> comes from the Canadian Fire Weather Index (FWI), computed from model weather. Vegetation from Sentinel-2 adds context through an experimental, fully explained composite score.
            </p>
            <p>None of these predicts where a fire will start. A high FWI means conditions would let a fire ignite easily and spread. It says nothing about whether an ignition source exists.</p>
          </Section>

          {error && !data ? (
            <ErrorState error={error} onRetry={refresh} />
          ) : !data ? (
            <Skeleton className="mt-6 h-96" />
          ) : (
            <>
              <Section id="modules" title="The five modules">
                <ol className="space-y-3">
                  {Object.entries((data.modules ?? {}) as Record<string, { title: string; number: number; summary: string; method?: string }>)
                    .sort((a, b) => a[1].number - b[1].number)
                    .map(([id, mod]) => (
                      <li key={id} className="rounded-xl bg-ivory/60 p-4">
                        <div className="flex items-baseline gap-2">
                          <span className="font-mono text-sm text-graphite-500">{String(mod.number).padStart(2, '0')}</span>
                          <span className="font-semibold text-graphite-950">{mod.title}</span>
                        </div>
                        <p className="mt-1 text-[14px]">{mod.summary}</p>
                        {mod.method && (
                          <details className="mt-2 text-[13px]">
                            <summary className="cursor-pointer font-medium">Method and limitations</summary>
                            <pre className="mt-2 whitespace-pre-wrap font-sans text-[12.5px] leading-relaxed text-graphite-800">{mod.method}</pre>
                          </details>
                        )}
                      </li>
                    ))}
                </ol>
              </Section>

              <Section id="sources" title="Data sources & attribution">
                <div className="grid gap-3">
                  {data.sources.map((s: Record<string, string>) => (
                    <div key={s.id} className="rounded-xl border border-graphite-900/10 bg-ivory/70 p-4">
                      <div className="font-semibold text-graphite-950"><a href={s.url} target="_blank" rel="noopener" className="underline decoration-graphite-900/25 underline-offset-2 hover:decoration-graphite-900">{s.name}</a></div>
                      {s.what && <p className="mt-1 text-[13.5px]">{s.what}</p>}
                      {s.latency && <p className="mt-1 text-[13px] text-graphite-600">{s.latency}</p>}
                      {s.resolution && <p className="mt-1 text-[13px] text-graphite-600">{s.resolution}</p>}
                      {s.notes && <p className="mt-1 text-[13px] text-graphite-600">{s.notes}</p>}
                      <p className="mt-2 text-[12px] text-graphite-500">Attribution: {s.attribution}{s.licence ? ` · ${s.licence}` : ''}</p>
                    </div>
                  ))}
                </div>
              </Section>

              <Section id="detections" title="Satellite detections">
                <p>
                  VIIRS fire detections mark 375 m pixels whose mid-infrared brightness indicates a heat source. Each record keeps its latitude and longitude (pixel centre), UTC acquisition time, satellite, confidence (low / nominal / high), fire radiative power (MW) and brightness temperatures. Incendium requests the India monitoring window every 30 minutes, deduplicates by satellite, overpass minute and position, and keeps 30 days.
                </p>
                <p>Clusters on the map are a display aid at small scales. They are not fire incidents. Marker size scales with √FRP and is unrelated to the risk classes.</p>
              </Section>

              <Section id="fwi" title="Fire Weather Index (FWI)">
                <FwiDiagram />
                <p className="mt-4">{data.fwi.why}</p>
                <p><b>Inputs.</b> {data.fwi.inputs}</p>
                <dl className="mt-3 grid gap-2 sm:grid-cols-2">
                  {Object.entries(data.fwi.components as Record<string, string>).map(([k, v]) => (
                    <div key={k} className="rounded-lg bg-ivory/60 p-3">
                      <dt className="font-mono text-sm font-semibold">{k}</dt>
                      <dd className="mt-0.5 text-[13.5px]">{v}</dd>
                    </div>
                  ))}
                </dl>
                <p className="mt-3"><b>Start-up.</b> {data.fwi.startup} <b>Day length.</b> {data.fwi.day_length}</p>
                <p className="mt-3"><b>Implementation.</b> Incendium implements the equations of Van Wagner (1987). Automated tests cross-check them against the open-source <code className="font-mono text-[13px]">xclim</code> implementation at four latitudes. One documented difference: Eq. 12 uses the original published constant, where xclim uses the cffdrs variant.</p>
                {Object.entries((data.fwi.regional_classes ?? {}) as Record<string, RegionalFwiClasses>)
                  .filter(([, r]) => r.scheme === 'calibrated')
                  .map(([id, r]) => {
                    const sch = describeScheme(r, id.charAt(0).toUpperCase() + id.slice(1))
                    const cal = r.calibration!
                    return (
                      <div key={id}>
                        <ClassTable
                          title={`${id.charAt(0).toUpperCase() + id.slice(1)} FWI classes (calibrated on the Feb–May ${cal.source.start.slice(0, 4)} season)`}
                          rows={sch.classes.map((c, i) => ({ name: c.name, color: c.color, range: `${c.min} – ${sch.classes[i + 1]?.min ?? '∞'}` }))}
                          note={`${data.fwi.calibration_method} Reference: ${cal.source.cell_days.toLocaleString()} cell-days across ${cal.source.cells} cells, ${cal.source.weather}.`}
                        />
                        <p className="mt-2 text-[13px] text-graphite-600">The independent test of these classes on another season is shown under <a className="underline" href="#retrospective">Retrospective comparison</a>.</p>
                      </div>
                    )
                  })}
                <ClassTable title="EFFIS FWI classes (Europe; reference and fallback)" rows={FWI_CLASSES.map((c, i) => ({ name: c.name, color: c.color, range: `${c.min} – ${FWI_CLASSES[i + 1]?.min ?? '∞'}` }))} note={data.fwi.classes_note} />
              </Section>

              <Section id="composite" title="Experimental composite">
                <p className="font-mono text-[14px]">{data.composite.formula}</p>
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full text-left text-[13px]">
                    <thead className="text-graphite-600"><tr><th className="py-1.5 pr-3 font-medium">Factor</th><th className="py-1.5 pr-3 font-medium">Input</th><th className="py-1.5 pr-3 font-medium">Score 0 at</th><th className="py-1.5 pr-3 font-medium">Score 100 at</th><th className="py-1.5 font-medium">Weight</th></tr></thead>
                    <tbody>
                      {data.composite.factors.map((f: Record<string, string | number>) => (
                        <tr key={String(f.key)} className="border-t border-graphite-900/10 align-top">
                          <td className="py-1.5 pr-3"><span className="mr-1.5 inline-block size-2.5 rounded-[3px]" style={{ background: FACTOR_COLORS[String(f.key)] }} />{f.label}</td>
                          <td className="py-1.5 pr-3 text-graphite-700">{f.input}</td>
                          <td className="py-1.5 pr-3 font-mono">{f.score_0_at} {f.unit}</td>
                          <td className="py-1.5 pr-3 font-mono">{f.score_100_at} {f.unit}</td>
                          <td className="py-1.5 font-mono">{data.composite.weights[String(f.key)]}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <ul className="mt-3 list-disc space-y-1 pl-5 text-[14px]">{data.composite.rules.map((r: string) => <li key={r}>{r}</li>)}</ul>
                <p className="mt-3 rounded-lg bg-burnt/10 p-3 text-[14px] text-graphite-900">{data.composite.status}</p>
                {(data.composite.changes ?? []).map((c: { version: string; change: string; reason: string }) => (
                  <p key={c.version} className="mt-3 text-[14px]"><b>Change in v{c.version}.</b> {c.change} <span className="text-graphite-700">{c.reason}</span></p>
                ))}
                <ClassTable title="Prototype composite classes" rows={COMPOSITE_CLASSES.map((c, i) => ({ name: c.name, color: c.color, range: `${c.min} – ${COMPOSITE_CLASSES[i + 1]?.min ?? 100}` }))} />
              </Section>

              <Section id="grid" title="Spatial grid">
                <p>{data.grid.rationale} {data.grid.cell_inclusion} Weather is sampled at each cell centre. Vegetation is averaged over the whole cell.</p>
              </Section>

              <Section id="quality" title="Data quality & freshness">
                <ul className="list-disc space-y-1 pl-5">
                  <li>Every record stores its provider, observation or interval time, retrieval time and units.</li>
                  <li>If a provider fails, Incendium shows the last valid data with its age and marks it stale. It never substitutes invented values.</li>
                  <li>Cells with missing inputs show “insufficient data” instead of a number.</li>
                  <li>Model weather is labelled as model analysis (past days) or forecast (future days). Forecast days are drawn dashed.</li>
                  <li>Upstream requests are budgeted below the free-tier limits of each provider.</li>
                </ul>
              </Section>

              <Section id="limitations" title="Known limitations">
                <ul className="list-disc space-y-1.5 pl-5">{data.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>
              </Section>

              <Section id="validation" title="Validation">
                <p><b>Software testing.</b> {data.validation.software}</p>
                <p><b>Scientific validation.</b> {data.validation.scientific}</p>
              </Section>
              <Section id="retrospective" title="Retrospective comparison: Karnataka">
                <ValidationReport region="karnataka" />
              </Section>
              <Section id="ml" title="Detection-likelihood model: out-of-sample test">
                <MlReport />
              </Section>
            </>
          )}
        </motion.article>
      </div>
    </div>
  )
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <motion.section variants={fadeUp} id={id} className="mt-10 scroll-mt-24 space-y-3">
      <h2 className="font-display text-2xl font-semibold text-graphite-950">{title}</h2>
      {children}
    </motion.section>
  )
}

function ClassTable({ title, rows, note }: { title: string; rows: { name: string; color: string; range: string }[]; note?: string }) {
  return (
    <div className="mt-4">
      <div className="text-sm font-semibold">{title}</div>
      <div className="mt-2 flex flex-wrap gap-2">
        {rows.map((r) => (
          <div key={r.name} className="flex items-center gap-2 rounded-lg bg-ivory/70 px-2.5 py-1.5 text-[12.5px]">
            <span className="inline-block h-3 w-5 rounded-[3px]" style={{ background: r.color }} aria-hidden />
            <span className="font-medium">{r.name}</span>
            <span className="font-mono text-graphite-600">{r.range}</span>
          </div>
        ))}
      </div>
      {note && <p className="mt-2 text-[13px] text-graphite-600">{note}</p>}
    </div>
  )
}

/** Structure of the FWI system as an inline SVG flow diagram. */
function FwiDiagram() {
  const box = (x: number, y: number, w: number, label: string, sub: string, dark = false) => (
    <g key={label}>
      <rect x={x} y={y} width={w} height={44} rx={9} fill={dark ? '#2b3639' : '#e8e5db'} stroke="rgba(35,45,48,0.25)" />
      <text x={x + w / 2} y={y + 19} textAnchor="middle" fontSize="13" fontWeight="600" fill={dark ? '#e8e5db' : '#232d30'} fontFamily="IBM Plex Mono, monospace">{label}</text>
      <text x={x + w / 2} y={y + 34} textAnchor="middle" fontSize="10" fill={dark ? 'rgba(232,229,219,0.7)' : '#5c6f73'}>{sub}</text>
    </g>
  )
  const line = (x1: number, y1: number, x2: number, y2: number) => <path d={`M${x1},${y1} C${x1},${(y1 + y2) / 2} ${x2},${(y1 + y2) / 2} ${x2},${y2}`} fill="none" stroke="rgba(35,45,48,0.4)" strokeWidth="1.4" />
  return (
    <figure className="rounded-xl border border-graphite-900/10 bg-ivory/50 p-3">
      <svg viewBox="0 0 640 250" className="h-auto w-full" role="img" aria-label="FWI system structure: noon weather feeds FFMC, DMC and DC; wind and FFMC give ISI; DMC and DC give BUI; ISI and BUI give FWI.">
        {box(10, 8, 300, 'Temp · RH · Wind · Rain', 'noon weather (model)', true)}
        {box(330, 8, 300, 'Temp · RH · Rain', 'noon weather (model)', true)}
        {line(160, 52, 100, 92)}
        {line(480, 52, 320, 92)}
        {line(480, 52, 540, 92)}
        {line(160, 52, 320, 92)}
        {box(30, 92, 140, 'FFMC', 'fine fuels · hours')}
        {box(250, 92, 140, 'DMC', 'duff · ~2 weeks')}
        {box(470, 92, 140, 'DC', 'deep · ~7 weeks')}
        {line(100, 136, 160, 170)}
        {line(320, 136, 470, 170)}
        {line(540, 136, 470, 170)}
        {box(90, 170, 140, 'ISI', 'spread (+ wind)')}
        {box(400, 170, 140, 'BUI', 'fuel available')}
        {line(160, 214, 320, 230)}
        {line(470, 214, 320, 230)}
        <g>
          <rect x={250} y={206} width={140} height={40} rx={9} fill="#993b2c" />
          <text x={320} y={231} textAnchor="middle" fontSize="14" fontWeight="700" fill="#f3f1ea" fontFamily="IBM Plex Mono, monospace">FWI</text>
        </g>
      </svg>
      <figcaption className="mt-1 text-[12px] text-graphite-600">Structure of the Canadian FWI system (after Van Wagner 1987). Wind enters through FFMC and ISI. DC uses temperature and rain only.</figcaption>
    </figure>
  )
}
