import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FactorBar } from '../components/charts'
import { DetectionPanel, Legend } from '../components/panels'
import { SourceStatus } from '../components/SourceStatus'
import { ClassBadge, ErrorState } from '../components/ui'
import { ApiError, invalidate } from '../lib/api'
import type { DetectionProps, Factor, ProviderStatus } from '../lib/types'

// Test fixtures below are synthetic and exist only for these tests.
const provider = (over: Partial<ProviderStatus>): ProviderStatus => ({
  id: 'firms',
  label: 'NASA FIRMS',
  state: 'fresh',
  running: false,
  last_success: new Date().toISOString(),
  last_attempt: new Date().toISOString(),
  last_status: 'ok',
  last_message: null,
  age_minutes: 3,
  expected_interval_minutes: 30,
  ...over,
})

function mockFetch(handler: (url: string) => { status: number; body: unknown }) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const { status, body } = handler(String(input))
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  })
}

beforeEach(() => invalidate())
afterEach(() => vi.restoreAllMocks())

describe('ClassBadge', () => {
  it('never relies on colour alone: label text and level are exposed', () => {
    render(<ClassBadge name="Very High" />)
    expect(screen.getByText('Very High')).toBeInTheDocument()
    expect(screen.getByLabelText('FWI class: Very High, level 4 of 6')).toBeInTheDocument()
  })
  it('shows "No data" for missing classes', () => {
    render(<ClassBadge name={null} kind="composite" />)
    expect(screen.getByText('No data')).toBeInTheDocument()
  })
})

describe('ErrorState', () => {
  it('states that no substitute values are shown and offers retry', async () => {
    const onRetry = vi.fn()
    render(<ErrorState error={new ApiError(503, 'x', 'Upstream down.')} onRetry={onRetry} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Upstream down. No substitute values are shown.')
    await userEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(onRetry).toHaveBeenCalled()
  })
})

describe('SourceStatus', () => {
  it('shows provider freshness and stale states', async () => {
    mockFetch(() => ({
      status: 200,
      body: { providers: [provider({}), provider({ id: 'cdse', label: 'Copernicus', state: 'stale', last_message: 'HTTP 429' })] },
    }))
    render(<SourceStatus />)
    const btn = await screen.findByRole('button', { name: /data sources/i })
    await waitFor(() => expect(btn).toHaveTextContent('Stale'))
    await userEvent.click(btn)
    expect(await screen.findByRole('dialog', { name: /data source status/i })).toHaveTextContent('Copernicus')
    expect(screen.getByText('HTTP 429')).toBeInTheDocument()
  })
  it('reports an unreachable backend instead of inventing status', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    render(<SourceStatus />)
    expect(await screen.findByText('Backend unreachable')).toBeInTheDocument()
  })
})

describe('FactorBar', () => {
  const factors: Factor[] = [
    { key: 'T', label: 'Temperature', input: '', unit: '°C', value: 35, score: 75, weight: 0.25, contribution: 18.8, rule: '', note: null },
    { key: 'H', label: 'Low humidity', input: '', unit: '%', value: 30, score: 76.9, weight: 0.25, contribution: 19.2, rule: '', note: null },
    { key: 'W', label: 'Wind', input: '', unit: 'km/h', value: 12, score: 20, weight: 0.2, contribution: 4, rule: '', note: null },
    { key: 'D', label: 'Dryness', input: '', unit: 'DC', value: 300, score: 50, weight: 0.15, contribution: 7.5, rule: '', note: null },
    { key: 'V', label: 'Vegetation moisture', input: '', unit: 'NDMI', value: 0.1, score: 60, weight: 0.15, contribution: 9, rule: '', note: null },
  ]
  it('describes every contribution textually', () => {
    render(<FactorBar factors={factors} score={58.5} />)
    expect(screen.getByRole('img')).toHaveAccessibleName(/Composite 58.5: Temperature 18.8, Low humidity 19.2, Wind 4, Dryness 7.5, Vegetation moisture 9/)
  })
})

describe('DetectionPanel', () => {
  const d: DetectionProps & { lon: number; lat: number } = {
    id: 'NOAA-20|20261113T0930|14.1000|76.5000',
    satellite: 'NOAA-20',
    instrument: 'VIIRS',
    source: 'VIIRS_NOAA20_NRT',
    acq_datetime: '2026-11-13T09:30:00Z',
    confidence: 'nominal',
    frp: 4.2,
    bright_ti4: 330.1,
    bright_ti5: 295.2,
    scan: 0.39,
    track: 0.36,
    daynight: 'D',
    version: '2.0NRT',
    state: 'Karnataka',
    cell_id: 'karnataka:14.125,76.625',
    retrieved_at: new Date().toISOString(),
    lon: 76.5,
    lat: 14.1,
  }
  it('labels a detection as a thermal anomaly, not a confirmed fire, with UTC and IST', async () => {
    const onInspect = vi.fn()
    render(<MemoryRouter><DetectionPanel d={d} onClose={() => undefined} onInspectCell={onInspect} /></MemoryRouter>)
    expect(screen.getByText(/Not a confirmed wildfire/)).toBeInTheDocument()
    expect(screen.getByText('2026-11-13 09:30 UTC')).toBeInTheDocument()
    expect(screen.getByText(/15:00 IST/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /inspect conditions/i }))
    expect(onInspect).toHaveBeenCalledWith('karnataka:14.125,76.625')
  })
})

describe('Legend', () => {
  it('labels FWI classes as prototype and uncalibrated', () => {
    render(<Legend metric="fwi" showDetections />)
    expect(screen.getByText(/prototype classes/i)).toBeInTheDocument()
    expect(screen.getByText(/not calibrated for India/)).toBeInTheDocument()
    expect(screen.getByText(/Satellite thermal anomaly/)).toBeInTheDocument()
  })
  it('explains NDMI is a proxy', () => {
    render(<Legend metric="ndmi" showDetections={false} />)
    expect(screen.getByText(/A proxy, not fuel moisture/)).toBeInTheDocument()
  })
})
