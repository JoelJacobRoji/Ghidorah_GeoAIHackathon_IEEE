import { describe, expect, it } from 'vitest'
import { buildField, sampleField, uvFromWind } from '../lib/wind'

const close = (a: number, b: number, eps = 1e-9) => Math.abs(a - b) < eps

describe('uvFromWind (meteorological convention: direction wind blows FROM)', () => {
  it('northerly wind blows southward', () => {
    const [u, v] = uvFromWind(10, 0)
    expect(close(u, 0, 1e-9)).toBe(true)
    expect(v).toBeCloseTo(-10)
  })
  it('westerly wind blows eastward', () => {
    const [u, v] = uvFromWind(10, 270)
    expect(u).toBeCloseTo(10)
    expect(Math.abs(v)).toBeLessThan(1e-9)
  })
})

describe('wind field interpolation', () => {
  // four 0.25° cells: centres at 12.125/12.375 N × 76.125/76.375 E (synthetic test values)
  const cells = [
    { lat: 12.125, lon: 76.125, ws10: 10, wd10: 270 },
    { lat: 12.125, lon: 76.375, ws10: 20, wd10: 270 },
    { lat: 12.375, lon: 76.125, ws10: 10, wd10: 270 },
    { lat: 12.375, lon: 76.375, ws10: 20, wd10: 270 },
  ]
  const f = buildField(cells)!

  it('reproduces values exactly at cell centres', () => {
    expect(sampleField(f, 76.125, 12.125)![0]).toBeCloseTo(10)
    expect(sampleField(f, 76.375, 12.375)![0]).toBeCloseTo(20)
  })
  it('interpolates linearly between centres', () => {
    expect(sampleField(f, 76.25, 12.25)![0]).toBeCloseTo(15)
    expect(sampleField(f, 76.1875, 12.2)![0]).toBeCloseTo(12.5)
  })
  it('reports bounds and max speed', () => {
    expect(f.bounds).toEqual([76, 12, 76.5, 12.5])
    expect(f.maxSpeed).toBe(20)
  })
  it('returns null far outside the sampled cells', () => {
    expect(sampleField(f, 80, 20)).toBeNull()
  })
  it('ignores missing samples', () => {
    expect(buildField([{ lat: 12.125, lon: 76.125, ws10: null, wd10: 10 }])).toBeNull()
  })
})
