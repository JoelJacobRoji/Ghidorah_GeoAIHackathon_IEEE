import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MlReport } from '../components/MlReport'
import { FirebreakTool } from '../components/tools/FirebreakTool'
import { SpreadTool } from '../components/tools/SpreadTool'
import { invalidate } from '../lib/api'
import { circlePolygon } from '../lib/overlays'
import type { AlertsResponse, FirebreakResult, SpreadResult } from '../lib/types'
import Alerts from '../pages/Alerts'

// Synthetic fixtures for tests only.
const spread: SpreadResult = {
  type: 'FeatureCollection',
  features: [{ type: 'Feature', geometry: { type: 'Polygon', coordinates: [[[76, 13], [76.01, 13], [76.01, 13.01], [76, 13]]] }, properties: { hour: 1, area_ha: 4.2, time_local: '2026-03-10T12:00' } }],
  meta: {
    ignition: { lon: 76, lat: 13, fuel: 'grass', snapped_to: [76, 13] }, start_local: '2026-03-10T12:00', hours: 2, cell_id: 'karnataka:12.875,75.875',
    grid: { resolution_m: 50, radius_m: 5000, cells: 40000, bbox: [75.9, 12.9, 76.1, 13.1] }, final_area_ha: 12.5,
    hourly: [{ hour: 1, time_local: '2026-03-10T12:00', area_ha: 4.2 }, { hour: 2, time_local: '2026-03-10T13:00', area_ha: 12.5 }],
    weather_by_hour: [{ time_local: '2026-03-10T12:00', ffmc: 91, isi: 9, wind_kmh: 15, wind_from_deg: 270, temp_c: 33, rh_pct: 22, head_ros_m_min: { grass: 18, shrub: 10, forest: 2 }, lb: 1.6 }],
    inputs: { curing_pct: 85, ffmc_prev_day: 89, bui: 40 },
    fuel: { summary: { grass: 0.6 }, period: ['2026-01-25', '2026-03-10'], retrieved_at: 'x', source: 'Copernicus Sentinel-2 L2A median NDVI (Process API)' },
    terrain: { source: 'Mapterhorn Terrarium DEM', elev_min: 600, elev_max: 700 },
    settlements_reached: [{ id: 'node/1', name: 'Hosahalli', lat: 13, lon: 76.02, subtype: 'village', arrival_hours: 1.5 }],
    fire_stations: [{ id: 'way/2', name: 'Town Fire Station', lat: 13.1, lon: 76.1, distance_km: 14.2, phone: '101', osm_url: 'https://www.openstreetmap.org/way/2' }],
    assumptions: ['Illustrative scenario, not a forecast.'], generated_at: 'x',
  },
}

const firebreaks: FirebreakResult = {
  type: 'FeatureCollection',
  features: [{ type: 'Feature', geometry: { type: 'LineString', coordinates: [[76, 13], [76.01, 13.01]] }, properties: {
    rank: 1, score: 0.2, length_m: 2400, forest_intensity_kw_m: 4800, intensity_class: 'very high', hazard: 1, wind_into_forest: 0.9, past_fires: 12, human_pressure: 0.7,
    forest_protected_km2: 14.2, terrain: 0.8, mean_slope_deg: 7, settlements_near: [{ name: 'Hosahalli', subtype: 'village' }], roads_near: ['SH 33'],
    anchor: { osm_id: 'way/9', class: 'track', name: 'Forest track', share: 0.6 } } }],
  meta: {
    center: [76, 13], radius_km: 6, cell_id: 'c',
    design: { seasons: 'Feb–May 2025 and 2026', wind_from_deg: 270, steadiness: 0.8, ws_p90_kmh: 18, ffmc_p90: 93, bui_p90: 110, isi: 14, intensity_kw_m: { grass: 3700, shrub: 4400, forest: 5100 } },
    human_zone: { type: 'Polygon', coordinates: [[[76, 13], [76.01, 13], [76.01, 13.01], [76, 13]]] },
    hazard_image: { url: 'data:image/png;base64,', bounds: [75.9, 12.9, 76.1, 13.1] },
    edge_km_examined: 40, line_km_proposed: 2.4, settlements_considered: 40, roads_considered: 12, empty_reason: null,
    fire_history: { years: [2025, 2026], detections_in_window: 40, on_forest: 30, source: 'VIIRS' },
    parameters: { ignition_buffer_m: 500, min_forest_km2: 5, min_intensity_kw_m: 2000, min_line_m: 1000, min_past_fires: 3, fire_radius_m: 2000 },
    fuel: { summary: {}, period: ['a', 'b'], retrieved_at: 'x' }, osm_retrieved_at: '2026-03-10T00:00:00Z',
    method: 'score = ...', assumptions: ['Proposed lines for field assessment only.'], generated_at: 'x',
  },
}

