import { ArrowRight, BellRing, CloudSun, Flame, Leaf, Route, Satellite } from 'lucide-react'
import { motion } from 'motion/react'
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { DailyDetections } from '../components/charts'
import { NavBar } from '../components/NavBar'
import { SourceStatus } from '../components/SourceStatus'
import { ErrorState, LoadingBlock } from '../components/ui'
import { useApi } from '../lib/api'
import { fmtIst, timeAgo } from '../lib/format'
import { fadeUp, hasWebGL, MOTION, stagger, useReducedMotion } from '../lib/motion'
import type { HotspotsResponse, SummaryResponse } from '../lib/types'

const Globe = lazy(() => import('../components/Globe'))

const FOCI = [
  { lat: 16, lon: 70, distance: 4.6, key: 0 }, // idle: India region in view, slowly rotating
  { lat: 22, lon: 80, distance: 3.1, key: 1 }, // India
  { lat: 15, lon: 76.5, distance: 2.2, key: 2 }, // Karnataka
  { lat: 14, lon: 76.2, distance: 1.85, key: 3 },
]

export default function Landing() {
  const summary = useApi<SummaryResponse>('/api/summary', { refreshMs: 5 * 60_000 })
  const hotspots = useApi<HotspotsResponse>('/api/hotspots?hours=24')
  const reduce = useReducedMotion()
  const [section, setSection] = useState(0)
  const refs = useRef<(HTMLElement | null)[]>([])
  const webgl = useMemo(() => hasWebGL(), [])

  useEffect(() => {
    const io = new IntersectionObserver(
      (entries) => {
        const vis = entries.filter((e) => e.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0]
        if (vis) setSection(Number((vis.target as HTMLElement).dataset.section))
      },
      { threshold: [0.35, 0.6] },
    )
    refs.current.forEach((el) => el && io.observe(el))
    return () => io.disconnect()
  }, [])

  const points = useMemo<[number, number][]>(
    () => (hotspots.data?.features ?? []).slice(0, 8000).map((f) => [f.geometry.coordinates[1], f.geometry.coordinates[0]]),
    [hotspots.data],
  )
  const det = summary.data?.detections
  const focus = FOCI[Math.min(section, FOCI.length - 1)]

  return (
    <div className="grain relative min-h-dvh bg-[radial-gradient(120%_80%_at_70%_20%,#3a4a4e_0%,#2b3639_45%,#1f282a_100%)] text-ivory">
      {/* Globe stays fixed while the narrative scrolls over it. */}
      <div className="pointer-events-none fixed inset-0 z-0 md:left-[30%]">
        {webgl ? (
          <Suspense fallback={null}>
            <Globe points={points} focus={focus} className="pointer-events-auto h-full w-full" />
          </Suspense>
        ) : (
          <div className="grid h-full place-items-center text-sm text-ivory/45">3D globe needs WebGL, which is unavailable here.</div>
        )}
      </div>
      <div className="pointer-events-none fixed inset-0 z-0 bg-[#1f282a]/60 bg-gradient-to-r from-[#1f282a]/85 via-[#1f282a]/35 to-transparent md:bg-transparent md:via-transparent" />

      <div className="relative z-10">
        <div className="p-3 md:p-4">
          <NavBar variant="overlay" right={<SourceStatus dark />} />
        </div>

        {/* ---------------------------------------------------------- hero */}
        <section ref={(el) => { refs.current[0] = el }} data-section={0} className="mx-auto flex min-h-[88dvh] max-w-[1400px] items-center px-5 md:px-10">
          <motion.div variants={stagger} initial="hidden" animate="show" className="max-w-[560px]">
            <motion.div variants={fadeUp} className="eyebrow text-sage-300">Early wildfire awareness · India</motion.div>
            <motion.h1 variants={fadeUp} className="font-display mt-4 text-[clamp(44px,7vw,92px)] font-bold leading-[0.95] tracking-[-0.035em]">
              Incendium
            </motion.h1>
            <motion.p variants={fadeUp} className="font-display mt-4 text-[clamp(20px,2.3vw,30px)] font-medium leading-tight text-ivory/90">
              Satellite heat detections, fire-weather danger and vegetation condition, in one honest picture.
            </motion.p>
            <motion.p variants={fadeUp} className="mt-4 max-w-[520px] text-[15px] leading-relaxed text-ivory/65">
              Incendium combines NASA VIIRS thermal-anomaly detections, Open-Meteo model weather and Copernicus Sentinel-2 vegetation indices. It computes the Canadian Fire Weather Index on a 0.25° grid and explains every number.
            </motion.p>

            <motion.div variants={fadeUp} className="mt-7 min-h-[64px]" aria-live="polite">
              {summary.error && !summary.data ? (
                <ErrorState error={summary.error} onRetry={summary.refresh} dark title="Live data unavailable" />
              ) : det ? (
                <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
                  <div>
                    <div className="font-display text-4xl font-semibold tabular">{det.last_24h.toLocaleString('en-IN')}</div>
                    <div className="mt-1 text-[12.5px] text-ivory/60">thermal anomalies detected in the last 24 h<br />over the India monitoring window (VIIRS NOAA-20/21)</div>
                  </div>
                  <div className="text-[12.5px] text-ivory/60">
                    Latest overpass in data: <span className="font-mono text-ivory/85">{fmtIst(det.latest_acquisition)}</span>
                    <br />({timeAgo(det.latest_acquisition)}). Detections are not confirmed fires.
                  </div>
                </div>
              ) : (
                <LoadingBlock label="Loading live detections…" dark />
              )}
            </motion.div>

            <motion.div variants={fadeUp} className="mt-8 flex flex-wrap items-center gap-3">
              <Link
                to="/dashboard"
                className="group inline-flex items-center gap-2 rounded-2xl bg-incendium px-5 py-3 text-[15px] font-semibold text-graphite-950 shadow-[0_10px_30px_-10px_rgba(231,121,67,0.7)] transition-all hover:bg-incendium-300"
              >
                Explore Wildfire Intelligence
                <ArrowRight className={`size-4 ${reduce ? '' : 'transition-transform group-hover:translate-x-0.5'}`} aria-hidden />
              </Link>
              <Link to="/methodology" className="rounded-2xl px-4 py-3 text-sm font-medium text-ivory/75 hover:bg-ivory/8 hover:text-ivory">
                How it works
              </Link>
            </motion.div>
          </motion.div>
        </section>

        {/* ---------------------------------------------------------- narrative */}
        <Chapter
          index={1}
          refFn={(el) => (refs.current[1] = el)}
          icon={<Satellite className="size-4" />}
          kicker="01 · Detect"
          title="Polar-orbiting satellites flag heat, several times a day."
          body="VIIRS on NOAA-20 and NOAA-21 resolves thermal anomalies at about 375 m. NASA FIRMS publishes them within about three hours of the overpass. Incendium keeps each detection’s satellite, UTC acquisition time, confidence and fire radiative power. It never merges detections into “incidents” and never calls them confirmed fires."
        >
          {det && det.daily_utc.length > 0 ? (
            <div className="mt-6 max-w-md">
              <div className="eyebrow mb-2 text-ivory/50">Detections per day (UTC), India window</div>
              <DailyDetections data={det.daily_utc} dark />
            </div>
          ) : null}
        </Chapter>
        <Chapter
          index={2}
          refFn={(el) => (refs.current[2] = el)}
          icon={<CloudSun className="size-4" />}
          kicker="02 · Measure"
          title="Fire weather, computed daily on a 28 km grid."
          body="Noon temperature, humidity and wind, plus 24-hour rainfall, drive the Canadian Fire Weather Index: fine-fuel dryness (FFMC), deeper drought (DMC, DC), spread potential (ISI) and available fuel (BUI). India’s Forest Survey reports building an FWI-based danger system on the same foundation. Incendium’s thresholds are labelled as prototype classes."
        />
        <Chapter
          index={3}
          refFn={(el) => (refs.current[3] = el)}
          icon={<Leaf className="size-4" />}
          kicker="03 · Understand"
          title="Vegetation adds context. Every score shows its working."
          body="Sentinel-2 provides cloud-masked NDVI (greenness) and NDMI (a water-content proxy) for every cell. An experimental composite shows how temperature, humidity, wind, days since rain and vegetation each contribute. Missing inputs are reported, never filled in."
        />
        <Chapter
          index={4}
          refFn={(el) => (refs.current[4] = el)}
          icon={<Route className="size-4" />}
          kicker="04 · Act"
          title="From awareness to prevention and response."
          body="Five connected modules: wildfire prediction (fire-weather indices plus an experimental model tested on a season it never saw), geospatial monitoring, firebreak recommendation, fire spread simulation and rule-based alerts listing the nearest fire stations. Every output is decision support with its assumptions shown, never an official warning."
        >
          <ul className="mt-5 grid gap-2 text-[13.5px] text-ivory/80">
            <li className="flex items-center gap-2"><Flame className="size-4 text-incendium-300" aria-hidden /> Spread scenarios hour by hour from any ignition point</li>
            <li className="flex items-center gap-2"><Route className="size-4 text-incendium-300" aria-hidden /> Firebreak lines between roads and villages and the deeper forest</li>
            <li className="flex items-center gap-2"><BellRing className="size-4 text-incendium-300" aria-hidden /> Alerts with nearest fire stations; you choose who is notified</li>
          </ul>
          <div className="mt-7 flex flex-wrap gap-3">
            <Link to="/dashboard" className="inline-flex items-center gap-2 rounded-2xl bg-ivory px-5 py-3 text-[15px] font-semibold text-graphite-900 hover:bg-stone-200">
              Open the dashboard <ArrowRight className="size-4" aria-hidden />
            </Link>
            <Link to="/alerts" className="inline-flex items-center gap-2 rounded-2xl px-4 py-3 text-sm font-medium text-ivory/80 hover:bg-ivory/8 hover:text-ivory">
              View alerts
            </Link>
          </div>
        </Chapter>

        <footer className="mx-auto max-w-[1400px] px-5 pb-10 pt-24 text-[11.5px] leading-relaxed text-ivory/45 md:px-10">
          Detections: NASA FIRMS (VIIRS NOAA-20/21 NRT). Weather data by Open-Meteo.com (CC BY 4.0). Contains modified Copernicus Sentinel data. Globe imagery: NASA Blue Marble (public domain).
          Basemap © OpenFreeMap © OpenMapTiles, data © OpenStreetMap contributors. Fire stations, settlements and roads © OpenStreetMap contributors (ODbL). Terrain © Mapterhorn. Research prototype; not an official warning service.
        </footer>
      </div>
    </div>
  )
}

function Chapter({
  index,
  refFn,
  icon,
  kicker,
  title,
  body,
  children,
}: {
  index: number
  refFn: (el: HTMLElement | null) => void
  icon: React.ReactNode
  kicker: string
  title: string
  body: string
  children?: React.ReactNode
}) {
  return (
    <section ref={refFn} data-section={index} className="mx-auto flex min-h-[90dvh] max-w-[1400px] items-center px-5 md:px-10">
      <motion.div
        initial={{ opacity: 0, y: 28 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ amount: 0.4, once: false }}
        transition={{ duration: MOTION.slow, ease: MOTION.ease }}
        className="panel-dark max-w-[560px] p-6 md:p-8"
      >
        <div className="eyebrow flex items-center gap-2 text-incendium-300">{icon}{kicker}</div>
        <h2 className="font-display mt-3 text-[clamp(24px,2.8vw,36px)] font-semibold leading-[1.08]">{title}</h2>
        <p className="mt-4 text-[14.5px] leading-relaxed text-ivory/70">{body}</p>
        {children}
      </motion.div>
    </section>
  )
}
