import type { Map as MLMap } from 'maplibre-gl'
import { useEffect } from 'react'
import { prefersReducedMotion } from '../lib/motion'
import type { WeatherCell } from '../lib/types'
import { buildField, sampleField } from '../lib/wind'

/** Pixels moved per frame per km/h of model wind (visual scale only). */
const PX_PER_KMH = 0.035
const MAX_PARTICLES = 2200
const FADE = 0.9

interface Particle {
  lon: number
  lat: number
  age: number
  life: number
}

/**
 * Illustrative wind particles drawn on a 2D canvas layered over the MapLibre
 * canvas. Positions are geographic and re-projected every frame, so they stay
 * registered to the map in 2D and 3D. Motion is interpolated between model
 * cell-centre winds; it is not an atmospheric simulation.
 */
export default function WindParticles({ map, cells, enabled }: { map: MLMap | null; cells: WeatherCell[] | undefined; enabled: boolean }) {
  useEffect(() => {
    if (!map || !enabled || !cells?.length || prefersReducedMotion()) return
    const field = buildField(cells)
    if (!field) return
    const host = map.getCanvasContainer()
    const canvas = document.createElement('canvas')
    canvas.setAttribute('aria-hidden', 'true')
    canvas.className = 'incendium-wind-particles'
    Object.assign(canvas.style, { position: 'absolute', inset: '0', width: '100%', height: '100%', pointerEvents: 'none' })
    host.appendChild(canvas)
    const ctx = canvas.getContext('2d')!
    const dpr = Math.min(window.devicePixelRatio || 1, 2)

    const resize = () => {
      const c = map.getCanvas()
      canvas.width = Math.round(c.clientWidth * dpr)
      canvas.height = Math.round(c.clientHeight * dpr)
    }
    resize()

    const [w, s, e, n] = field.bounds
    const spawn = (p?: Particle): Particle => {
      for (let tries = 0; tries < 20; tries++) {
        const lon = w + Math.random() * (e - w)
        const lat = s + Math.random() * (n - s)
        if (sampleField(field, lon, lat)) {
          const life = 50 + Math.random() * 90
          if (p) {
            p.lon = lon
            p.lat = lat
            p.age = 0
            p.life = life
            return p
          }
          return { lon, lat, age: Math.floor(Math.random() * life), life }
        }
      }
      return p ?? { lon: (w + e) / 2, lat: (s + n) / 2, age: 0, life: 60 }
    }
    const area = (e - w) * (n - s)
    const count = Math.min(MAX_PARTICLES, Math.round(area * 60))
    const particles: Particle[] = Array.from({ length: count }, () => spawn())

    let moving = false
    const onMoveStart = () => (moving = true)
    const onMoveEnd = () => {
      moving = false
      ctx.clearRect(0, 0, canvas.width, canvas.height)
    }
    map.on('movestart', onMoveStart)
    map.on('moveend', onMoveEnd)
    map.on('resize', resize)

    let raf = 0
    const frame = () => {
      raf = requestAnimationFrame(frame)
      if (document.hidden) return
      // Degrees of longitude/latitude per screen pixel near the map centre.
      const c = map.getCenter()
      const p0 = map.project(c)
      const p1 = map.project([c.lng + 0.1, c.lat])
      const p2 = map.project([c.lng, c.lat + 0.1])
      const degPerPxLon = 0.1 / Math.max(Math.hypot(p1.x - p0.x, p1.y - p0.y), 1e-6)
      const degPerPxLat = 0.1 / Math.max(Math.hypot(p2.x - p0.x, p2.y - p0.y), 1e-6)

      if (moving) ctx.clearRect(0, 0, canvas.width, canvas.height)
      else {
        ctx.globalCompositeOperation = 'destination-in'
        ctx.fillStyle = `rgba(0,0,0,${FADE})`
        ctx.fillRect(0, 0, canvas.width, canvas.height)
      }
      ctx.globalCompositeOperation = 'source-over'
      ctx.lineWidth = 1.15 * dpr
      ctx.lineCap = 'round'
      ctx.strokeStyle = 'rgba(30, 44, 47, 0.62)'
      ctx.beginPath()
      for (const p of particles) {
        const uv = sampleField(field, p.lon, p.lat)
        if (!uv || p.age++ > p.life) {
          spawn(p)
          continue
        }
        const a = map.project([p.lon, p.lat])
        p.lon += uv[0] * PX_PER_KMH * degPerPxLon
        p.lat += uv[1] * PX_PER_KMH * degPerPxLat
        const b = map.project([p.lon, p.lat])
        ctx.moveTo(a.x * dpr, a.y * dpr)
        ctx.lineTo(b.x * dpr, b.y * dpr)
      }
      ctx.stroke()
    }
    raf = requestAnimationFrame(frame)

    return () => {
      cancelAnimationFrame(raf)
      map.off('movestart', onMoveStart)
      map.off('moveend', onMoveEnd)
      map.off('resize', resize)
      canvas.remove()
    }
  }, [map, cells, enabled])

  return null
}
