import { AnimatePresence, motion, MotionConfig } from 'motion/react'
import { Component, lazy, Suspense, type ReactNode } from 'react'
import { BrowserRouter, Link, Route, Routes, useLocation } from 'react-router-dom'
import { MOTION } from './lib/motion'

const Landing = lazy(() => import('./pages/Landing'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const RegionAnalysis = lazy(() => import('./pages/RegionAnalysis'))
const Explorer = lazy(() => import('./pages/Explorer'))
const Methodology = lazy(() => import('./pages/Methodology'))
const Alerts = lazy(() => import('./pages/Alerts'))

function PageFallback() {
  return (
    <div className="grid h-dvh place-items-center bg-graphite-800" role="status" aria-live="polite">
      <div className="eyebrow text-ivory/45">Loading Incendium…</div>
    </div>
  )
}

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) {
    return { error }
  }
  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="grid h-dvh place-items-center bg-graphite-800 p-6 text-ivory" role="alert">
        <div className="max-w-md">
          <div className="font-display text-2xl">Something went wrong on this page.</div>
          <p className="mt-2 text-sm text-ivory/65">No data was altered. Reload the page, or return to the start.</p>
          <a href="/" className="mt-4 inline-block rounded-xl bg-ivory px-4 py-2 text-sm font-medium text-graphite-900">Back to Incendium</a>
        </div>
      </div>
    )
  }
}

function NotFound() {
  return (
    <div className="grid h-dvh place-items-center bg-graphite-800 text-ivory">
      <div className="text-center">
        <div className="font-display text-3xl">Page not found</div>
        <Link to="/" className="mt-3 inline-block text-incendium-300 underline">Return home</Link>
      </div>
    </div>
  )
}

function AnimatedRoutes() {
  const location = useLocation()
  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={location.pathname}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1, transition: { duration: MOTION.base, ease: MOTION.ease } }}
        exit={{ opacity: 0, transition: { duration: MOTION.fast } }}
        className="min-h-dvh"
      >
        <Suspense fallback={<PageFallback />}>
          <Routes location={location}>
            <Route path="/" element={<Landing />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/analysis" element={<RegionAnalysis />} />
            <Route path="/detections" element={<Explorer />} />
            <Route path="/methodology" element={<Methodology />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </Suspense>
      </motion.div>
    </AnimatePresence>
  )
}

export default function App() {
  return (
    <MotionConfig reducedMotion="user">
      <ErrorBoundary>
        <BrowserRouter>
          <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[100] focus:rounded-lg focus:bg-ivory focus:px-3 focus:py-2 focus:text-graphite-900">
            Skip to content
          </a>
          <div id="main">
            <AnimatedRoutes />
          </div>
        </BrowserRouter>
      </ErrorBoundary>
    </MotionConfig>
  )
}
