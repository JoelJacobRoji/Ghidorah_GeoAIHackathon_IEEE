import { Loader2, Play, Route, X } from 'lucide-react'
import { useState } from 'react'
import { ApiError, apiPost } from '../../lib/api'
import { compass, fmtCoord, num } from '../../lib/format'
import { CORRIDOR_COLOR, HUMAN_ZONE_COLOR } from '../../lib/overlays'
import type { FirebreakResult } from '../../lib/types'
import { ErrorState } from '../ui'

const RADII = [3, 6, 9, 12]
const COMPONENTS: [keyof FirebreakResult['features'][number]['properties'], string][] = [
  ['hazard', 'Forest fire hazard'],
  ['wind_into_forest', 'Wind into forest'],
  ['human_pressure', 'Human pressure'],
  ['terrain', 'Gentle terrain'],
]

export function FirebreakTool({ point, radius, onRadius, result, onResult, showHazard, onShowHazard, onFocus, onClose }: {
  point: [number, number] | null
  radius: number
  onRadius: (r: number) => void
  result: FirebreakResult | null
  onResult: (r: FirebreakResult | null) => void
  showHazard: boolean
  onShowHazard: (v: boolean) => void
  onFocus: (coords: [number, number]) => void
  onClose: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  async function run() {
    if (!point) return
    setBusy(true)
    setError(null)
    try {
      onResult(await apiPost<FirebreakResult>('/api/firebreaks', { lon: point[0], lat: point[1], radius_km: radius }))
    } catch (e) {
      setError(e as ApiError)
      onResult(null)
    } finally {
      setBusy(false)
    }
  }

  const m = result?.meta
  return (
    <div className="panel scroll-thin flex max-h-full min-h-0 flex-col overflow-y-auto p-4 text-graphite-900">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="eyebrow text-graphite-500">Module 3 · decision support</div>
          <h2 className="font-display mt-0.5 flex items-center gap-1.5 text-lg font-semibold"><Route className="size-4" style={{ color: CORRIDOR_COLOR }} aria-hidden /> Firebreak recommendation</h2>
        </div>
        <button type="button" onClick={onClose} className="rounded-lg p-1 hover:bg-graphite-900/8" aria-label="Close firebreak planning"><X className="size-4" /></button>
      </div>
      <p className="mt-2 text-[12.5px] leading-snug text-graphite-700">
        Most fires here are started by people. This proposes lines along the forest edge that separate the zone within 500 m of roads and villages from the deeper forest, only where that forest is large and satellites have seen it burn in recent fire seasons.{' '}
        {point ? <>Area centre <b className="font-mono">{fmtCoord(point[1], point[0], 4)}</b>.</> : 'Click the map inside Karnataka to choose an area.'}
      </p>

      <div className="mt-3 grid gap-3 text-[12.5px]">
        <div>
          <div className="font-medium">Radius</div>
          <div className="mt-1 grid grid-cols-4 gap-1" role="radiogroup" aria-label="Analysis radius">
            {RADII.map((r) => (
              <button key={r} type="button" role="radio" aria-checked={radius === r} onClick={() => onRadius(r)} className={`rounded-lg px-1.5 py-1 text-[12px] font-medium ${radius === r ? 'bg-graphite-800 text-ivory' : 'bg-graphite-900/6 hover:bg-graphite-900/10'}`}>{r} km</button>
            ))}
          </div>
        </div>
        <button type="button" disabled={!point || busy} onClick={run} className="flex items-center justify-center gap-2 rounded-xl bg-graphite-800 px-3 py-2 text-sm font-medium text-ivory hover:bg-graphite-700 disabled:opacity-45">
          {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Play className="size-4" aria-hidden />}
          {busy ? 'Analysing… (first run in an area can take ~1 min)' : 'Find firebreak lines'}
        </button>
      </div>

      {error && <div className="mt-3"><ErrorState error={error} onRetry={run} title="Analysis unavailable" /></div>}

      {m && result && !busy && (
        <div className="mt-4 space-y-4 border-t border-graphite-900/10 pt-3" aria-live="polite">
          <div className="grid grid-cols-3 gap-2 text-[12px]">
            <div className="rounded-lg bg-ivory/70 p-2"><div className="eyebrow text-[9px] text-graphite-500">Fire-season wind</div><div className="mt-0.5 font-mono">{compass(m.design.wind_from_deg)} {Math.round(m.design.wind_from_deg)}°</div><div className="text-[10.5px] text-graphite-500">afternoons, p90 {num(m.design.ws_p90_kmh, 0)} km/h</div></div>
            <div className="rounded-lg bg-ivory/70 p-2"><div className="eyebrow text-[9px] text-graphite-500">Forest intensity</div><div className="mt-0.5 font-mono">{num(m.design.intensity_kw_m.forest, 0)} kW/m</div><div className="text-[10.5px] text-graphite-500">design day, FFMC {num(m.design.ffmc_p90, 0)}</div></div>
            <div className="rounded-lg bg-ivory/70 p-2"><div className="eyebrow text-[9px] text-graphite-500">Proposed</div><div className="mt-0.5 font-mono">{num(m.line_km_proposed, 1)} km</div><div className="text-[10.5px] text-graphite-500">of {num(m.edge_km_examined, 0)} km zone edge</div></div>
          </div>
          <div className="flex items-center gap-3 text-[11px] text-graphite-600">
            <span className="flex items-center gap-1.5"><span className="inline-block h-[3px] w-5 rounded" style={{ background: CORRIDOR_COLOR }} aria-hidden /> proposed firebreak</span>
            <span className="flex items-center gap-1.5"><span className="inline-block size-3 rounded-sm border border-dashed" style={{ background: `${HUMAN_ZONE_COLOR}24`, borderColor: HUMAN_ZONE_COLOR }} aria-hidden /> within 500 m of roads/villages</span>
          </div>
          <label className="flex items-center gap-2 text-[12px]">
            <input type="checkbox" checked={showHazard} onChange={(e) => onShowHazard(e.target.checked)} className="accent-graphite-800" />
            Show fire-season hazard surface (green low → dark red high)
          </label>

          {result.features.length === 0 && m.empty_reason && (
            <div className="rounded-lg bg-ivory/70 p-2.5 text-[12px] text-graphite-700"><b>No firebreak needed here.</b> {m.empty_reason}</div>
          )}
          {result.features.length > 0 && (
          <div>
            <div className="text-[12px] font-semibold">Proposed firebreak lines</div>
            <ol className="mt-1.5 space-y-2">
              {result.features.map((f) => {
                const p = f.properties
                const near = p.settlements_near.map((s) => s.name).filter(Boolean)
                return (
                  <li key={p.rank}>
                    <button type="button" aria-label={`Line ${p.rank}`} onClick={() => onFocus(f.geometry.coordinates[Math.floor(f.geometry.coordinates.length / 2)] as [number, number])} className="w-full rounded-lg bg-ivory/60 p-2 text-left hover:bg-ivory">
                      <div className="flex items-baseline gap-2">
                        <span className="grid size-5 shrink-0 place-items-center rounded-full text-[11px] font-bold text-white" style={{ background: CORRIDOR_COLOR }}>{p.rank}</span>
                        <span className="text-[12.5px] font-medium">Forest edge · {p.past_fires} past forest-fire detections</span>
                        <span className="ml-auto font-mono text-[11px] text-graphite-600">{(p.length_m / 1000).toFixed(1)} km</span>
                      </div>
                      <div className="mt-1 grid grid-cols-4 gap-1">
                        {COMPONENTS.map(([k, label]) => (
                          <div key={k} title={`${label}: ${Number(p[k]).toFixed(2)}`}>
                            <div className="h-1.5 overflow-hidden rounded-full bg-graphite-900/10"><div className="h-full rounded-full bg-graphite-700" style={{ width: `${Math.min(1, Number(p[k])) * 100}%` }} /></div>
                            <div className="mt-0.5 truncate text-[9.5px] text-graphite-600">{label}</div>
                          </div>
                        ))}
                      </div>
                      {(near.length > 0 || p.roads_near.length > 0) && (
                        <div className="mt-1 text-[11px] text-graphite-700">Separates forest from {[...near, ...p.roads_near].join(', ')}</div>
                      )}
                      <div className="mt-0.5 text-[10.5px] text-graphite-500">
                        protects {num(p.forest_protected_km2, 1)} km² forest · {num(p.forest_intensity_kw_m, 0)} kW/m ({p.intensity_class}) · slope {p.mean_slope_deg}° ·{' '}
                        {p.anchor ? `along ${p.anchor.name ?? `unnamed ${p.anchor.class}`} (${p.anchor.class})` : 'new cut'}
                      </div>
                    </button>
                  </li>
                )
              })}
            </ol>
          </div>
          )}

          <details className="text-[12px]">
            <summary className="cursor-pointer font-semibold">How lines are chosen and scored</summary>
            <pre className="mt-1 whitespace-pre-wrap font-sans text-[11.5px] leading-snug text-graphite-700">{m.method}</pre>
          </details>
          <details className="text-[12px]" open>
            <summary className="cursor-pointer font-semibold">Assumptions & limits</summary>
            <ul className="mt-1 list-disc space-y-1 pl-4 text-graphite-700">{m.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
            <p className="mt-1.5 text-[11px] text-graphite-600">Fuel: Sentinel-2 {m.fuel.period[0]} → {m.fuel.period[1]} (fire season). Fire history: {m.fire_history.source}, {m.fire_history.years.join(' and ')} ({m.fire_history.on_forest} detections on forest in this window). Roads, waterways and settlements © OpenStreetMap contributors (ODbL), retrieved {m.osm_retrieved_at.slice(0, 10)}.</p>
          </details>
        </div>
      )}
    </div>
  )
}
