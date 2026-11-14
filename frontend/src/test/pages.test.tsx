import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { invalidate } from '../lib/api'
import Explorer from '../pages/Explorer'
import Methodology from '../pages/Methodology'

// MapLibre needs WebGL, which jsdom lacks: replace the map with a stub.
vi.mock('../components/MapView', () => ({ default: () => <div data-testid="map-stub" /> }))

// Synthetic fixtures for tests only.
const hotspots = {
  type: 'FeatureCollection',
  features: [
    { type: 'Feature', geometry: { type: 'Point', coordinates: [76.5, 14.1] }, properties: { id: 'a', satellite: 'NOAA-20', instrument: 'VIIRS', source: 'VIIRS_NOAA20_NRT', acq_datetime: '2026-11-13T09:30:00Z', confidence: 'high', frp: 12.5, bright_ti4: 330, bright_ti5: 295, scan: 0.4, track: 0.4, daynight: 'D', version: '2.0NRT', state: 'Karnataka', cell_id: null, retrieved_at: '2026-11-13T10:00:00Z' } },
    { type: 'Feature', geometry: { type: 'Point', coordinates: [77.2, 28.6] }, properties: { id: 'b', satellite: 'NOAA-21', instrument: 'VIIRS', source: 'VIIRS_NOAA21_NRT', acq_datetime: '2026-11-13T08:00:00Z', confidence: 'nominal', frp: 2.1, bright_ti4: 320, bright_ti5: 290, scan: 0.4, track: 0.4, daynight: 'D', version: '2.0NRT', state: 'Delhi', cell_id: null, retrieved_at: '2026-11-13T10:00:00Z' } },
  ],
  meta: { count: 2, truncated: false, since: '2026-11-11T10:00:00Z', until: '2026-11-13T10:00:00Z', provider: 'NASA FIRMS', products: [], detection_window: { id: 'india', name: '', bbox: [68, 6, 97.5, 37.5], note: '' }, provider_status: { id: 'firms', label: '', state: 'fresh', running: false, last_success: '2026-11-13T10:00:00Z', last_attempt: null, last_status: 'ok', last_message: null, age_minutes: 1, expected_interval_minutes: 30 }, semantics: '' },
}

let calls: string[] = []
beforeEach(() => {
  invalidate()
  calls = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    calls.push(url)
    let body: unknown = {}
    let status = 200
    if (url.startsWith('/api/hotspots')) body = hotspots
    else if (url.startsWith('/api/regions')) body = { regions: [], detection_window: hotspots.meta.detection_window }
    else if (url.startsWith('/api/summary')) body = { detections: { by_state_24h: [{ state: 'Karnataka', n: 1 }] } }
    else if (url.startsWith('/api/providers/status')) body = { providers: [] }
    else if (url.startsWith('/api/methodology')) {
      status = 503
      body = { error: 'unavailable', message: 'Methodology service down.' }
    }
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  })
})
afterEach(() => vi.restoreAllMocks())

describe('Explorer page', () => {
  it('lists detections, sorts by FRP and re-queries on filter change', async () => {
    render(<MemoryRouter><Explorer /></MemoryRouter>)
    expect(await screen.findByText('14.1000, 76.5000')).toBeInTheDocument()
    const rowsBefore = screen.getAllByRole('row').slice(1).map((r) => r.textContent)
    expect(rowsBefore[0]).toContain('NOAA-20')
    await userEvent.click(screen.getByRole('button', { name: /sort: newest/i }))
    expect(screen.getByRole('button', { name: /sort: highest frp/i })).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Confidence'), 'high')
    expect(calls.some((c) => c.includes('min_confidence=high'))).toBe(true)
  })
  it('falls back honestly when WebGL is unavailable (jsdom has none)', async () => {
    render(<MemoryRouter><Explorer /></MemoryRouter>)
    expect(await screen.findByText(/Map unavailable \(WebGL not supported\)\. The table remains fully usable\./)).toBeInTheDocument()
    expect(screen.queryByTestId('map-stub')).not.toBeInTheDocument()
  })
  it('is keyboard operable: rows are focusable', async () => {
    render(<MemoryRouter><Explorer /></MemoryRouter>)
    const cell = await screen.findByText('14.1000, 76.5000')
    const row = cell.closest('tr')!
    expect(row).toHaveAttribute('tabindex', '0')
  })
})

describe('Methodology page', () => {
  it('shows an honest error state when the API fails', async () => {
    render(
      <MemoryRouter initialEntries={['/methodology']}>
        <Routes><Route path="/methodology" element={<Methodology />} /></Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByText('Methodology service down. No substitute values are shown.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/what it cannot tell you/)
  })
})
