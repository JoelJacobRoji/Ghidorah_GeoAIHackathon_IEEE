import { useSyncExternalStore } from 'react'

/** Incendium motion system. Every animation draws its timing from here. */
export const MOTION = {
  fast: 0.18,
  base: 0.32,
  slow: 0.6,
  stagger: 0.04,
  camera: { short: 1.2, long: 2.4 },
  ease: [0.16, 1, 0.3, 1] as [number, number, number, number], // out-expo
  easeInOut: [0.76, 0, 0.24, 1] as [number, number, number, number],
  gsapEase: 'expo.out',
  gsapEaseInOut: 'power3.inOut',
}

const query = '(prefers-reduced-motion: reduce)'

function subscribe(cb: () => void) {
  if (typeof window === 'undefined' || !window.matchMedia) return () => undefined
  const mq = window.matchMedia(query)
  mq.addEventListener('change', cb)
  return () => mq.removeEventListener('change', cb)
}

export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia(query).matches
}

export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, prefersReducedMotion, () => false)
}

export const fadeUp = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { duration: MOTION.slow, ease: MOTION.ease } },
}

export const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: MOTION.stagger * 2 } },
}

/** Detect usable WebGL once (used for honest fallbacks). */
let webglCache: boolean | null = null
export function hasWebGL(): boolean {
  if (webglCache !== null) return webglCache
  try {
    const c = document.createElement('canvas')
    webglCache = !!(c.getContext('webgl2') || c.getContext('webgl'))
  } catch {
    webglCache = false
  }
  return webglCache
}
