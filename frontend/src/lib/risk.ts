import type { CompositeClass, FwiClass } from './types'

// Green (low) -> dark red (extreme) ordinal ramps, generated in OKLCH with strictly
// falling lightness (adjacent ΔL ≥ 0.06), so steps stay distinguishable by brightness
// under colour-vision deficiency, and the light end clears 2:1 on the panel surface.
// They deliberately span several hues (validator "single hue" check waived by design
// request); every class is also named in legends, badges and tooltips.
export const FWI_CLASSES: { name: FwiClass; min: number; color: string; level: number }[] = [
  { name: 'Low', min: 0, color: '#49a550', level: 1 },
  { name: 'Moderate', min: 11.2, color: '#857c00', level: 2 },
  { name: 'High', min: 21.3, color: '#975000', level: 3 },
  { name: 'Very High', min: 38, color: '#952500', level: 4 },
  { name: 'Extreme', min: 50, color: '#7c0206', level: 5 },
  { name: 'Very Extreme', min: 70, color: '#550513', level: 6 },
]

export const COMPOSITE_CLASSES: { name: CompositeClass; min: number; color: string; level: number }[] = [
  { name: 'Low', min: 0, color: '#49a550', level: 1 },
  { name: 'Moderate', min: 25, color: '#9a5600', level: 2 },
  { name: 'High', min: 50, color: '#8a0700', level: 3 },
  { name: 'Very High', min: 75, color: '#550513', level: 4 },
]

export const NO_DATA_COLOR = '#7d8a8a'
/** ML detection-likelihood stops (probability -> colour), same green -> dark red ramp as the FWI classes. */
export const ML_STOPS: [number, string][] = [[0.05, '#49a550'], [0.15, '#857c00'], [0.3, '#975000'], [0.45, '#952500'], [0.6, '#7c0206'], [0.75, '#550513']]
/** NDMI stops at -0.2, 0, 0.15, 0.3, 0.45 (validated single-hue sequential ramp). */
export const NDMI_RAMP = ['#5f8d68', '#3c7753', '#1a6243', '#044c34', '#003625'] as const
export const NDMI_STOPS = [-0.2, 0, 0.15, 0.3, 0.45] as const
export const DETECTION_COLOR = '#e77943'

export function fwiClassInfo(name: string | null | undefined) {
  return FWI_CLASSES.find((c) => c.name === name)
}

export function compositeClassInfo(name: string | null | undefined) {
  return COMPOSITE_CLASSES.find((c) => c.name === name)
}

export function textOn(hex: string): string {
  // Pick the text colour with the higher WCAG contrast against the fill.
  const lum = (h: string) => {
    const n = parseInt(h.slice(1), 16)
    const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
      const c = v / 255
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
    })
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
  }
  const dark = '#1c2426'
  const light = '#f3f1ea'
  const L = lum(hex)
  const cDark = (L + 0.05) / (lum(dark) + 0.05)
  const cLight = (lum(light) + 0.05) / (L + 0.05)
  return cDark >= cLight ? dark : light
}

/** MapLibre expression: colour by class name with a neutral no-data colour. */
export function classColorExpression(prop: string, classes: { name: string; color: string }[]) {
  return ['match', ['coalesce', ['get', prop], '__none__'], ...classes.flatMap((c) => [c.name, c.color]), NO_DATA_COLOR]
}

export const FACTOR_META: Record<string, { label: string; short: string }> = {
  T: { label: 'Temperature', short: 'Temp' },
  H: { label: 'Low humidity', short: 'Humidity' },
  W: { label: 'Wind', short: 'Wind' },
  D: { label: 'Dryness (days since rain)', short: 'Dryness' },
  V: { label: 'Vegetation moisture', short: 'Vegetation' },
}

// Categorical factor palette in fixed order T,H,W,D,V — validated: lightness band,
// chroma ≥ 0.10, adjacent CVD ΔE ≥ 10, normal-vision ΔE ≥ 15. Wind (#b8860b) is
// below 3:1 on the panel surface, so every factor segment is direct-labelled.
export const FACTOR_COLORS: Record<string, string> = {
  T: '#c95a2a',
  H: '#3a6db5',
  W: '#b8860b',
  D: '#1d8a72',
  V: '#7a4fb0',
}

export function compositeClassFor(score: number | null): CompositeClass | null {
  if (score === null) return null
  for (let i = COMPOSITE_CLASSES.length - 1; i >= 0; i--) if (score >= COMPOSITE_CLASSES[i].min) return COMPOSITE_CLASSES[i].name
  return 'Low'
}
