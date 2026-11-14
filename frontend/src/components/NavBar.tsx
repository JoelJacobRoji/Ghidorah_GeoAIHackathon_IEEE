import { Menu, X } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { useApi } from '../lib/api'
import { MOTION } from '../lib/motion'
import type { AlertsResponse } from '../lib/types'

export const NAV = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/analysis', label: 'Regional analysis' },
  { to: '/detections', label: 'Detections' },
  { to: '/alerts', label: 'Alerts' },
  { to: '/methodology', label: 'Methodology' },
]

export function Logo({ className = '' }: { className?: string }) {
  return (
    <NavLink to="/" className={`flex items-center gap-2 ${className}`} aria-label="Incendium home">
      <svg viewBox="0 0 64 64" className="size-6" aria-hidden>
        <circle cx="32" cy="32" r="17" fill="none" stroke="currentColor" strokeOpacity="0.55" strokeWidth="2.5" />
        <path d="M32 16c5 8 11 12 11 20a11 11 0 0 1-22 0c0-5 3-8 5-11 1 4 3 6 5 6-1-5 0-10 1-15z" fill="#e77943" />
      </svg>
      <span className="font-display text-[15px] font-bold tracking-[0.16em]">INCENDIUM</span>
    </NavLink>
  )
}

/** Open medium/high-priority alerts in the last 24 h (polled once a minute, shared cache). */
function AlertsBadge() {
  const { data } = useApi<AlertsResponse>('/api/alerts?hours=24&min_priority=medium&include_acknowledged=false&limit=1', { refreshMs: 60_000 })
  const n = (data?.summary?.high?.open ?? 0) + (data?.summary?.medium?.open ?? 0)
  if (!n) return null
  return (
    <span className="ml-1.5 inline-grid min-w-[18px] place-items-center rounded-full bg-[#a86400] px-1 text-[10.5px] font-bold leading-[18px] text-white" aria-label={`${n} open medium or high priority alerts`}>
      {n > 99 ? '99+' : n}
    </span>
  )
}

export function NavBar({ variant = 'solid', right }: { variant?: 'solid' | 'overlay'; right?: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  const base = variant === 'overlay' ? 'panel-dark' : 'bg-graphite-800/95 border-b border-ivory/8'
  return (
    <header className={`${variant === 'overlay' ? 'pointer-events-auto rounded-2xl' : 'sticky top-0 z-40 backdrop-blur'} ${base}`}>
      <div className={`flex items-center gap-4 ${variant === 'overlay' ? 'px-3 py-2' : 'mx-auto max-w-[1400px] px-4 py-3 md:px-6'}`}>
        <Logo className="text-ivory" />
        <nav className="hidden items-center gap-1 md:flex" aria-label="Primary">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) =>
                `rounded-lg px-2.5 py-1.5 text-[13px] font-medium transition-colors ${isActive ? 'bg-ivory/12 text-ivory' : 'text-ivory/65 hover:bg-ivory/8 hover:text-ivory'}`
              }
            >
              {n.label}
              {n.to === '/alerts' && <AlertsBadge />}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          {right}
          <button type="button" className="rounded-lg p-1.5 text-ivory/80 hover:bg-ivory/10 md:hidden" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label="Menu">
            {open ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
        </div>
      </div>
      <AnimatePresence>
        {open && (
          <motion.nav
            aria-label="Primary mobile"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: MOTION.base, ease: MOTION.ease }}
            className="overflow-hidden md:hidden"
          >
            <div className="flex flex-col gap-1 px-3 pb-3">
              {NAV.map((n) => (
                <NavLink key={n.to} to={n.to} onClick={() => setOpen(false)} className={({ isActive }) => `rounded-lg px-3 py-2 text-sm ${isActive ? 'bg-ivory/12 text-ivory' : 'text-ivory/75'}`}>
                  {n.label}
                  {n.to === '/alerts' && <AlertsBadge />}
                </NavLink>
              ))}
            </div>
          </motion.nav>
        )}
      </AnimatePresence>
    </header>
  )
}
