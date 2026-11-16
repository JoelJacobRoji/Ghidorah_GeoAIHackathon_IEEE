import type { Map as MLMap } from 'maplibre-gl'
import { AnimatePresence, motion } from 'motion/react'
import { Activity, Box, ChevronDown, Flame, Layers, MapPin, Route, Search, Square } from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { FlyTarget, LayerToggles, Metric } from '../components/MapView'
import { NavBar } from '../components/NavBar'
import { CellPanel, DetectionPanel, Legend } from '../components/panels'
import { FirebreakTool } from '../components/tools/FirebreakTool'
import { SpreadTool } from '../components/tools/SpreadTool'
import { SourceStatus } from '../components/SourceStatus'
import { ErrorState, Tag } from '../components/ui'
import { useApi } from '../lib/api'
import { useFwiClasses } from '../lib/fwiClasses'
import { fmtDate, fmtIst, timeAgo } from '../lib/format'
import { hasWebGL, MOTION, useReducedMotion } from '../lib/motion'
import { setFireStations, setFirebreakOverlay, setSpreadOverlay, setToolPoint } from '../lib/overlays'
import type { Basemap } from '../lib/mapStyle'
import type { DetectionProps, FirebreakResult, HotspotsResponse, RegionsResponse, RiskMapResponse, SpreadResult, StationRef, WeatherResponse } from '../lib/types'

const MapView = lazy(() => import('../components/MapView'))
const WindParticles = lazy(() => import('../components/WindParticles'))

const WINDOWS = [
  { h: 6, label: '6 h' },
  { h: 24, label: '24 h' },
  { h: 48, label: '48 h' },
  { h: 168, label: '7 days' },
]

type SelectedDet = DetectionProps & { lon: number; lat: number }
type Tool = 'monitor' | 'spread' | 'firebreak'

const TOOLS: { id: Tool; label: string; icon: typeof Flame }[] = [
  { id: 'monitor', label: 'Monitor', icon: Activity },
  { id: 'spread', label: 'Spread', icon: Flame },
  { id: 'firebreak', label: 'Firebreaks', icon: Route },
]

