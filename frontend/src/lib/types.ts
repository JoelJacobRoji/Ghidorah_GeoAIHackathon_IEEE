// Typed shapes of the Incendium backend API.

export type ProviderState = 'fresh' | 'partial' | 'degraded' | 'stale' | 'unavailable' | 'never_run'

export interface ProviderStatus {
  id: 'firms' | 'open-meteo' | 'cdse' | 'risk-engine'
  label: string
  state: ProviderState
  running: boolean
  last_success: string | null
  last_attempt: string | null
  last_status: string | null
  last_message: string | null
  age_minutes: number | null
  expected_interval_minutes: number
}

export interface DetectionProps {
  id: string
  satellite: string
  instrument: string
  source: string
  acq_datetime: string
  confidence: 'low' | 'nominal' | 'high' | null
  frp: number | null
  bright_ti4: number | null
  bright_ti5: number | null
  scan: number | null
  track: number | null
  daynight: string | null
  version: string | null
  state: string | null
  cell_id: string | null
  retrieved_at: string
}

export interface PointFeature<P> {
  type: 'Feature'
  id?: string
  geometry: { type: 'Point'; coordinates: [number, number] }
  properties: P
}

export interface PolygonFeature<P> {
  type: 'Feature'
  id?: string
  geometry: { type: 'Polygon'; coordinates: number[][][] }
  properties: P
}

export interface HotspotsResponse {
  type: 'FeatureCollection'
  features: PointFeature<DetectionProps>[]
  meta: {
    count: number
    truncated: boolean
    since: string
    until: string
    provider: string
    products: string[]
    detection_window: { id: string; name: string; bbox: [number, number, number, number]; note: string }
    provider_status: ProviderStatus
    semantics: string
  }
}

export type FwiClass = 'Low' | 'Moderate' | 'High' | 'Very High' | 'Extreme' | 'Very Extreme'
export type CompositeClass = 'Low' | 'Moderate' | 'High' | 'Very High'

export interface RiskCellProps {
  cell_id: string
  lat: number
  lon: number
  overlap: number
  fwi: number | null
  fwi_class: FwiClass | null
  ffmc: number | null
  dmc: number | null
  dc: number | null
  isi: number | null
  bui: number | null
  fwi_status: string
  composite: number | null
  composite_class: CompositeClass | null
  completeness: number
  missing: string[]
  ndvi: number | null
  ndmi: number | null
  t_max: number | null
  rh_min: number | null
  ws_max: number | null
  ws_noon: number | null
  wd_noon: number | null
  rain24: number | null
  detections_7d: number
  ml_probability?: number | null
  ml_in_season?: boolean | null
  is_forecast: boolean
}

export interface AvailableDate {
  date: string
  is_forecast: boolean
}

export interface RiskMapResponse {
  type: 'FeatureCollection'
  features: PolygonFeature<RiskCellProps>[]
  meta: {
    region: string
    date: string
    is_forecast: boolean
    available_dates: AvailableDate[]
    computed_at: string | null
    grid_deg: number
    providers: ProviderStatus[]
    semantics: string
  }
}

export interface Factor {
  key: 'T' | 'H' | 'W' | 'D' | 'V'
  label: string
  input: string
  unit: string
  value: number | null
  score: number | null
  weight: number
  contribution: number | null
  rule: string
  note: string | null
}

export interface Composite {
  score: number | null
  class: CompositeClass | null
  factors: Factor[]
  missing: string[]
  completeness: number
  status: 'ok' | 'insufficient_data'
}

export interface FwiRow {
  date: string
  ffmc: number | null
  dmc: number | null
  dc: number | null
  isi: number | null
  bui: number | null
  fwi: number | null
  fwi_class: FwiClass | null
  is_forecast: number
  status: string
  dsr?: number | null
  computed_at?: string
}

export interface WeatherDay {
  date: string
  t_noon: number | null
  rh_noon: number | null
  ws_noon: number | null
  rain24_noon: number | null
  t_max: number | null
  rh_min: number | null
  ws_max: number | null
  gust_max: number | null
  vpd_max: number | null
  precip_day: number | null
  is_forecast: number
  retrieved_at?: string
  wd_noon?: number | null
}

