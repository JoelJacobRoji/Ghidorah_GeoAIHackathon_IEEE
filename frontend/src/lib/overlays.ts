import type { GeoJSONSource, ImageSource, Map as MLMap } from 'maplibre-gl'
import type { FirebreakResult, SpreadResult, StationRef } from './types'

const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] }

// Spread arrival time: single-hue ordinal ramp, darkest = earliest (closest to ignition).
export const SPREAD_RAMP = ['#430c05', '#5e1400', '#792000', '#913200', '#a94300', '#bf5914'] // validated ordinal ramp (single hue, ΔL ≥ 0.06, light end 2.6:1 on the basemap)
export const CORRIDOR_COLOR = '#1f5fa8'
export const HUMAN_ZONE_COLOR = '#6b5a3e'
export const STATION_COLOR = '#c0182a'

function firstSymbolId(m: MLMap): string | undefined {
  return m.getStyle().layers.find((l) => l.type === 'symbol')?.id
}

function ensureSource(m: MLMap, id: string, data: GeoJSON.FeatureCollection = EMPTY) {
  if (!m.getSource(id)) m.addSource(id, { type: 'geojson', data })
  return m.getSource(id) as GeoJSONSource
}

function labelFont(m: MLMap): string[] {
  const l = m.getStyle().layers.find((x) => x.type === 'symbol' && (x.layout as Record<string, unknown> | undefined)?.['text-font']) as
    | { layout: { 'text-font': string[] } }
    | undefined
  return l?.layout['text-font'] ?? ['Noto Sans Regular']
}

/** Circle polygon (approximate, for display) around a point. */
export function circlePolygon(lon: number, lat: number, radiusKm: number, steps = 72): GeoJSON.Feature<GeoJSON.Polygon> {
  const coords: [number, number][] = []
  const dLat = radiusKm / 110.54
  const dLon = radiusKm / (111.32 * Math.cos((lat * Math.PI) / 180))
  for (let i = 0; i <= steps; i++) {
    const a = (i / steps) * 2 * Math.PI
    coords.push([lon + dLon * Math.cos(a), lat + dLat * Math.sin(a)])
  }
  return { type: 'Feature', geometry: { type: 'Polygon', coordinates: [coords] }, properties: {} }
}

// --------------------------------------------------------------------------- tool point
export function setToolPoint(m: MLMap, p: [number, number] | null, radiusKm?: number) {
  const pt = ensureSource(m, 'tool-point')
  const ring = ensureSource(m, 'tool-ring')
  const before = firstSymbolId(m)
  if (!m.getLayer('tool-ring-line'))
    m.addLayer({ id: 'tool-ring-line', type: 'line', source: 'tool-ring', paint: { 'line-color': '#232d30', 'line-width': 1.5, 'line-dasharray': [3, 2], 'line-opacity': 0.8 } }, before)
  if (!m.getLayer('tool-point-dot'))
    m.addLayer({ id: 'tool-point-dot', type: 'circle', source: 'tool-point', paint: { 'circle-radius': 7, 'circle-color': '#e77943', 'circle-stroke-color': '#fff', 'circle-stroke-width': 2.5 } })
  pt.setData(p ? { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: p }, properties: {} }] } : EMPTY)
  ring.setData(p && radiusKm ? { type: 'FeatureCollection', features: [circlePolygon(p[0], p[1], radiusKm)] } : EMPTY)
}

// --------------------------------------------------------------------------- spread
export function setSpreadOverlay(m: MLMap, r: SpreadResult | null) {
  const src = ensureSource(m, 'spread')
  const before = firstSymbolId(m)
  const n = Math.max(1, r?.meta.hours ?? 1)
  const colorExpr: unknown[] = ['match', ['get', 'hour']]
  for (let h = 1; h <= 12; h++) colorExpr.push(h, SPREAD_RAMP[Math.min(SPREAD_RAMP.length - 1, Math.round(((h - 1) / Math.max(1, n - 1)) * (SPREAD_RAMP.length - 1)))])
  colorExpr.push('#999')
  if (!m.getLayer('spread-fill')) {
    m.addLayer({ id: 'spread-fill', type: 'fill', source: 'spread', paint: { 'fill-color': colorExpr as never, 'fill-opacity': 0.72 } }, before)
    m.addLayer({ id: 'spread-line', type: 'line', source: 'spread', paint: { 'line-color': '#fff', 'line-width': 0.8, 'line-opacity': 0.75 } }, before)
  } else {
    m.setPaintProperty('spread-fill', 'fill-color', colorExpr as never)
  }
  // Largest (latest) first so earlier isochrones draw on top.
  const feats = r ? [...r.features].sort((a, b) => b.properties.hour - a.properties.hour) : []
  src.setData({ type: 'FeatureCollection', features: feats } as GeoJSON.FeatureCollection)
}

