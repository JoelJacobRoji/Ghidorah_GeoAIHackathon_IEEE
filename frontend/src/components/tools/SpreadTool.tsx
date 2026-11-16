import { Flame, Loader2, Play, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { ApiError, apiPost } from '../../lib/api'
import { fmtCoord, num } from '../../lib/format'
import { SPREAD_RAMP } from '../../lib/overlays'
import type { SpreadResult } from '../../lib/types'
import { ErrorState } from '../ui'

function hourOptions(): { value: string; label: string }[] {
  // Local (browser) wall-clock hours; the backend interprets them in the region's time zone (IST).
  const out: { value: string; label: string }[] = [{ value: '', label: 'Now (current hour)' }]
  const now = new Date()
  now.setMinutes(0, 0, 0)
  for (let h = -24; h <= 72; h += 3) {
    if (h === 0) continue
    const d = new Date(now.getTime() + h * 3600_000)
    const pad = (n: number) => String(n).padStart(2, '0')
    const value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:00`
    const label = `${d.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short' })} ${pad(d.getHours())}:00${h > 0 ? ' (forecast)' : ''}`
    out.push({ value, label })
  }
  return out
}

export function SpreadTool({ point, result, onResult, onClose }: {
  point: [number, number] | null
  result: SpreadResult | null
  onResult: (r: SpreadResult | null) => void
  onClose: () => void
}) {
  const [start, setStart] = useState('')
  const [hours, setHours] = useState(6)
  const [curing, setCuring] = useState(85)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const options = useMemo(hourOptions, [])

  async function run() {
    if (!point) return
    setBusy(true)
    setError(null)
    try {
      const r = await apiPost<SpreadResult>('/api/simulate/spread', { lon: point[0], lat: point[1], hours, curing_pct: curing, ...(start ? { start_local: start } : {}) })
      onResult(r)
    } catch (e) {
      setError(e as ApiError)
      onResult(null)
    } finally {
      setBusy(false)
    }
  }

  const m = result?.meta
  const maxArea = Math.max(1, ...(m?.hourly.map((h) => h.area_ha) ?? [1]))
  return (
    <div className="panel scroll-thin flex max-h-full min-h-0 flex-col overflow-y-auto p-4 text-graphite-900">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="eyebrow text-graphite-500">Module 4 · illustrative scenario</div>
          <h2 className="font-display mt-0.5 flex items-center gap-1.5 text-lg font-semibold"><Flame className="size-4 text-[#b8561c]" aria-hidden /> Fire spread simulation</h2>
        </div>
        <button type="button" onClick={onClose} className="rounded-lg p-1 hover:bg-graphite-900/8" aria-label="Close spread simulation"><X className="size-4" /></button>
      </div>
      <p className="mt-2 text-[12.5px] leading-snug text-graphite-700">
        {point ? <>Ignition at <b className="font-mono">{fmtCoord(point[1], point[0], 4)}</b>. Click the map to move it.</> : 'Click the map inside Karnataka to set an ignition point, or use “Simulate spread” on a detection.'}
      </p>

      <div className="mt-3 grid gap-3 text-[12.5px]">
        <label className="grid gap-1">
          <span className="font-medium">Start time (IST)</span>
          <select value={start} onChange={(e) => setStart(e.target.value)} className="rounded-lg border border-graphite-900/15 bg-ivory/70 px-2 py-1.5">
            {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
        <label className="grid gap-1">
          <span className="flex justify-between font-medium"><span>Duration</span><span className="font-mono">{hours} h</span></span>
          <input type="range" min={1} max={12} value={hours} onChange={(e) => setHours(Number(e.target.value))} className="accent-graphite-800" />
        </label>
        <label className="grid gap-1">
          <span className="flex justify-between font-medium"><span>Grass curing (assumption)</span><span className="font-mono">{curing} %</span></span>
          <input type="range" min={40} max={100} step={5} value={curing} onChange={(e) => setCuring(Number(e.target.value))} className="accent-graphite-800" />
        </label>
        <button type="button" disabled={!point || busy} onClick={run} className="flex items-center justify-center gap-2 rounded-xl bg-graphite-800 px-3 py-2 text-sm font-medium text-ivory hover:bg-graphite-700 disabled:opacity-45">
          {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Play className="size-4" aria-hidden />}
          {busy ? 'Computing… (first run in an area can take ~30 s)' : 'Run scenario'}
        </button>
      </div>

      {error && <div className="mt-3"><ErrorState error={error} onRetry={run} title="Scenario unavailable" /></div>}

      {m && !busy && (
        <div className="mt-4 space-y-4 border-t border-graphite-900/10 pt-3" aria-live="polite">
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Area after" value={`${num(m.final_area_ha, 0)} ha`} hint={`${m.hours} h`} />
            <Stat label="Ignition fuel" value={m.ignition.fuel} hint="Sentinel-2 proxy" capitalize />
            <Stat label="Settlements" value={String(m.settlements_reached.length)} hint="reached / ≤ 300 m" />
          </div>

          <div>
            <div className="text-[12px] font-semibold">Area by hour</div>
            <ul className="mt-1.5 space-y-1" aria-label="Burned area by hour">
              {m.hourly.map((h, i) => (
                <li key={h.hour} className="flex items-center gap-2 text-[11.5px]">
                  <span className="w-11 font-mono text-graphite-600">{h.time_local.slice(11, 16)}</span>
                  <span className="relative h-3 flex-1 overflow-hidden rounded-sm bg-graphite-900/8">
                    <span className="absolute inset-y-0 left-0 rounded-sm" style={{ width: `${(h.area_ha / maxArea) * 100}%`, background: SPREAD_RAMP[Math.round((i / Math.max(1, m.hourly.length - 1)) * (SPREAD_RAMP.length - 1))] }} />
                  </span>
                  <span className="w-16 text-right font-mono">{num(h.area_ha, h.area_ha < 10 ? 1 : 0)} ha</span>
                </li>
              ))}
            </ul>
            <p className="mt-1 text-[11px] text-graphite-600">Map colours: darkest = reached first. Each ring is the cumulative area by that hour.</p>
          </div>

          <details className="text-[12px]">
            <summary className="cursor-pointer font-semibold">Hourly fire weather used</summary>
            <div className="mt-1.5 overflow-x-auto">
              <table className="w-full min-w-[320px] text-left">
                <thead className="text-graphite-600"><tr><th className="pr-2 font-medium">Hour</th><th className="pr-2 font-medium">Wind</th><th className="pr-2 font-medium">RH</th><th className="pr-2 font-medium">FFMC</th><th className="font-medium">Head ROS grass</th></tr></thead>
                <tbody className="font-mono tabular">
                  {m.weather_by_hour.map((w) => (
                    <tr key={w.time_local} className="border-t border-graphite-900/8">
                      <td className="pr-2">{w.time_local.slice(11, 16)}</td>
                      <td className="pr-2">{num(w.wind_kmh, 0)} km/h from {w.wind_from_deg}°</td>
                      <td className="pr-2">{w.rh_pct}%</td>
                      <td className="pr-2">{num(w.ffmc, 1)}</td>
                      <td>{num(w.head_ros_m_min.grass, 1)} m/min</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>

          {m.settlements_reached.length > 0 && (
            <div>
              <div className="text-[12px] font-semibold">Settlements reached (OpenStreetMap)</div>
              <ul className="mt-1 space-y-0.5 text-[12px]">
                {m.settlements_reached.slice(0, 8).map((s) => (
                  <li key={s.id} className="flex justify-between gap-2"><span>{s.name ?? 'Unnamed'} <span className="text-graphite-500">({s.subtype})</span></span><span className="font-mono">~{num(s.arrival_hours ?? null, 1)} h</span></li>
                ))}
              </ul>
            </div>
          )}

          {m.fire_stations.length > 0 && (
            <div>
              <div className="text-[12px] font-semibold">Nearest fire stations (straight-line)</div>
              <ul className="mt-1 space-y-0.5 text-[12px]">
                {m.fire_stations.map((s) => (
                  <li key={s.id} className="flex justify-between gap-2">
                    <a href={s.osm_url} target="_blank" rel="noopener noreferrer" className="underline decoration-graphite-900/25 hover:decoration-graphite-900">{s.name ?? 'Fire station'}</a>
                    <span className="font-mono">{num(s.distance_km ?? null, 0)} km{s.phone ? ` · ${s.phone}` : ''}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <details className="text-[12px]" open>
            <summary className="cursor-pointer font-semibold">Assumptions & limits</summary>
            <ul className="mt-1 list-disc space-y-1 pl-4 text-graphite-700">{m.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
            <p className="mt-1.5 text-[11px] text-graphite-600">
              Fuel: {m.fuel.source}, {m.fuel.period[0]} → {m.fuel.period[1]}. Terrain: {m.terrain.source} ({m.terrain.elev_min}–{m.terrain.elev_max} m). Grid {m.grid.resolution_m} m. Start FFMC {m.inputs.ffmc_prev_day}, BUI {m.inputs.bui ?? '—'}.
            </p>
          </details>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, hint, capitalize = false }: { label: string; value: string; hint?: string; capitalize?: boolean }) {
  return (
    <div className="rounded-lg bg-ivory/70 p-2">
      <div className="eyebrow text-[9px] text-graphite-500">{label}</div>
      <div className={`mt-0.5 font-mono text-[15px] tabular ${capitalize ? 'capitalize' : ''}`}>{value}</div>
      {hint && <div className="text-[10.5px] text-graphite-500">{hint}</div>}
    </div>
  )
}