const alerts: AlertsResponse = {
  alerts: [{
    id: 'det-1', created_at: '2026-03-10T08:30:00Z', kind: 'detection', priority: 'high', title: 'Thermal anomaly near Hosahalli', message: 'Full message text',
    lat: 13, lon: 76, state: 'Karnataka', cell_id: 'karnataka:12.875,75.875', acknowledged_at: null, notified_at: null, notify_status: null,
    rules: [{ id: 'R1', text: '3 detection(s) 1.2 km from Hosahalli (village)' }],
    fire_stations: [{ id: 'way/2', name: 'Town Fire Station', lat: 13.1, lon: 76.1, distance_km: 14.2, phone: '101', osm_url: 'https://www.openstreetmap.org/way/2' }],
  }],
  summary: { high: { total: 1, open: 1 } },
  channels: { email: false, webhook: false, min_priority: 'medium', automatic_authority_contact: false },
  rules: 'R1 near settlement ...', disclaimer: 'Not an official warning.', last_evaluation: null,
}

let posted: { url: string; body: unknown }[] = []
beforeEach(() => {
  invalidate()
  posted = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    let body: unknown = {}
    let status = 200
    if (init?.method === 'POST') posted.push({ url, body: init.body ? JSON.parse(String(init.body)) : undefined })
    if (url.startsWith('/api/simulate/spread')) body = spread
    else if (url.startsWith('/api/firebreaks')) body = firebreaks
    else if (url.startsWith('/api/alerts') && url.includes('/ack')) body = { status: 'ok' }
    else if (url.startsWith('/api/alerts')) body = alerts
    else if (url.startsWith('/api/ml/report')) { status = 404; body = { error: 'not_found', message: 'No model.' } }
    else if (url.startsWith('/api/providers/status')) body = { providers: [] }
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  })
})
afterEach(() => vi.restoreAllMocks())

describe('Spread simulation tool', () => {
  it('asks for a point first, then runs with the chosen parameters and shows results with assumptions', async () => {
    const onResult = vi.fn()
    const { rerender } = render(<SpreadTool point={null} result={null} onResult={onResult} onClose={() => {}} />)
    expect(screen.getByRole('button', { name: /run scenario/i })).toBeDisabled()
    rerender(<SpreadTool point={[76, 13]} result={null} onResult={onResult} onClose={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: /run scenario/i }))
    expect(posted[0]).toEqual({ url: '/api/simulate/spread', body: { lon: 76, lat: 13, hours: 6, curing_pct: 85 } })
    expect(onResult).toHaveBeenCalledWith(spread)
    rerender(<SpreadTool point={[76, 13]} result={spread} onResult={onResult} onClose={() => {}} />)
    expect(screen.getAllByText('13 ha').length).toBe(2) // summary tile + last hourly bar
    expect(screen.getByText('Hosahalli')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Town Fire Station' })).toHaveAttribute('href', 'https://www.openstreetmap.org/way/2')
    expect(screen.getByText('Illustrative scenario, not a forecast.')).toBeInTheDocument()
  })
})

describe('Firebreak tool', () => {
  it('lists ranked forest-edge lines with their score components and nearby settlements', async () => {
    const onFocus = vi.fn()
    render(<FirebreakTool point={[76, 13]} radius={6} onRadius={() => {}} result={firebreaks} onResult={() => {}} showHazard onShowHazard={() => {}} onFocus={onFocus} onClose={() => {}} />)
    const item = screen.getByRole('button', { name: /line 1/i })
    expect(within(item).getByText(/Hosahalli/)).toBeInTheDocument()
    expect(within(item).getByText(/along Forest track/)).toBeInTheDocument()
    expect(within(item).getByText(/12 past forest-fire detections/)).toBeInTheDocument()
    expect(within(item).getByTitle('Wind into forest: 0.90')).toBeInTheDocument()
    await userEvent.click(item)
    expect(onFocus).toHaveBeenCalled()
    expect(screen.getByText('Proposed lines for field assessment only.')).toBeInTheDocument()
  })
})

describe('Alerts page', () => {
  it('shows priority with icon and label, nearest stations and no automatic authority contact', async () => {
    render(<MemoryRouter><Alerts /></MemoryRouter>)
    expect(await screen.findByText('Thermal anomaly near Hosahalli')).toBeInTheDocument()
    expect(screen.getAllByText('High priority').length).toBeGreaterThan(0)
    expect(screen.getByText(/Automatic contact with authorities: never/)).toBeInTheDocument()
    expect(screen.getByText('101')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /simulate spread/i })).toHaveAttribute('href', expect.stringContaining('tool=spread'))
    await userEvent.click(screen.getByRole('button', { name: /acknowledge/i }))
    expect(posted.some((p) => p.url === '/api/alerts/det-1/ack')).toBe(true)
  })
})

describe('ML report', () => {
  it('explains honestly when no model has been trained', async () => {
    render(<MlReport />)
    expect(await screen.findByText(/has not been trained yet/)).toBeInTheDocument()
  })
})

describe('overlay helpers', () => {
  it('builds a closed circle polygon of the requested radius', () => {
    const c = circlePolygon(76, 13, 10)
    const ring = c.geometry.coordinates[0]
    expect(ring[0]).toEqual(ring[ring.length - 1])
    const north = Math.max(...ring.map((p) => p[1]))
    expect((north - 13) * 110.54).toBeCloseTo(10, 1)
  })
})
