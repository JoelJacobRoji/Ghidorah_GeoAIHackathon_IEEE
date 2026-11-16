import type { DataDrivenPropertyValueSpecification, Map as MLMap, StyleSpecification } from 'maplibre-gl'

export const BASEMAP_STYLE_URL = 'https://tiles.openfreemap.org/styles/positron'
export const TERRAIN_TILEJSON = 'https://tiles.mapterhorn.com/tilejson.json'

export const BASEMAP_ATTRIBUTION =
  '<a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a> <a href="https://www.openmaptiles.org/" target="_blank" rel="noopener">© OpenMapTiles</a> Data from <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'

/** Palette for the restyled basemap: mid-tone, low-chroma, editorial. */
const P = {
  land: '#c9c4b5',
  landShade: '#bfb9a8',
  water: '#73898d',
  waterLine: '#6a8085',
  wood: '#a9b39c',
  park: '#b3bba3',
  residential: '#c0b9a8',
  roadMinor: '#d9d4c7',
  roadMajor: '#e4e0d4',
  roadCasing: '#aea795',
  rail: '#a39d8c',
  boundary: '#56666a',
  label: '#2b3639',
  labelSoft: '#4c5b5e',
  halo: '#d9d4c7',
  building: '#b7b09f',
}

/**
 * Fetch the OpenFreeMap Positron style and rewrite its colours to the Incendium
 * palette. Layer structure (and therefore data/attribution) is unchanged.
 */
export async function loadIncendiumStyle(signal?: AbortSignal): Promise<StyleSpecification> {
  const res = await fetch(BASEMAP_STYLE_URL, { signal })
  if (!res.ok) throw new Error(`Basemap style unavailable (${res.status})`)
  const style = (await res.json()) as StyleSpecification
  for (const layer of style.layers) {
    const id = layer.id
    const paint = ((layer as { paint?: Record<string, unknown> }).paint ??= {})
    const layout = ((layer as { layout?: Record<string, unknown> }).layout ??= {})
    if (layer.type === 'background') paint['background-color'] = P.land
    else if (id === 'water') paint['fill-color'] = P.water
    else if (id === 'waterway') paint['line-color'] = P.waterLine
    else if (id === 'park') {
      paint['fill-color'] = P.park
      paint['fill-opacity'] = 0.55
    } else if (id.startsWith('landcover_wood')) {
      paint['fill-color'] = P.wood
      paint['fill-opacity'] = 0.55
    } else if (id.startsWith('landcover_')) paint['fill-color'] = '#d6d2c6'
    else if (id === 'landuse_residential') {
      paint['fill-color'] = P.residential
      paint['fill-opacity'] = 0.5
    } else if (id === 'building') {
      paint['fill-color'] = P.building
      paint['fill-opacity'] = 0.6
    } else if (layer.type === 'line' && id.includes('casing')) paint['line-color'] = P.roadCasing
    else if (layer.type === 'line' && (id.includes('motorway') || id.includes('major'))) paint['line-color'] = P.roadMajor
    else if (layer.type === 'line' && (id.startsWith('highway') || id.startsWith('road') || id.startsWith('aeroway'))) paint['line-color'] = P.roadMinor
    else if (layer.type === 'line' && id.startsWith('railway')) paint['line-color'] = P.rail
    else if (layer.type === 'line' && id.startsWith('boundary')) {
      paint['line-color'] = P.boundary
      paint['line-opacity'] = 0.55
    } else if (layer.type === 'symbol') {
      if (layout['text-field'] !== undefined) {
        paint['text-color'] = id.startsWith('water') ? '#e2e6e2' : id.includes('country') || id.includes('state') ? P.labelSoft : P.label
        paint['text-halo-color'] = id.startsWith('water') ? P.water : P.halo
        paint['text-halo-width'] = 1.2
      }
    }
  }
  return style
}

export type Basemap = 'map' | 'satellite'

/** EOX Sentinel-2 cloudless 2024 mosaic (CC BY-NC-SA 4.0, attribution required). */
export const SATELLITE_TILES = 'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg'
export const SATELLITE_ATTRIBUTION =
  '<a href="https://s2maps.eu" target="_blank" rel="noopener">Sentinel-2 cloudless 2024</a> by EOX IT Services GmbH (contains modified Copernicus Sentinel data 2024)'

/**
 * Add the satellite imagery layer (hidden) above the basemap's area fills and below
 * its roads, boundaries and labels, so every overlay still draws on top of it.
 */