export interface VegRow {
  cell_id: string
  interval_from: string
  interval_to: string
  ndvi_mean: number | null
  ndvi_std: number | null
  ndmi_mean: number | null
  ndmi_std: number | null
  valid_fraction: number | null
  sample_count: number | null
  retrieved_at: string
}

export interface NearbyDetection extends DetectionProps {
  latitude: number
  longitude: number
  distance_km: number
}

export interface RiskDetails {
  cell: { id: string; region: string; lat: number; lon: number; west: number; south: number; east: number; north: number; overlap: number }
  date: string | null
  fwi: FwiRow | null
  weather: WeatherDay | null
  composite: Composite | null
  percentiles: Record<string, number> | null
  vegetation: { interval_from: string; interval_to: string; ndvi: number; ndmi: number; valid_fraction: number; retrieved_at: string } | null
  explanation: string[]
  weights: Record<string, number>
  fwi_series: FwiRow[]
  weather_series: WeatherDay[]
  vegetation_series: VegRow[]
  nearby_detections: NearbyDetection[]
  provenance: {
    weather: { provider: string; model_grid_point: { lat: number; lon: number; elevation_m: number } | null; retrieved_at: string | null }
    fwi: { computed_at: string | null }
    vegetation: { provider: string; retrieved_at: string | null }
    detections: { provider: string; radius_km: number; window_days: number }
    risk_computed_at: string | null
  }
}

export interface Stats {
  min: number
  median: number
  mean: number
  max: number
  n: number
}

export interface RegionSummary {
  region: string
  date: string
  cells: number
  is_forecast: boolean
  fwi: { class_counts: Record<string, number>; stats: Stats | null }
  composite: { class_counts: Record<string, number>; stats: Stats | null; mean_contributions: Record<string, number> }
  weather: { t_max: Stats | null; rh_min: Stats | null; ws_max: Stats | null; rain24: Stats | null }
  detections: { last_24h: number; last_7d: number }
  top_cells: Array<{ id: string; lat: number; lon: number; fwi: number; fwi_class: FwiClass; ffmc: number; isi: number; bui: number; dc: number; t_max: number; rh_min: number; ws_max: number; rain24_noon: number }>
  available_dates: AvailableDate[]
  providers: ProviderStatus[]
}

export interface Region {
  id: string
  name: string
  state_code: string
  grid_deg: number
  center: [number, number]
  zoom: number
  enabled: boolean
  cells: number
  outline: { type: 'Polygon' | 'MultiPolygon'; coordinates: unknown }
}

export interface RegionsResponse {
  regions: Region[]
  detection_window: { id: string; name: string; bbox: [number, number, number, number]; note: string }
}

export interface WeatherCell {
  cell_id: string
  lat: number
  lon: number
  time_local: string
  t2m: number | null
  rh2m: number | null
  ws10: number | null
  wd10: number | null
  gust10: number | null
  precip: number | null
  vpd: number | null
  is_forecast: boolean
  retrieved_at: string
}

export interface WeatherResponse {
  region: string
  timezone: string
  target_time_local: string
  cells: WeatherCell[]
  units: Record<string, string>
  semantics: string
  provider_status: ProviderStatus
}

export interface SummaryResponse {
  detections: {
    last_24h: number
    last_7d: number
    latest_acquisition: string | null
    earliest_stored: string | null
    by_state_24h: { state: string; n: number }[]
    by_satellite_24h: { satellite: string; n: number }[]
    by_confidence_24h: { confidence: string; n: number }[]
    daily_utc: { day: string; n: number }[]
  }
  detection_window: { id: string; name: string; bbox: [number, number, number, number]; note: string }
  providers: ProviderStatus[]
  generated_at: string
}

// Methodology is rendered generically.
export type Methodology = Record<string, any> // eslint-disable-line @typescript-eslint/no-explicit-any

