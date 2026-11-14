const IST = 'Asia/Kolkata'

export function parseIso(s: string | null | undefined): Date | null {
  if (!s) return null
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? null : d
}

export function fmtUtc(s: string | null | undefined): string {
  const d = parseIso(s)
  if (!d) return '—'
  return `${d.toISOString().slice(0, 16).replace('T', ' ')} UTC`
}

export function fmtIst(s: string | null | undefined, withDate = true): string {
  const d = parseIso(s)
  if (!d) return '—'
  return (
    new Intl.DateTimeFormat('en-GB', {
      timeZone: IST,
      ...(withDate ? { day: '2-digit', month: 'short' } : {}),
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(d) + ' IST'
  )
}

export function fmtDate(s: string | null | undefined): string {
  if (!s) return '—'
  const d = new Date(`${s.slice(0, 10)}T12:00:00Z`)
  return new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' }).format(d)
}

export function timeAgo(s: string | null | undefined, now = Date.now()): string {
  const d = parseIso(s)
  if (!d) return 'never'
  const sec = Math.round((now - d.getTime()) / 1000)
  if (sec < 0) return 'in the future'
  if (sec < 60) return `${sec}s ago`
  const min = Math.round(sec / 60)
  if (min < 60) return `${min} min ago`
  const h = Math.round(min / 60)
  if (h < 48) return `${h} h ago`
  return `${Math.round(h / 24)} d ago`
}

export function num(v: number | null | undefined, digits = 1, unit = ''): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${v.toFixed(digits)}${unit ? ` ${unit}` : ''}`
}

export function fmtCoord(lat: number, lon: number, digits = 3): string {
  return `${Math.abs(lat).toFixed(digits)}°${lat >= 0 ? 'N' : 'S'}  ${Math.abs(lon).toFixed(digits)}°${lon >= 0 ? 'E' : 'W'}`
}

export function compass(deg: number | null | undefined): string {
  if (deg === null || deg === undefined) return '—'
  const dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
  return dirs[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16]
}

export function compactNumber(n: number): string {
  return new Intl.NumberFormat('en-IN', { notation: n >= 10000 ? 'compact' : 'standard' }).format(n)
}
