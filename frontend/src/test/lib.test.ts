import { describe, expect, it } from 'vitest'
import { compass, fmtCoord, fmtIst, fmtUtc, num, timeAgo } from '../lib/format'
import { classColorExpression, compositeClassFor, COMPOSITE_CLASSES, FWI_CLASSES, fwiClassInfo, NO_DATA_COLOR, textOn } from '../lib/risk'

describe('format', () => {
  it('formats UTC and IST without shifting the instant', () => {
    expect(fmtUtc('2026-11-13T09:30:00Z')).toBe('2026-11-13 09:30 UTC')
    expect(fmtIst('2026-11-13T09:30:00Z')).toContain('15:00')
    expect(fmtUtc(null)).toBe('—')
  })
  it('renders missing numbers as an em dash, never as zero', () => {
    expect(num(null)).toBe('—')
    expect(num(undefined)).toBe('—')
    expect(num(Number.NaN)).toBe('—')
    expect(num(0)).toBe('0.0')
    expect(num(12.345, 2, 'mm')).toBe('12.35 mm')
  })
  it('formats coordinates with hemisphere', () => {
    expect(fmtCoord(12.97, 77.59, 2)).toBe('12.97°N  77.59°E')
    expect(fmtCoord(-1.5, -70.25, 1)).toBe('1.5°S  70.3°W')
  })
  it('converts wind direction to compass points', () => {
    expect(compass(0)).toBe('N')
    expect(compass(90)).toBe('E')
    expect(compass(225)).toBe('SW')
    expect(compass(359)).toBe('N')
    expect(compass(null)).toBe('—')
  })
  it('describes relative time', () => {
    const now = Date.parse('2026-11-13T12:00:00Z')
    expect(timeAgo('2026-11-13T11:59:30Z', now)).toBe('30s ago')
    expect(timeAgo('2026-11-13T11:15:00Z', now)).toBe('45 min ago')
    expect(timeAgo('2026-11-13T11:00:00Z', now)).toBe('1 h ago')
    expect(timeAgo('2026-11-13T06:00:00Z', now)).toBe('6 h ago')
    expect(timeAgo(null, now)).toBe('never')
  })
})

describe('risk classes', () => {
  it('has strictly increasing thresholds matching the backend', () => {
    expect(FWI_CLASSES.map((c) => c.min)).toEqual([0, 11.2, 21.3, 38, 50, 70])
    expect(COMPOSITE_CLASSES.map((c) => c.min)).toEqual([0, 25, 50, 75])
  })
  it('maps composite scores to classes at the boundaries', () => {
    expect(compositeClassFor(null)).toBeNull()
    expect(compositeClassFor(24.9)).toBe('Low')
    expect(compositeClassFor(25)).toBe('Moderate')
    expect(compositeClassFor(74.9)).toBe('High')
    expect(compositeClassFor(75)).toBe('Very High')
  })
  it('gives each class a level for non-colour encoding', () => {
    expect(fwiClassInfo('Extreme')?.level).toBe(5)
    expect(fwiClassInfo('nonsense')).toBeUndefined()
  })
  it('falls back to a neutral no-data colour in map expressions', () => {
    const expr = classColorExpression('fwi_class', FWI_CLASSES)
    expect(expr[expr.length - 1]).toBe(NO_DATA_COLOR)
  })
  it('chooses legible text colour on fills', () => {
    expect(textOn('#461214')).toBe('#f3f1ea')
    expect(textOn('#e8e5db')).toBe('#1c2426')
  })
})