// --------------------------------------------------------------------------- operations modules
export interface StationRef {
  id: string
  name: string | null
  lat: number
  lon: number
  phone?: string | null
  operator?: string | null
  distance_km?: number
  osm_url?: string
  subtype?: string | null
}

export interface SettlementRef extends StationRef {
  arrival_hours?: number
}

export interface SpreadHourWeather {
  time_local: string
  ffmc: number
  isi: number
  wind_kmh: number
  wind_from_deg: number
  temp_c: number
  rh_pct: number
  head_ros_m_min: Record<'grass' | 'shrub' | 'forest', number>
  lb: number
}

export interface SpreadResult {
  type: 'FeatureCollection'
  features: GeoJSON.Feature<GeoJSON.Polygon | GeoJSON.MultiPolygon, { hour: number; area_ha: number; time_local: string }>[]
  meta: {
    ignition: { lon: number; lat: number; fuel: string; snapped_to: [number, number] }
    start_local: string
    hours: number
    cell_id: string
    grid: { resolution_m: number; radius_m: number; cells: number; bbox: [number, number, number, number] }
    final_area_ha: number
    hourly: { hour: number; time_local: string; area_ha: number }[]
    weather_by_hour: SpreadHourWeather[]
    inputs: { curing_pct: number; ffmc_prev_day: number; bui: number | null }
    fuel: { summary: Record<string, number>; period: [string, string]; retrieved_at: string; source: string }
    terrain: { source: string; elev_min: number; elev_max: number }
    settlements_reached: SettlementRef[]
    fire_stations: StationRef[]
    assumptions: string[]
    generated_at: string
  }
}

export interface CorridorProps {
  rank: number
  score: number
  length_m: number
  anchor: 'road' | 'waterway'
  osm_class: string
  name: string | null
  osm_id: string
  upwind_hazard: number
  assets: number
  orientation: number
  access: number
  terrain: number
  mean_slope_deg: number
  burnable_share: number
  settlements_protected: { name: string | null; subtype: string | null }[]
}

export interface FirebreakResult {
  type: 'FeatureCollection'
  features: GeoJSON.Feature<GeoJSON.LineString, CorridorProps>[]
  meta: {
    center: [number, number]
    radius_km: number
    cell_id: string
    wind: { from_deg: number; p90_kmh: number; mean_kmh: number; steadiness: number; hours: number; period_start: string }
    dry_conditions: { ffmc_7d_max: number; bui_7d_max: number | null; isi: number; head_ros_m_min: Record<string, number> }
    hazard_image: { url: string; bounds: [number, number, number, number] }
    candidates_scored: number
    settlements_considered: number
    fuel: { summary: Record<string, number>; period: [string, string]; retrieved_at: string }
    osm_retrieved_at: string
    method: string
    assumptions: string[]
    generated_at: string
  }
}

export type AlertPriority = 'low' | 'medium' | 'high'

export interface AlertItem {
  id: string
  created_at: string
  kind: 'detection' | 'outlook'
  priority: AlertPriority
  title: string
  message: string
  lat: number | null
  lon: number | null
  state: string | null
  cell_id: string | null
  acknowledged_at: string | null
  notified_at: string | null
  notify_status: string | null
  rules: { id: string; text: string }[]
  detection_count?: number
  frp_total_mw?: number
  first_acq?: string
  fwi_class?: string | null
  persistent_source?: boolean
  persistent_days?: number
  nearest_settlement?: StationRef | null
  fire_stations?: StationRef[]
  cells?: string[]
  dates?: string[]
}

export interface AlertsResponse {
  alerts: AlertItem[]
  /** Number of alerts matching the filters (alerts holds at most `limit` of them). */
  total?: number
  summary: Partial<Record<AlertPriority, { total: number; open: number }>>
  channels: { email: boolean; webhook: boolean; min_priority: AlertPriority; automatic_authority_contact: false }
  rules: string
  disclaimer: string
  last_evaluation: { finished_at: string; status: string } | null
}
