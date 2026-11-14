import * as maplibregl from 'maplibre-gl'
import type { GeoJSONSource, Map as MLMap, MapGeoJSONFeature } from 'maplibre-gl'
// MapLibre 6 resolves its worker relative to its own module URL, which bundlers
// cannot follow. Let Vite bundle the worker (with its shared chunk) explicitly.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'

maplibregl.setWorkerUrl(maplibreWorkerUrl)
import { useEffect, useRef, useState } from 'react'
import { fmtIst } from '../lib/format'
import { addTerrainSources, applySky, loadIncendiumStyle, setTerrain, windArrowImage } from '../lib/mapStyle'
import { MOTION, prefersReducedMotion } from '../lib/motion'
import { classColorExpression, COMPOSITE_CLASSES, DETECTION_COLOR, FWI_CLASSES, ML_STOPS, NDMI_RAMP, NO_DATA_COLOR } from '../lib/risk'
import type { DetectionProps, HotspotsResponse, Region, RiskMapResponse, WeatherResponse } from '../lib/types'

export type Metric = 'fwi' | 'composite' | 'ndmi' | 'ml'

export interface LayerToggles {
  detections: boolean
  risk: boolean
  wind: boolean
  outline: boolean
  extrude: boolean
  particles?: boolean
}

export interface FlyTarget {
  center: [number, number]
  zoom: number
  key: string | number
  pitch?: number
}

interface Props {
  regions: Region[]
  hotspots?: HotspotsResponse
  risk?: RiskMapResponse
  weather?: WeatherResponse
  layers: LayerToggles
  metric: Metric
  mode3d: boolean
  selectedCellId?: string | null
  selectedDetectionId?: string | null
  onSelectCell?: (id: string | null) => void
  onSelectDetection?: (d: (DetectionProps & { lon: number; lat: number }) | null) => void
  flyTo?: FlyTarget | null
  initialView?: { center: [number, number]; zoom: number }
  onMapReady?: (map: MLMap) => void
  /** Tool modes: return true to consume the click (skips cell/detection selection). */
  onMapClick?: (lngLat: [number, number]) => boolean
  className?: string
  interactive?: boolean
}

const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] }

function fillColor(metric: Metric): maplibregl.ExpressionSpecification {
  if (metric === 'fwi') return classColorExpression('fwi_class', FWI_CLASSES) as maplibregl.ExpressionSpecification
  if (metric === 'composite') return classColorExpression('composite_class', COMPOSITE_CLASSES) as maplibregl.ExpressionSpecification
  if (metric === 'ml')
    return ['case', ['==', ['get', 'ml_probability'], null], NO_DATA_COLOR, ['interpolate', ['linear'], ['get', 'ml_probability'], ...ML_STOPS.flat()]] as maplibregl.ExpressionSpecification
  return [
    'case',
    ['==', ['get', 'ndmi'], null],
    NO_DATA_COLOR,
    // Validated single-hue sequential ramp: darker = higher NDMI (more vegetation water).
    ['interpolate', ['linear'], ['get', 'ndmi'], -0.2, NDMI_RAMP[0], 0, NDMI_RAMP[1], 0.15, NDMI_RAMP[2], 0.3, NDMI_RAMP[3], 0.45, NDMI_RAMP[4]],
  ] as maplibregl.ExpressionSpecification
}

function hasValue(metric: Metric): maplibregl.ExpressionSpecification {
  const prop = metric === 'fwi' ? 'fwi' : metric === 'composite' ? 'composite' : metric === 'ml' ? 'ml_probability' : 'ndmi'
  return ['!=', ['get', prop], null]
}