export default function Dashboard() {
  const [params, setParams] = useSearchParams()
  const regionId = params.get('region') ?? 'karnataka'
  const [metric, setMetric] = useState<Metric>('fwi')
  const [layers, setLayers] = useState<LayerToggles>({ detections: true, risk: true, wind: false, particles: false, outline: true, extrude: false })
  const [stationsOn, setStationsOn] = useState(false)
  const [tool, setTool] = useState<Tool>((params.get('tool') as Tool) || 'monitor')
  const [toolPoint, setToolPointState] = useState<[number, number] | null>(null)
  const [spreadRes, setSpreadRes] = useState<SpreadResult | null>(null)
  const [fbRes, setFbRes] = useState<FirebreakResult | null>(null)
  const [fbRadius, setFbRadius] = useState(6)
  const [showHazard, setShowHazard] = useState(true)
  const [mode3d, setMode3d] = useState(false)
  const [hours, setHours] = useState(24)
  const [date, setDate] = useState<string | null>(params.get('date'))
  const [cell, setCell] = useState<string | null>(params.get('cell'))
  const [det, setDet] = useState<SelectedDet | null>(null)
  const [fly, setFly] = useState<FlyTarget | null>(null)
  const [layersOpen, setLayersOpen] = useState(typeof window !== 'undefined' ? window.innerWidth >= 900 : true)
  const mapRef = useRef<MLMap | null>(null)
  const [mapInst, setMapInst] = useState<MLMap | null>(null)
  const reduceMotion = useReducedMotion()

  const regions = useApi<RegionsResponse>('/api/regions')
  const fwiScheme = useFwiClasses(regionId)
  const hotspots = useApi<HotspotsResponse>(`/api/hotspots?hours=${hours}`, { refreshMs: 10 * 60_000 })
  const region = regions.data?.regions.find((r) => r.id === regionId)
  const riskPath = region?.enabled ? `/api/risk-map?region=${regionId}${date ? `&date=${date}` : ''}` : null
  const risk = useApi<RiskMapResponse>(riskPath)
  const weather = useApi<WeatherResponse>(region?.enabled && (layers.wind || layers.particles) ? `/api/weather?region=${regionId}` : null)
  const stationsBbox = useMemo(() => {
    const g = region?.outline as GeoJSON.Polygon | GeoJSON.MultiPolygon | undefined
    if (!g) return null
    const pts = (g.type === 'Polygon' ? g.coordinates.flat() : g.coordinates.flat(2)) as number[][]
    const xs = pts.map((p) => p[0])
    const ys = pts.map((p) => p[1])
    return [Math.min(...xs) - 0.3, Math.min(...ys) - 0.3, Math.max(...xs) + 0.3, Math.max(...ys) + 0.3].map((v) => v.toFixed(2)).join(',')
  }, [region])
  const stations = useApi<{ features: { properties: StationRef }[] }>(stationsOn && stationsBbox ? `/api/places/fire-stations?bbox=${stationsBbox}` : null)

  // ---------------------------------------------------------------- tool overlays
  useEffect(() => {
    if (!mapInst) return
    setToolPoint(mapInst, tool === 'monitor' ? null : toolPoint, tool === 'firebreak' ? fbRadius : undefined)
  }, [mapInst, tool, toolPoint, fbRadius])
  useEffect(() => {
    if (mapInst) setSpreadOverlay(mapInst, tool === 'spread' ? spreadRes : null)
  }, [mapInst, tool, spreadRes])
  useEffect(() => {
    if (mapInst) setFirebreakOverlay(mapInst, tool === 'firebreak' ? fbRes : null, showHazard)
  }, [mapInst, tool, fbRes, showHazard])
  useEffect(() => {
    if (mapInst) setFireStations(mapInst, stationsOn ? (stations.data?.features.map((f) => f.properties) ?? []) : null)
  }, [mapInst, stationsOn, stations.data])

  const onMapClick = useCallback(
    (p: [number, number]) => {
      if (tool === 'monitor') return false
      setToolPointState(p)
      if (tool === 'spread') setSpreadRes(null)
      else setFbRes(null)
      return true
    },
    [tool],
  )
  // Tool modes hide the 0.25° risk grid so scenario overlays read clearly; it is restored on exit.
  const savedRisk = useRef<boolean | null>(null)
  useEffect(() => {
    if (tool !== 'monitor' && savedRisk.current === null) {
      savedRisk.current = layers.risk
      setLayers((l) => ({ ...l, risk: false }))
    } else if (tool === 'monitor' && savedRisk.current !== null) {
      const prev = savedRisk.current
      savedRisk.current = null
      setLayers((l) => ({ ...l, risk: prev }))
    }
  }, [tool]) // eslint-disable-line react-hooks/exhaustive-deps

  // Frame a spread result (plus a margin) so small fires are visible.
  useEffect(() => {
    if (!mapInst || !spreadRes || tool !== 'spread') return
    const pts = spreadRes.features.flatMap((f) => (f.geometry.type === 'Polygon' ? f.geometry.coordinates.flat() : f.geometry.coordinates.flat(2))) as number[][]
    const ig = spreadRes.meta.ignition
    pts.push([ig.lon, ig.lat])
    const xs = pts.map((p) => p[0])
    const ys = pts.map((p) => p[1])
    const pad = 0.004
    mapInst.fitBounds([[Math.min(...xs) - pad, Math.min(...ys) - pad], [Math.max(...xs) + pad, Math.max(...ys) + pad]], {
      padding: { top: 90, bottom: 150, left: 40, right: window.innerWidth >= 768 ? 420 : 40 },
      maxZoom: 15,
      duration: reduceMotion ? 0 : 900,
    })
  }, [mapInst, spreadRes, tool, reduceMotion])

  const switchTool = (t: Tool) => {
    setTool(t)
    if (t !== 'monitor') {
      setCell(null)
      setDet(null)
    }
  }

  // Keep URL in sync (shareable state).
  useEffect(() => {
    const next = new URLSearchParams()
    next.set('region', regionId)
    if (date) next.set('date', date)
    if (cell) next.set('cell', cell)
    if (tool !== 'monitor') next.set('tool', tool)
    setParams(next, { replace: true })
  }, [regionId, date, cell, tool, setParams])

  // Fly to region on first load / region change, or to a linked location (?lat=&lon=, e.g. from an alert).
  // Captured once at mount: the URL-sync effect rewrites the query string afterwards.
  const [link] = useState(() => {
    const la = Number(params.get('lat'))
    const lo = Number(params.get('lon'))
    return params.has('lat') && params.has('lon') && Number.isFinite(la) && Number.isFinite(lo) ? ([lo, la] as [number, number]) : null
  })
  useEffect(() => {
    if (link) setToolPointState((p) => p ?? (tool !== 'monitor' ? link : null))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (link) {
      setFly({ center: link, zoom: 11, key: `link-${link[0]}-${link[1]}` })
      return
    }
    if (region) setFly({ center: region.center, zoom: region.zoom, key: `region-${region.id}` })
  }, [region?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const selectCell = useCallback(
    (id: string | null) => {
      setCell(id)
      if (id) setDet(null)
      const f = risk.data?.features.find((x) => x.properties.cell_id === id)
      if (f) setFly({ center: [f.properties.lon, f.properties.lat], zoom: Math.max(8.2, mapRef.current?.getZoom() ?? 8), key: `cell-${id}-${Date.now()}` })
    },
    [risk.data],
  )

  const selectDet = useCallback((d: SelectedDet | null) => {
    setDet(d)
    if (d) setCell(null)
  }, [])

  const [basemap, setBasemapChoice] = useState<Basemap>('map')
  const toggle = (k: keyof LayerToggles) => setLayers((l) => ({ ...l, [k]: !l[k] }))
  const dates = risk.data?.meta.available_dates ?? []
  const activeDate = risk.data?.meta.date ?? null
  const webgl = useMemo(() => hasWebGL(), [])

  // The top bar wraps to a second row when the window is not wide enough. On desktop
  // the side panels start just below its real height, so they never cover its controls.
  const topBarRef = useRef<HTMLDivElement>(null)
  const [panelTop, setPanelTop] = useState(78)
  useEffect(() => {
    const el = topBarRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    // offsetHeight includes the bar's 16 px bottom padding; leave a 12 px gap below the controls.
    const update = () => setPanelTop(Math.max(78, el.offsetHeight - 4))
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  return (
    <div className="relative h-dvh w-full overflow-hidden bg-[#c9c4b5]" style={{ '--panel-top': `${panelTop}px` } as React.CSSProperties}>
      {webgl ? (
        <Suspense fallback={<div className="absolute inset-0 grid place-items-center text-graphite-600">Loading map…</div>}>
          <MapView
            regions={regions.data?.regions ?? []}
            hotspots={hotspots.data}
            risk={risk.data}
            weather={weather.data}
            layers={layers}
            metric={metric}
            mode3d={mode3d}
            basemap={basemap}
            selectedCellId={cell}
            selectedDetectionId={det?.id ?? null}
            onSelectCell={selectCell}
            onSelectDetection={selectDet}
            flyTo={fly}
            onMapClick={onMapClick}
            onMapReady={(m) => {
              mapRef.current = m
              setMapInst(m)
            }}
          />
          <WindParticles map={mapInst} cells={weather.data?.cells} enabled={layers.particles === true && !reduceMotion} />
        </Suspense>
      ) : (
        <div className="absolute inset-0 grid place-items-center bg-graphite-800 p-6 text-center text-ivory/75">
          WebGL is not available in this browser, so the map cannot be drawn. The Detections and Regional analysis pages work without it.
        </div>
      )}

      {/* ---------------------------------------------------------- top bar */}
      <div ref={topBarRef} className="pointer-events-none absolute inset-x-0 top-0 z-20 flex flex-wrap items-start gap-2 p-3 md:p-4">
        <NavBar variant="overlay" />
        <div className="pointer-events-auto order-3 flex w-full items-center gap-2 md:order-none md:w-auto">
          <RegionSelect regions={regions.data} value={regionId} onChange={(id) => { setParams({ region: id }); setDate(null); setCell(null) }} />
          <PlaceSearch mapRef={mapRef} risk={risk.data} onFly={(c, z) => setFly({ center: c, zoom: z, key: Date.now() })} onCell={selectCell} />
          <div className="panel flex !rounded-xl p-0.5" role="radiogroup" aria-label="Tool">
            {TOOLS.map(({ id, label, icon: Icon }) => (
              <button key={id} type="button" role="radio" aria-checked={tool === id} onClick={() => switchTool(id)} className={`flex items-center gap-1 rounded-[10px] px-2.5 py-1.5 text-xs font-medium ${tool === id ? 'bg-graphite-800 text-ivory' : 'text-graphite-700 hover:bg-graphite-900/6'}`}>
                <Icon className="size-3.5" aria-hidden /> <span className={id === 'monitor' ? 'hidden sm:inline' : ''}>{label}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="pointer-events-auto ml-auto flex items-center gap-2">
          <SourceStatus allowRefresh />
          <div className="panel flex !rounded-xl p-0.5" role="group" aria-label="Map dimension">
            <button type="button" aria-pressed={!mode3d} onClick={() => setMode3d(false)} className={`flex items-center gap-1 rounded-[10px] px-2.5 py-1.5 text-xs font-medium ${!mode3d ? 'bg-graphite-800 text-ivory' : 'text-graphite-700'}`}>
              <Square className="size-3.5" aria-hidden /> 2D
            </button>
            <button type="button" aria-pressed={mode3d} onClick={() => setMode3d(true)} className={`flex items-center gap-1 rounded-[10px] px-2.5 py-1.5 text-xs font-medium ${mode3d ? 'bg-graphite-800 text-ivory' : 'text-graphite-700'}`}>
              <Box className="size-3.5" aria-hidden /> 3D<span className="hidden sm:inline">&nbsp;terrain</span>
            </button>
          </div>
        </div>
      </div>

      {/* ---------------------------------------------------------- layers panel */}
      <div className="pointer-events-none absolute bottom-[132px] left-3 z-20 flex flex-col-reverse md:bottom-auto md:left-4 md:top-[var(--panel-top)] md:flex-col">
        <button
          type="button"
          onClick={() => setLayersOpen((o) => !o)}
          aria-expanded={layersOpen}
          className="panel pointer-events-auto flex items-center gap-2 !rounded-xl px-3 py-2 text-xs font-medium text-graphite-900"
        >
          <Layers className="size-4" aria-hidden /> Layers & filters
          <ChevronDown className={`size-3.5 transition-transform ${layersOpen ? 'rotate-180' : ''}`} aria-hidden />
        </button>
        <AnimatePresence>
          {layersOpen && (
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: MOTION.base, ease: MOTION.ease }}
              className="panel scroll-thin pointer-events-auto mb-2 max-h-[58dvh] w-[min(280px,calc(100vw-24px))] space-y-4 overflow-y-auto p-3.5 text-graphite-900 md:mb-0 md:mt-2 md:max-h-[calc(100dvh-var(--panel-top)-182px)]"
            >
              <fieldset>
                <legend className="eyebrow text-graphite-500">Risk surface</legend>
                <div className="mt-2 grid gap-1">
                  {(
                    [
                      ['fwi', 'Fire Weather Index', 'Canadian FWI system'],
                      ['composite', 'Composite', 'Experimental, explainable'],
                      ['ndmi', 'Vegetation water', 'Sentinel-2 NDMI'],
                      ['ml', 'Detection likelihood', 'ML, experimental'],
                    ] as const
                  ).map(([k, label, hint]) => (
                    <label key={k} className={`flex cursor-pointer items-center gap-2.5 rounded-lg px-2 py-1.5 ${metric === k ? 'bg-graphite-900/8' : 'hover:bg-graphite-900/5'}`}>
                      <input type="radio" name="metric" checked={metric === k} onChange={() => setMetric(k)} className="accent-graphite-800" />
                      <span className="text-[12.5px] font-medium">{label}</span>
                      <span className="ml-auto text-[10.5px] text-graphite-500">{hint}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <fieldset>
                <legend className="eyebrow text-graphite-500">Basemap</legend>
                <div className="mt-2 grid grid-cols-2 gap-1" role="radiogroup" aria-label="Basemap">
                  {(
                    [
                      ['map', 'Map'],
                      ['satellite', 'Satellite'],
                    ] as const
                  ).map(([k, label]) => (
                    <button key={k} type="button" role="radio" aria-checked={basemap === k} onClick={() => setBasemapChoice(k)} className={`rounded-lg px-1.5 py-1 text-[11.5px] font-medium ${basemap === k ? 'bg-graphite-800 text-ivory' : 'bg-graphite-900/6 hover:bg-graphite-900/10'}`}>
                      {label}
                    </button>
                  ))}
                </div>
                {basemap === 'satellite' && <p className="mt-1.5 text-[10.5px] text-graphite-500">Sentinel-2 cloudless 2024 mosaic (EOX). Turn off the risk grid to see the ground clearly.</p>}
              </fieldset>
              <fieldset>
                <legend className="eyebrow text-graphite-500">Layers</legend>
                <div className="mt-2 grid gap-1.5">
                  <Toggle on={layers.risk} onClick={() => toggle('risk')} label="Risk grid (0.25°)" />
                  <Toggle on={layers.detections} onClick={() => toggle('detections')} label="Satellite detections" />
                  <Toggle on={layers.wind} onClick={() => toggle('wind')} label="Wind arrows (model, current hour)" />
                  <Toggle on={!!layers.particles && !reduceMotion} onClick={() => toggle('particles')} label="Wind flow (illustrative)" disabled={reduceMotion} hint={reduceMotion ? 'reduced motion' : undefined} />
                  <Toggle on={layers.outline} onClick={() => toggle('outline')} label="Region outline" />
                  <Toggle on={stationsOn} onClick={() => setStationsOn((v) => !v)} label="Fire stations (OpenStreetMap)" />
                  <Toggle on={layers.extrude} onClick={() => toggle('extrude')} label="Extrude cells by value" disabled={!mode3d} hint={!mode3d ? '3D only' : undefined} />
                </div>
              </fieldset>
              <fieldset>
                <legend className="eyebrow text-graphite-500">Detections window</legend>
                <div className="mt-2 grid grid-cols-4 gap-1" role="radiogroup">
                  {WINDOWS.map((w) => (
                    <button key={w.h} type="button" role="radio" aria-checked={hours === w.h} onClick={() => setHours(w.h)} className={`rounded-lg px-1.5 py-1 text-[11.5px] font-medium ${hours === w.h ? 'bg-graphite-800 text-ivory' : 'bg-graphite-900/6 hover:bg-graphite-900/10'}`}>
                      {w.label}
                    </button>
                  ))}
                </div>
                <div className="mt-2 text-[11px] text-graphite-600" aria-live="polite">
                  {hotspots.error && !hotspots.data ? (
                    <ErrorState error={hotspots.error} onRetry={hotspots.refresh} compact />
                  ) : hotspots.data ? (
                    <>
                      <b className="font-mono text-graphite-900">{hotspots.data.meta.count.toLocaleString()}</b> detections in the India window
                      {hotspots.data.meta.truncated && ' (truncated)'} · FIRMS updated {timeAgo(hotspots.data.meta.provider_status.last_success)}
                    </>
                  ) : (
                    'Loading detections…'
                  )}
                </div>
              </fieldset>
              <div className="[&>.panel]:!w-full [&>.panel]:!border-0 [&>.panel]:!bg-transparent [&>.panel]:!p-0 [&>.panel]:!shadow-none">
                <Legend metric={metric} showDetections={layers.detections} showParticles={!!layers.particles && !reduceMotion} fwi={fwiScheme} />
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* ---------------------------------------------------------- detail panel */}
      <div className="pointer-events-none absolute bottom-3 right-3 top-[38dvh] z-30 flex w-[calc(100vw-24px)] flex-col md:bottom-[120px] md:right-4 md:top-[var(--panel-top)] md:w-[min(380px,calc(100vw-24px))]">
        <AnimatePresence mode="wait">
          {tool === 'spread' && (
            <div key="tool-spread" className="pointer-events-auto flex max-h-full min-h-0 flex-col">
              <SpreadTool point={toolPoint} result={spreadRes} onResult={setSpreadRes} onClose={() => switchTool('monitor')} />
            </div>
          )}
          {tool === 'firebreak' && (
            <div key="tool-fb" className="pointer-events-auto flex max-h-full min-h-0 flex-col">
              <FirebreakTool
                point={toolPoint}
                radius={fbRadius}
                onRadius={(r) => { setFbRadius(r); setFbRes(null) }}
                result={fbRes}
                onResult={(r) => {
                  setFbRes(r)
                  if (r && toolPoint) setFly({ center: toolPoint, zoom: fbRadius <= 6 ? 11.2 : 10.4, key: `fb-${Date.now()}` })
                }}
                showHazard={showHazard}
                onShowHazard={setShowHazard}
                onFocus={(c) => setFly({ center: c, zoom: 13, key: `fbf-${Date.now()}` })}
                onClose={() => switchTool('monitor')}
              />
            </div>
          )}
          {tool === 'monitor' && cell && (
            <div key={`c-${cell}`} className="pointer-events-auto flex max-h-full min-h-0 flex-col">
              <CellPanel cellId={cell} date={activeDate} onClose={() => setCell(null)} onFlyTo={(lon, lat) => setFly({ center: [lon, lat], zoom: 11, key: Date.now() })} />
            </div>
          )}
          {tool === 'monitor' && det && !cell && (
            <div key={`d-${det.id}`} className="pointer-events-auto flex max-h-full min-h-0 flex-col">
              <DetectionPanel
                d={det}
                onClose={() => setDet(null)}
                onInspectCell={(id) => selectCell(id)}
                onSimulate={(lon, lat) => {
                  setTool('spread')
                  setToolPointState([lon, lat])
                  setSpreadRes(null)
                  setDet(null)
                  setFly({ center: [lon, lat], zoom: 12, key: `sim-${Date.now()}` })
                }}
              />
            </div>
          )}
        </AnimatePresence>
      </div>

      {/* ---------------------------------------------------------- legend + timeline */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 flex flex-col-reverse items-stretch gap-2 p-3 md:flex-row md:items-end md:p-4 md:pb-8">
        <div className={`pointer-events-auto hidden ${layersOpen ? '' : 'md:block'}`}>
          <Legend metric={metric} showDetections={layers.detections} showParticles={!!layers.particles && !reduceMotion} fwi={fwiScheme} />
        </div>
        <div className="pointer-events-auto md:ml-auto md:mr-[min(400px,30vw)]">
          {region && !region.enabled ? (
            <div className="panel max-w-md p-3 text-[12.5px] text-graphite-800">
              <b>{region.name}</b>: risk grid not ingested yet. Satellite detections are shown. Enable it with <code className="font-mono">INCENDIUM_RISK_REGIONS</code>.
            </div>
          ) : risk.error && !risk.data ? (
            <div className="panel p-3"><ErrorState error={risk.error} onRetry={risk.refresh} title="Risk grid unavailable" /></div>
          ) : (
            <DateStrip dates={dates} active={activeDate} onPick={(d) => setDate(d)} computedAt={risk.data?.meta.computed_at ?? null} />
          )}
        </div>
      </div>
    </div>
  )
}

function Toggle({ on, onClick, label, disabled, hint }: { on: boolean; onClick: () => void; label: string; disabled?: boolean; hint?: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} disabled={disabled} onClick={onClick} className="flex items-center gap-2.5 rounded-lg px-2 py-1 text-left text-[12.5px] hover:bg-graphite-900/5 disabled:opacity-45">
      <span className={`relative h-4 w-7 rounded-full transition-colors ${on && !disabled ? 'bg-graphite-800' : 'bg-graphite-900/20'}`} aria-hidden>
        <span className={`absolute top-0.5 size-3 rounded-full bg-ivory transition-transform ${on && !disabled ? 'translate-x-3.5' : 'translate-x-0.5'}`} />
      </span>
      {label}
      {hint && <span className="ml-auto text-[10.5px] text-graphite-500">{hint}</span>}
    </button>
  )
}

function RegionSelect({ regions, value, onChange }: { regions: RegionsResponse | undefined; value: string; onChange: (id: string) => void }) {
  return (
    <label className="panel flex items-center gap-1.5 !rounded-xl py-1 pl-2.5 pr-1 text-xs text-graphite-900">
      <MapPin className="size-3.5 text-graphite-600" aria-hidden />
      <span className="sr-only">Region</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} className="cursor-pointer bg-transparent py-1 pr-1 text-[12.5px] font-medium outline-none">
        {(regions?.regions ?? []).map((r) => (
          <option key={r.id} value={r.id}>
            {r.name}{r.enabled ? '' : ' (detections only)'}
          </option>
        ))}
      </select>
    </label>
  )
}

/** Search: "lat, lon", a grid cell, or a place name visible in the loaded basemap tiles (no external geocoder). */
function PlaceSearch({ mapRef, risk, onFly, onCell }: { mapRef: React.RefObject<MLMap | null>; risk?: RiskMapResponse; onFly: (c: [number, number], z: number) => void; onCell: (id: string) => void }) {
  const [q, setQ] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  function submit(e: FormEvent) {
    e.preventDefault()
    setMsg(null)
    const s = q.trim()
    if (!s) return
    const m = s.match(/^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$/)
    if (m) {
      const lat = Number(m[1])
      const lon = Number(m[2])
      if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return setMsg('Coordinates out of range.')
      const f = risk?.features.find((x) => {
        const [w, so] = [x.properties.lon - 0.125, x.properties.lat - 0.125]
        return lon >= w && lon < w + 0.25 && lat >= so && lat < so + 0.25
      })
      if (f) onCell(f.properties.cell_id)
      else onFly([lon, lat], 9)
      return
    }
    const map = mapRef.current
    if (!map) return
    const needle = s.toLowerCase()
    const feats = map.querySourceFeatures('openmaptiles', { sourceLayer: 'place' })
    const hit = feats
      .filter((f) => String(f.properties?.name ?? f.properties?.['name:en'] ?? '').toLowerCase().includes(needle))
      .sort((a, b) => Number(a.properties?.rank ?? 99) - Number(b.properties?.rank ?? 99))[0]
    if (hit && hit.geometry.type === 'Point') {
      onFly(hit.geometry.coordinates as [number, number], 10)
      setMsg(`→ ${hit.properties?.name}`)
    } else setMsg('Not found in the visible map. Try zooming nearer, or enter “lat, lon”.')
  }
  return (
    <form onSubmit={submit} className="panel relative flex items-center gap-1.5 !rounded-xl py-1 pl-2.5 pr-1" role="search">
      <Search className="size-3.5 text-graphite-600" aria-hidden />
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Place or lat, lon"
        aria-label="Search place or coordinates"
        className="w-32 bg-transparent py-1 text-[12.5px] text-graphite-900 outline-none placeholder:text-graphite-500 md:w-44"
      />
      {msg && <div className="absolute left-0 top-full mt-1 w-64 rounded-lg bg-graphite-900 px-2 py-1 text-[11px] text-ivory" role="status">{msg}</div>}
    </form>
  )
}

function DateStrip({ dates, active, onPick, computedAt }: { dates: { date: string; is_forecast: boolean }[]; active: string | null; onPick: (d: string) => void; computedAt: string | null }) {
  const recent = dates.slice(-10)
  if (!recent.length)
    return <div className="panel p-3 text-[12px] text-graphite-700">Risk grid is being computed. FWI needs 45 days of weather spin-up before results are shown.</div>
  return (
    <div className="panel p-2">
      <div className="flex items-center justify-between px-1">
        <div className="eyebrow text-graphite-500">Risk date (local)</div>
        <div className="text-[10.5px] text-graphite-500">computed {timeAgo(computedAt)} · {computedAt ? fmtIst(computedAt) : ''}</div>
      </div>
      <div className="scroll-thin mt-1.5 flex gap-1 overflow-x-auto pb-0.5" role="radiogroup" aria-label="Risk date">
        {recent.map((d) => (
          <button
            key={d.date}
            type="button"
            role="radio"
            aria-checked={active === d.date}
            onClick={() => onPick(d.date)}
            className={`flex shrink-0 flex-col items-center rounded-lg px-2.5 py-1 text-[11px] transition-colors ${active === d.date ? 'bg-graphite-800 text-ivory' : 'hover:bg-graphite-900/8 text-graphite-800'} ${d.is_forecast ? 'border border-dashed border-graphite-600/50' : ''}`}
          >
            <span className="font-medium">{fmtDate(d.date).split(' ').slice(0, 2).join(' ')}</span>
            {d.is_forecast ? <span className="text-[9.5px] opacity-75">forecast</span> : <span className="text-[9.5px] opacity-60">analysis</span>}
          </button>
        ))}
      </div>
      {recent.find((d) => d.date === active)?.is_forecast && (
        <div className="mt-1 px-1"><Tag tone="forecast">Showing a forecast day: model-predicted weather, not observed</Tag></div>
      )}
    </div>
  )
}