export function addSatelliteLayer(map: MLMap) {
  if (map.getLayer('satellite')) return
  map.addSource('satellite', {
    type: 'raster',
    tiles: [SATELLITE_TILES],
    tileSize: 256,
    maxzoom: 17,
    attribution: SATELLITE_ATTRIBUTION,
  })
  const before = map.getStyle().layers.find((l) => l.type === 'line' || l.type === 'symbol')?.id
  map.addLayer({ id: 'satellite', type: 'raster', source: 'satellite', layout: { visibility: 'none' }, paint: { 'raster-fade-duration': 200 } }, before)
}

type LabelPaint = { color: DataDrivenPropertyValueSpecification<string> | undefined; halo: DataDrivenPropertyValueSpecification<string> | undefined }
const labelOriginals = new WeakMap<MLMap, Map<string, LabelPaint>>()

/** Switch between the editorial vector basemap and satellite imagery. */
export function setBasemap(map: MLMap, basemap: Basemap) {
  const sat = basemap === 'satellite'
  if (map.getLayer('satellite')) map.setLayoutProperty('satellite', 'visibility', sat ? 'visible' : 'none')
  if (map.getLayer('hillshade')) map.setLayoutProperty('hillshade', 'visibility', sat ? 'none' : 'visible')
  let originals = labelOriginals.get(map)
  if (!originals) {
    originals = new Map()
    labelOriginals.set(map, originals)
  }
  for (const layer of map.getStyle().layers) {
    if (layer.type !== 'symbol' || !(layer.layout as Record<string, unknown> | undefined)?.['text-field']) continue
    // Only basemap labels: our own symbol layers keep their styling.
    if (layer.source !== 'openmaptiles') continue
    if (!originals.has(layer.id)) {
      originals.set(layer.id, { color: map.getPaintProperty(layer.id, 'text-color'), halo: map.getPaintProperty(layer.id, 'text-halo-color') })
    }
    const o = originals.get(layer.id)!
    map.setPaintProperty(layer.id, 'text-color', sat ? '#f4f1ea' : o.color)
    map.setPaintProperty(layer.id, 'text-halo-color', sat ? 'rgba(20,24,26,0.85)' : o.halo)
  }
}

/** Add DEM sources (terrain + hillshade) using Mapterhorn terrarium tiles. */
export function addTerrainSources(map: MLMap) {
  if (!map.getSource('dem')) {
    map.addSource('dem', { type: 'raster-dem', url: TERRAIN_TILEJSON, encoding: 'terrarium', tileSize: 512 })
  }
  if (!map.getSource('dem-hillshade')) {
    // A second source instance avoids terrain/hillshade sharing one source (recommended by MapLibre).
    map.addSource('dem-hillshade', { type: 'raster-dem', url: TERRAIN_TILEJSON, encoding: 'terrarium', tileSize: 512 })
  }
  if (!map.getLayer('hillshade')) {
    const before = map.getStyle().layers.find((l) => l.id === 'water')?.id
    map.addLayer(
      {
        id: 'hillshade',
        type: 'hillshade',
        source: 'dem-hillshade',
        paint: {
          'hillshade-shadow-color': '#3a4649',
          'hillshade-highlight-color': '#efece3',
          'hillshade-accent-color': '#55645f',
          'hillshade-exaggeration': 0.45,
        },
      },
      before,
    )
  }
}

export function setTerrain(map: MLMap, on: boolean, exaggeration = 1.4) {
  if (on) map.setTerrain({ source: 'dem', exaggeration })
  else map.setTerrain(null)
}

export function applySky(map: MLMap) {
  try {
    map.setSky({
      'sky-color': '#8ea1a1',
      'horizon-color': '#d4cfc2',
      'fog-color': '#c9c4b5',
      'sky-horizon-blend': 0.6,
      'horizon-fog-blend': 0.6,
      'fog-ground-blend': 0.35,
      'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 0.8, 8, 0.3, 12, 0],
    })
  } catch {
    /* sky unsupported: purely aesthetic */
  }
}

/** Arrow glyph for wind direction, drawn once into an ImageData (no external sprite). */
export function windArrowImage(size = 48): ImageData {
  const c = document.createElement('canvas')
  c.width = c.height = size
  const g = c.getContext('2d')!
  g.translate(size / 2, size / 2)
  g.strokeStyle = '#26383b'
  g.fillStyle = '#26383b'
  g.lineWidth = size * 0.07
  g.lineCap = 'round'
  // Arrow points "up" = toward north; rotate by (wind_from + 180) to show where air moves to.
  g.beginPath()
  g.moveTo(0, size * 0.32)
  g.lineTo(0, -size * 0.2)
  g.stroke()
  g.beginPath()
  g.moveTo(0, -size * 0.36)
  g.lineTo(size * 0.13, -size * 0.12)
  g.lineTo(-size * 0.13, -size * 0.12)
  g.closePath()
  g.fill()
  return g.getImageData(0, 0, size, size)
}