// --------------------------------------------------------------------------- firebreaks
export function setFirebreakOverlay(m: MLMap, r: FirebreakResult | null, showHazard = true) {
  const before = firstSymbolId(m)
  if (m.getLayer('fb-hazard')) m.removeLayer('fb-hazard')
  if (m.getSource('fb-hazard')) m.removeSource('fb-hazard')
  if (r && showHazard) {
    const [w, s, e, n] = r.meta.hazard_image.bounds
    m.addSource('fb-hazard', { type: 'image', url: r.meta.hazard_image.url, coordinates: [[w, n], [e, n], [e, s], [w, s]] })
    m.addLayer({ id: 'fb-hazard', type: 'raster', source: 'fb-hazard', paint: { 'raster-opacity': 0.8, 'raster-resampling': 'nearest' } }, before)
  }
  const zone = ensureSource(m, 'fb-human-zone')
  if (!m.getLayer('fb-zone-fill')) {
    m.addLayer({ id: 'fb-zone-fill', type: 'fill', source: 'fb-human-zone', paint: { 'fill-color': HUMAN_ZONE_COLOR, 'fill-opacity': 0.14 } }, before)
    m.addLayer({ id: 'fb-zone-line', type: 'line', source: 'fb-human-zone', paint: { 'line-color': HUMAN_ZONE_COLOR, 'line-width': 1, 'line-opacity': 0.55, 'line-dasharray': [2, 2] } }, before)
  }
  zone.setData(r?.meta.human_zone ? { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: r.meta.human_zone, properties: {} }] } : EMPTY)
  const src = ensureSource(m, 'fb-corridors')
  if (!m.getLayer('fb-casing')) {
    m.addLayer({ id: 'fb-casing', type: 'line', source: 'fb-corridors', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#fff', 'line-width': ['interpolate', ['linear'], ['get', 'rank'], 1, 9, 15, 5] } })
    m.addLayer({ id: 'fb-line', type: 'line', source: 'fb-corridors', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': CORRIDOR_COLOR, 'line-width': ['interpolate', ['linear'], ['get', 'rank'], 1, 5.5, 15, 2.5] } })
    m.addLayer({
      id: 'fb-rank', type: 'symbol', source: 'fb-corridors',
      layout: { 'symbol-placement': 'line-center', 'text-field': ['to-string', ['get', 'rank']], 'text-font': labelFont(m), 'text-size': 12, 'text-allow-overlap': true },
      paint: { 'text-color': '#fff', 'text-halo-color': CORRIDOR_COLOR, 'text-halo-width': 2.2 },
    })
  }
  src.setData((r ?? EMPTY) as GeoJSON.FeatureCollection)
  void (m.getSource('fb-hazard') as ImageSource | undefined)
}

// --------------------------------------------------------------------------- fire stations
export function setFireStations(m: MLMap, stations: StationRef[] | null) {
  const src = ensureSource(m, 'fire-stations')
  if (!m.getLayer('fire-stations-dot')) {
    m.addLayer({ id: 'fire-stations-dot', type: 'circle', source: 'fire-stations', paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 3, 10, 6], 'circle-color': STATION_COLOR, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5 } })
    m.addLayer({
      id: 'fire-stations-label', type: 'symbol', source: 'fire-stations', minzoom: 8.5,
      layout: { 'text-field': ['coalesce', ['get', 'name'], 'Fire station'], 'text-font': labelFont(m), 'text-size': 11, 'text-offset': [0, 1.1], 'text-anchor': 'top', 'text-max-width': 9 },
      paint: { 'text-color': '#7a0f19', 'text-halo-color': '#fff', 'text-halo-width': 1.4 },
    })
  }
  src.setData({
    type: 'FeatureCollection',
    features: (stations ?? []).map((s) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [s.lon, s.lat] }, properties: { name: s.name, phone: s.phone ?? null, id: s.id } })),
  })
}