export default function MapView(props: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MLMap | null>(null)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  const cb = useRef(props)
  cb.current = props

  // ------------------------------------------------------------------ init
  useEffect(() => {
    const ctrl = new AbortController()
    let map: MLMap | null = null
    let raf = 0
    loadIncendiumStyle(ctrl.signal)
      .then((style) => {
        if (!container.current) return
        const init = cb.current.initialView ?? { center: [78.9, 21.5] as [number, number], zoom: 4.2 }
        const created = new maplibregl.Map({
          container: container.current,
          style,
          center: init.center,
          zoom: init.zoom,
          maxPitch: 75,
          attributionControl: false,
          interactive: cb.current.interactive ?? true,
          fadeDuration: prefersReducedMotion() ? 0 : 300,
        })
        map = created
        mapRef.current = created
        created.addControl(
          new maplibregl.AttributionControl({
            compact: true,
            customAttribution: [
              'Detections: NASA FIRMS',
              'Weather: <a href="https://open-meteo.com/" target="_blank" rel="noopener">Open-Meteo.com</a> (CC BY 4.0)',
              'Contains modified Copernicus Sentinel data',
            ],
          }),
          'bottom-right',
        )
        created.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left')
        created.on('error', (e) => {
          // Tile errors are transient; surface only fatal style errors.
          if ((e.error as Error | undefined)?.message?.includes('style')) setFailed((e.error as Error).message)
        })
        created.on('load', () => {
          const m = created
          addTerrainSources(m)
          applySky(m)
          const labelFont = (m.getStyle().layers.find((l) => l.type === 'symbol' && (l.layout as Record<string, unknown> | undefined)?.['text-font']) as
            | { layout: { 'text-font': string[] } }
            | undefined)?.layout['text-font'] ?? ['Noto Sans Regular']
          const firstSymbol = m.getStyle().layers.find((l) => l.type === 'symbol')?.id
          m.addImage('wind-arrow', windArrowImage(48), { pixelRatio: 2 })

          m.addSource('risk', { type: 'geojson', data: EMPTY, promoteId: 'cell_id' })
          m.addSource('outline', { type: 'geojson', data: EMPTY })
          m.addSource('selected-cell', { type: 'geojson', data: EMPTY })
          m.addSource('wind', { type: 'geojson', data: EMPTY })
          m.addSource('detections', { type: 'geojson', data: EMPTY, cluster: true, clusterMaxZoom: 6, clusterRadius: 26, promoteId: 'id' })
          m.addSource('selected-det', { type: 'geojson', data: EMPTY })

          m.addLayer(
            {
              id: 'risk-fill',
              type: 'fill',
              source: 'risk',
              paint: {
                'fill-color': fillColor('fwi'),
                'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.86, ['case', hasValue('fwi'), 0.68, 0.22]],
                'fill-outline-color': 'rgba(35,45,48,0.25)',
              },
            },
            firstSymbol,
          )
          m.addLayer({
            id: 'risk-extrude',
            type: 'fill-extrusion',
            source: 'risk',
            layout: { visibility: 'none' },
            paint: {
              'fill-extrusion-color': fillColor('fwi'),
              'fill-extrusion-height': ['*', ['coalesce', ['get', 'fwi'], 0], 550],
              'fill-extrusion-opacity': 0.82,
              'fill-extrusion-vertical-gradient': true,
            },
          })
          m.addLayer({
            id: 'outline-casing',
            type: 'line',
            source: 'outline',
            paint: { 'line-color': '#e8e5db', 'line-width': 3.5, 'line-opacity': 0.5 },
          })
          m.addLayer({
            id: 'outline-line',
            type: 'line',
            source: 'outline',
            paint: { 'line-color': '#26383b', 'line-width': 1.3, 'line-dasharray': [3, 2] },
          })
          m.addLayer({
            id: 'wind',
            type: 'symbol',
            source: 'wind',
            layout: {
              'icon-image': 'wind-arrow',
              'icon-rotate': ['+', ['get', 'wd'], 180],
              'icon-rotation-alignment': 'map',
              'icon-allow-overlap': true,
              'icon-ignore-placement': true,
              'icon-size': ['interpolate', ['linear'], ['get', 'ws'], 0, 0.35, 10, 0.55, 30, 0.95],
            },
            paint: { 'icon-opacity': ['interpolate', ['linear'], ['zoom'], 4, 0.55, 8, 0.85] },
          })
          m.addLayer({
            id: 'selected-cell-casing',
            type: 'line',
            source: 'selected-cell',
            paint: { 'line-color': '#1c2426', 'line-width': 5, 'line-opacity': 0.55 },
          })
          m.addLayer({
            id: 'selected-cell-line',
            type: 'line',
            source: 'selected-cell',
            paint: { 'line-color': '#f3f1ea', 'line-width': 2.2 },
          })
          // Detections: clusters at continental zoom, individual pixels when zoomed in.
          m.addLayer({
            id: 'det-cluster',
            type: 'circle',
            source: 'detections',
            filter: ['has', 'point_count'],
            paint: {
              'circle-color': DETECTION_COLOR,
              'circle-opacity': 0.82,
              'circle-stroke-color': '#f3f1ea',
              'circle-stroke-width': 1.2,
              'circle-radius': ['step', ['get', 'point_count'], 9, 10, 12, 50, 16, 200, 21, 1000, 27],
            },
          })
          m.addLayer({
            id: 'det-cluster-count',
            type: 'symbol',
            source: 'detections',
            filter: ['has', 'point_count'],
            layout: {
              'text-field': ['get', 'point_count_abbreviated'],
              'text-font': labelFont,
              'text-size': 11,
              'text-allow-overlap': true,
            },
            paint: { 'text-color': '#1c2426' },
          })
          m.addLayer({
            id: 'det-halo',
            type: 'circle',
            source: 'detections',
            filter: ['!', ['has', 'point_count']],
            paint: {
              'circle-color': DETECTION_COLOR,
              'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 6, 10, 14],
              'circle-blur': 1,
              'circle-opacity': 0.35,
            },
          })
          m.addLayer({
            id: 'det-pulse',
            type: 'circle',
            source: 'detections',
            filter: ['all', ['!', ['has', 'point_count']], ['==', ['get', 'recent'], true]],
            paint: {
              'circle-color': 'rgba(0,0,0,0)',
              'circle-stroke-color': DETECTION_COLOR,
              'circle-stroke-width': 1.4,
              'circle-radius': 8,
              'circle-stroke-opacity': 0.6,
            },
          })
          m.addLayer({
            id: 'det-point',
            type: 'circle',
            source: 'detections',
            filter: ['!', ['has', 'point_count']],
            paint: {
              'circle-color': DETECTION_COLOR,
              'circle-radius': [
                'interpolate', ['linear'], ['zoom'],
                4, ['interpolate', ['linear'], ['sqrt', ['coalesce', ['get', 'frp'], 1]], 0, 2.2, 10, 4.5],
                10, ['interpolate', ['linear'], ['sqrt', ['coalesce', ['get', 'frp'], 1]], 0, 4, 10, 9],
              ],
              'circle-stroke-color': '#f3f1ea',
              'circle-stroke-width': ['case', ['boolean', ['feature-state', 'hover'], false], 2.2, 1],
            },
          })
          m.addLayer({
            id: 'selected-det',
            type: 'circle',
            source: 'selected-det',
            paint: { 'circle-color': 'rgba(0,0,0,0)', 'circle-radius': 16, 'circle-stroke-color': '#f3f1ea', 'circle-stroke-width': 2 },
          })

          // ---------------------------------------------------------- interactions
          let hoverCell: string | number | undefined
          let hoverDet: string | number | undefined
          const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12, maxWidth: '260px' })
          m.on('mousemove', 'risk-fill', (e) => {
            const f = e.features?.[0]
            if (!f) return
            if (hoverCell !== undefined) m.setFeatureState({ source: 'risk', id: hoverCell }, { hover: false })
            hoverCell = f.id
            if (hoverCell !== undefined) m.setFeatureState({ source: 'risk', id: hoverCell }, { hover: true })
            m.getCanvas().style.cursor = 'pointer'
          })
          m.on('mouseleave', 'risk-fill', () => {
            if (hoverCell !== undefined) m.setFeatureState({ source: 'risk', id: hoverCell }, { hover: false })
            hoverCell = undefined
            m.getCanvas().style.cursor = ''
          })
          m.on('mousemove', 'det-point', (e) => {
            const f = e.features?.[0] as MapGeoJSONFeature | undefined
            if (!f) return
            if (hoverDet !== undefined) m.setFeatureState({ source: 'detections', id: hoverDet }, { hover: false })
            hoverDet = f.id
            if (hoverDet !== undefined) m.setFeatureState({ source: 'detections', id: hoverDet }, { hover: true })
            m.getCanvas().style.cursor = 'pointer'
            const p = f.properties as DetectionProps
            const coords = (f.geometry as GeoJSON.Point).coordinates as [number, number]
            const el = document.createElement('div')
            el.className = 'text-xs leading-snug'
            const strong = document.createElement('div')
            strong.className = 'font-semibold'
            strong.textContent = 'Thermal anomaly (VIIRS)'
            const line = document.createElement('div')
            line.textContent = `${fmtIst(p.acq_datetime)} · ${p.satellite} · conf. ${p.confidence ?? '—'}`
            const frp = document.createElement('div')
            frp.textContent = `FRP ${p.frp ?? '—'} MW${p.state ? ` · ${p.state}` : ''}`
            el.append(strong, line, frp)
            popup.setLngLat(coords).setDOMContent(el).addTo(m)
          })
          m.on('mouseleave', 'det-point', () => {
            if (hoverDet !== undefined) m.setFeatureState({ source: 'detections', id: hoverDet }, { hover: false })
            hoverDet = undefined
            m.getCanvas().style.cursor = ''
            popup.remove()
          })
          m.on('click', (e) => {
            if (cb.current.onMapClick?.([e.lngLat.lng, e.lngLat.lat])) return
            const hits = m.queryRenderedFeatures(e.point, { layers: ['det-cluster', 'det-point', 'risk-fill'] })
            const top = hits[0]
            if (!top) {
              cb.current.onSelectCell?.(null)
              cb.current.onSelectDetection?.(null)
              return
            }
            if (top.layer.id === 'det-cluster') {
              const src = m.getSource('detections') as GeoJSONSource
              const coords = (top.geometry as GeoJSON.Point).coordinates as [number, number]
              src.getClusterExpansionZoom(top.properties.cluster_id as number).then((z) =>
                m.easeTo({ center: coords, zoom: Math.min(z + 0.5, 12), duration: prefersReducedMotion() ? 0 : MOTION.camera.short * 1000 }),
              )
              return
            }
            if (top.layer.id === 'det-point') {
              const coords = (top.geometry as GeoJSON.Point).coordinates as [number, number]
              cb.current.onSelectDetection?.({ ...(top.properties as DetectionProps), lon: coords[0], lat: coords[1] })
              return
            }
            cb.current.onSelectCell?.(String(top.properties.cell_id))
          })

          // Subtle pulse on detections from the last 12 h (disabled for reduced motion).
          if (!prefersReducedMotion()) {
            let last = 0
            const tick = (t: number) => {
              raf = requestAnimationFrame(tick)
              if (t - last < 40 || !m.getLayer('det-pulse')) return
              last = t
              const phase = (t % 2600) / 2600
              m.setPaintProperty('det-pulse', 'circle-radius', 6 + phase * 16)
              m.setPaintProperty('det-pulse', 'circle-stroke-opacity', 0.65 * (1 - phase))
            }
            raf = requestAnimationFrame(tick)
          }
          setReady(true)
          cb.current.onMapReady?.(m)
        })
      })
      .catch((e: Error) => {
        if (e.name !== 'AbortError') setFailed(e.message)
      })
    return () => {
      ctrl.abort()
      cancelAnimationFrame(raf)
      map?.remove() // releases the WebGL context
      mapRef.current = null
    }
  }, [])

  // ------------------------------------------------------------------ data updates
  useEffect(() => {
    const m = mapRef.current
    if (!ready || !m) return
    const now = Date.now()
    const fc = props.hotspots
      ? {
          type: 'FeatureCollection' as const,
          features: props.hotspots.features.map((f) => ({
            ...f,
            properties: { ...f.properties, recent: now - new Date(f.properties.acq_datetime).getTime() < 12 * 3600_000 },
          })),
        }
      : EMPTY
    ;(m.getSource('detections') as GeoJSONSource).setData(fc as GeoJSON.FeatureCollection)
  }, [ready, props.hotspots])

  useEffect(() => {
    const m = mapRef.current
    if (!ready || !m) return
    ;(m.getSource('risk') as GeoJSONSource).setData((props.risk ?? EMPTY) as GeoJSON.FeatureCollection)
  }, [ready, props.risk])

  useEffect(() => {
    const m = mapRef.current
    if (!ready || !m) return
    const enabled = props.regions.filter((r) => r.enabled)
    ;(m.getSource('outline') as GeoJSONSource).setData({
      type: 'FeatureCollection',
      features: enabled.map((r) => ({ type: 'Feature', properties: { id: r.id }, geometry: r.outline as GeoJSON.Geometry })),
    })
  }, [ready, props.regions])

  useEffect(() => {
    const m = mapRef.current
    if (!ready || !m) return
    const fc: GeoJSON.FeatureCollection = {
      type: 'FeatureCollection',
      features: (props.weather?.cells ?? [])
        .filter((c) => c.wd10 !== null && c.ws10 !== null)
        .map((c) => ({ type: 'Feature', properties: { wd: c.wd10, ws: c.ws10 }, geometry: { type: 'Point', coordinates: [c.lon, c.lat] } })),
    }
    ;(m.getSource('wind') as GeoJSONSource).setData(fc)
  }, [ready, props.weather])

  useEffect(() => {
    const m = mapRef.current
    if (!ready || !m) return
    const f = props.risk?.features.find((x) => x.properties.cell_id === props.selectedCellId)
    ;(m.getSource('selected-cell') as GeoJSONSource).setData(f ? { type: 'FeatureCollection', features: [f as GeoJSON.Feature] } : EMPTY)
  }, [ready, props.selectedCellId, props.risk])

  useEffect(() => {
    const m = mapRef.current
    if (!ready || !m) return
    const f = props.hotspots?.features.find((x) => x.properties.id === props.selectedDetectionId)
    ;(m.getSource('selected-det') as GeoJSONSource).setData(f ? { type: 'FeatureCollection', features: [f as GeoJSON.Feature] } : EMPTY)
  }, [ready, props.selectedDetectionId, props.hotspots])

  // ------------------------------------------------------------------ style toggles
  useEffect(() => {
    const m = mapRef.current
    if (!ready || !m) return
    const vis = (on: boolean) => (on ? 'visible' : 'none')
    const { layers, metric, mode3d } = props
    m.setPaintProperty('risk-fill', 'fill-color', fillColor(metric))
    m.setPaintProperty('risk-fill', 'fill-opacity', [
      'case', ['boolean', ['feature-state', 'hover'], false], 0.86, ['case', hasValue(metric), 0.68, 0.22],
    ])
    m.setPaintProperty('risk-extrude', 'fill-extrusion-color', fillColor(metric))
    m.setPaintProperty(
      'risk-extrude',
      'fill-extrusion-height',
      metric === 'fwi'
        ? ['*', ['coalesce', ['get', 'fwi'], 0], 550]
        : metric === 'composite'
          ? ['*', ['coalesce', ['get', 'composite'], 0], 260]
          : metric === 'ml'
            ? ['*', ['coalesce', ['get', 'ml_probability'], 0], 26000]
          : ['*', ['max', 0, ['-', 0.5, ['coalesce', ['get', 'ndmi'], 0.5]]], 30000],
    )
    const showExtrude = layers.risk && layers.extrude && mode3d
    m.setLayoutProperty('risk-fill', 'visibility', vis(layers.risk && !showExtrude))
    m.setLayoutProperty('risk-extrude', 'visibility', vis(showExtrude))
    for (const id of ['det-cluster', 'det-cluster-count', 'det-halo', 'det-pulse', 'det-point', 'selected-det']) m.setLayoutProperty(id, 'visibility', vis(layers.detections))
    m.setLayoutProperty('wind', 'visibility', vis(layers.wind))
    for (const id of ['outline-casing', 'outline-line']) m.setLayoutProperty(id, 'visibility', vis(layers.outline))
  }, [ready, props.layers, props.metric, props.mode3d])

  useEffect(() => {
    const m = mapRef.current
    if (!ready || !m) return
    const reduce = prefersReducedMotion()
    setTerrain(m, props.mode3d)
    const opts = props.mode3d ? { pitch: 58, bearing: -14 } : { pitch: 0, bearing: 0 }
    if (reduce) m.jumpTo(opts)
    else m.easeTo({ ...opts, duration: MOTION.camera.short * 1000, easing: (t) => 1 - Math.pow(1 - t, 3) })
  }, [ready, props.mode3d])

  useEffect(() => {
    const m = mapRef.current
    const t = props.flyTo
    if (!ready || !m || !t) return
    if (prefersReducedMotion()) m.jumpTo({ center: t.center, zoom: t.zoom, ...(t.pitch !== undefined ? { pitch: t.pitch } : {}) })
    else m.flyTo({ center: t.center, zoom: t.zoom, ...(t.pitch !== undefined ? { pitch: t.pitch } : {}), duration: MOTION.camera.long * 1000, curve: 1.45, essential: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, props.flyTo?.key])

  return (
    <div className={`relative ${props.className ?? 'h-full w-full'}`}>
      {/* MapLibre adds `.maplibregl-map { position: relative }`, so size explicitly. */}
      <div ref={container} className="h-full w-full" role="region" aria-label="Interactive map of fire detections and risk grid" />
      {!ready && !failed && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center bg-[#c9c4b5]">
          <div className="eyebrow text-graphite-600">Loading basemap & terrain…</div>
        </div>
      )}
      {failed && (
        <div className="absolute inset-0 grid place-items-center bg-graphite-800 p-6 text-center text-ivory/80" role="alert">
          <div>
            <div className="font-display text-lg">Map unavailable</div>
            <p className="mt-1 max-w-sm text-sm text-ivory/60">{failed}. Detection and risk data remain available in the tables on other pages.</p>
          </div>
        </div>
      )}
    </div>
  )
}
