import { useApi } from './api'
import { FWI_CLASSES } from './risk'

export type FwiClassDef = (typeof FWI_CLASSES)[number]

export interface FwiCalibration {
  thresholds: number[]
  percentiles: number[]
  method: string
  source: { start: string; end: string; cell_days: number; cells: number; weather: string }
  generated_at: string
}

export interface RegionalFwiClasses {
  scheme: 'calibrated' | 'effis_prototype'
  classes: { name: string; min: number; max: number | null }[]
  calibration: FwiCalibration | null
}

interface MethodologyFwi {
  fwi: { regional_classes?: Record<string, RegionalFwiClasses> }
}

/** Apply server-side thresholds to the fixed class colours (colour follows the class name, never its threshold). */
export function withThresholds(defs: { name: string; min: number }[] | undefined): FwiClassDef[] {
  if (!defs || defs.length !== FWI_CLASSES.length) return FWI_CLASSES
  return FWI_CLASSES.map((c, i) => (defs[i]?.name === c.name ? { ...c, min: round(defs[i].min) } : c))
}

function round(x: number) {
  return Math.round(x * 10) / 10
}

export interface FwiScheme {
  classes: FwiClassDef[]
  calibrated: boolean
  calibration: FwiCalibration | null
  /** Short label, e.g. for a legend eyebrow. */
  short: string
  /** One-sentence description of where the thresholds come from. */
  note: string
}

export function describeScheme(r: RegionalFwiClasses | undefined, regionName = 'the region'): FwiScheme {
  if (r?.scheme === 'calibrated' && r.calibration) {
    const cal = r.calibration
    const season = cal.source.start.slice(0, 4)
    return {
      classes: withThresholds(r.classes),
      calibrated: true,
      calibration: cal,
      short: `calibrated · ${season} season`,
      note: `Thresholds are percentiles (${cal.percentiles.map((p) => `P${p}`).join(', ')}) of daily FWI across ${regionName}'s Feb–May ${season} fire season. Classes are relative to that season, not absolute danger levels.`,
    }
  }
  return {
    classes: FWI_CLASSES,
    calibrated: false,
    calibration: null,
    short: 'prototype classes',
    note: 'EFFIS (Europe) thresholds, not calibrated for India.',
  }
}

/** FWI class scheme in use for a region (calibrated thresholds if the server has them). */
export function useFwiClasses(region: string | null | undefined): FwiScheme {
  const { data } = useApi<MethodologyFwi>('/api/methodology')
  const name = region ? region.charAt(0).toUpperCase() + region.slice(1) : undefined
  return describeScheme(region ? data?.fwi.regional_classes?.[region] : undefined, name)
}
