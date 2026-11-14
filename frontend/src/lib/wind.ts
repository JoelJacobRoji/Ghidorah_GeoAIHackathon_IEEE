/**
 * Wind field utilities for the illustrative particle layer.
 *
 * Input: model 10 m wind (speed km/h, direction in degrees the wind blows FROM,
 * meteorological convention) at 0.25° cell centres. Output: (u, v) components,
 * eastward/northward, in km/h, bilinearly interpolated between centres.
 * This is a visual interpolation, not an atmospheric simulation.
 */

export type UV = [number, number]

export interface WindField {
  step: number
  values: Map<string, UV>
  bounds: [number, number, number, number] // west, south, east, north of the covered cells
  maxSpeed: number
}

/** Meteorological (speed, from-direction) → (u east, v north). Wind FROM north (0°) blows TO the south: v < 0. */
export function uvFromWind(speed: number, fromDeg: number): UV {
  const r = (fromDeg * Math.PI) / 180
  return [-speed * Math.sin(r), -speed * Math.cos(r)]
}

const key = (i: number, j: number) => `${i},${j}`

/** Build a field from cell-centre samples. Centres are assumed to lie at (k + 0.5) × step. */
export function buildField(cells: { lat: number; lon: number; ws10: number | null; wd10: number | null }[], step = 0.25): WindField | null {
  const values = new Map<string, UV>()
  let w = Infinity
  let s = Infinity
  let e = -Infinity
  let n = -Infinity
  let maxSpeed = 0
  for (const c of cells) {
    if (c.ws10 === null || c.wd10 === null || !Number.isFinite(c.ws10) || !Number.isFinite(c.wd10)) continue
    const i = Math.round(c.lon / step - 0.5)
    const j = Math.round(c.lat / step - 0.5)
    values.set(key(i, j), uvFromWind(c.ws10, c.wd10))
    w = Math.min(w, c.lon - step / 2)
    e = Math.max(e, c.lon + step / 2)
    s = Math.min(s, c.lat - step / 2)
    n = Math.max(n, c.lat + step / 2)
    maxSpeed = Math.max(maxSpeed, c.ws10)
  }
  if (!values.size) return null
  return { step, values, bounds: [w, s, e, n], maxSpeed }
}

/**
 * Bilinear interpolation between the four surrounding cell centres. Missing
 * neighbours are dropped and the remaining weights renormalised; returns null
 * when less than a quarter of the interpolation weight is backed by data
 * (i.e. the point lies outside the sampled area).
 */
export function sampleField(f: WindField, lon: number, lat: number): UV | null {
  const x = lon / f.step - 0.5
  const y = lat / f.step - 0.5
  const i0 = Math.floor(x)
  const j0 = Math.floor(y)
  const fx = x - i0
  const fy = y - j0
  let u = 0
  let v = 0
  let wsum = 0
  const corners: [number, number, number][] = [
    [i0, j0, (1 - fx) * (1 - fy)],
    [i0 + 1, j0, fx * (1 - fy)],
    [i0, j0 + 1, (1 - fx) * fy],
    [i0 + 1, j0 + 1, fx * fy],
  ]
  for (const [i, j, wt] of corners) {
    const val = f.values.get(key(i, j))
    if (!val || wt === 0) continue
    u += val[0] * wt
    v += val[1] * wt
    wsum += wt
  }
  if (wsum < 0.25) return null
  return [u / wsum, v / wsum]
}
