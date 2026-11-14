import { describe, expect, it } from 'vitest'
import { describeScheme, withThresholds, type RegionalFwiClasses } from '../lib/fwiClasses'
import { FWI_CLASSES } from '../lib/risk'

const calibrated: RegionalFwiClasses = {
  scheme: 'calibrated',
  classes: [
    { name: 'Low', min: 0, max: 18.04 },
    { name: 'Moderate', min: 18.04, max: 27.5 },
    { name: 'High', min: 27.5, max: 36.12 },
    { name: 'Very High', min: 36.12, max: 44 },
    { name: 'Extreme', min: 44, max: 51.66 },
    { name: 'Very Extreme', min: 51.66, max: null },
  ],
  calibration: {
    thresholds: [18.04, 27.5, 36.12, 44, 51.66],
    percentiles: [25, 50, 75, 90, 97],
    method: 'test',
    source: { start: '2025-02-01', end: '2025-05-31', cell_days: 33840, cells: 282, weather: 'ERA5' },
    generated_at: '2026-11-14T00:00:00Z',
  },
}

describe('FWI class schemes', () => {
  it('keeps colours by class name and applies server thresholds', () => {
    const c = withThresholds(calibrated.classes)
    expect(c.map((x) => x.min)).toEqual([0, 18, 27.5, 36.1, 44, 51.7])
    expect(c.map((x) => x.color)).toEqual(FWI_CLASSES.map((x) => x.color))
  })

  it('falls back to EFFIS when the server has no calibration or sends a malformed one', () => {
    expect(withThresholds(undefined)).toBe(FWI_CLASSES)
    expect(withThresholds(calibrated.classes.slice(0, 3))).toBe(FWI_CLASSES)
    const s = describeScheme(undefined)
    expect(s.calibrated).toBe(false)
    expect(s.note).toMatch(/not calibrated for India/)
  })

  it('describes a calibrated scheme with its reference season', () => {
    const s = describeScheme(calibrated, 'Karnataka')
    expect(s.calibrated).toBe(true)
    expect(s.short).toBe('calibrated · 2025 season')
    expect(s.note).toMatch(/P25, P50, P75, P90, P97/)
    expect(s.note).toMatch(/Karnataka's Feb–May 2025/)
  })
})
